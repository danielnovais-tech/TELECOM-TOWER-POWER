# Failover runbook — `api.telecomtowerpower.com.br`

Verified topology as of 2026-05-01. Read [/memories/repo/prod-topology.md] equivalent
section in this file before acting.

## Normal state

```
api.telecomtowerpower.com.br
        │  (Route 53 ALIAS)
        ▼
  ALB telecom-tower-power-alb (sa-east-1)
        │  listener :443, rule priority 5 (host-header = api.*)
        ▼
  TG telecom-tower-power-api-tg
        │  forward to ECS Fargate task ENI :8000
        ▼
  ECS service telecom-tower-power (Fargate, 1 task, task def rev ≥48)
```

Other hosts (unchanged during a Fargate failover):

| Host                                  | Listener priority | Target group               |
|---------------------------------------|-------------------|----------------------------|
| `app/www/docs/telecomtowerpower.com.br` | 20                | `telecom-tower-power-ec2-tg` (EC2 :80, Caddy) |
| `monitoring.telecomtowerpower.com.br` | 10                | `ttp-grafana-tg` (EC2 :3001) |
| `prometheus.telecomtowerpower.com.br` | 11                | `ttp-prometheus-tg` (EC2 :9090) |
| default rule                          | —                 | `telecom-tower-power-api-tg` (Fargate) |

## Failure modes & recovery

### A. Fargate task unhealthy / crash-loop, image still good

Just force a new deployment — ECS pulls latest image from ECR.

```bash
aws --region sa-east-1 ecs update-service \
  --cluster telecom-tower-power \
  --service telecom-tower-power \
  --force-new-deployment

aws --region sa-east-1 ecs wait services-stable \
  --cluster telecom-tower-power --services telecom-tower-power
```

Validate:
```bash
curl -fsS https://api.telecomtowerpower.com.br/health
```

### B. Fargate broken (bad image, AZ outage) — failover to Railway

Railway holds a warm replica of the API at
`https://web-production-90b1f.up.railway.app`. Caddy on the EC2 already
proxies `api.*` to Railway when EC2 is the active backend
(see [Caddyfile L13-L33](../Caddyfile#L13-L33)).

Steps (≈ 60 seconds, no DNS change required):

```bash
# 1. Get current ARNs
LB_ARN=$(aws --region sa-east-1 elbv2 describe-load-balancers \
  --names telecom-tower-power-alb \
  --query 'LoadBalancers[0].LoadBalancerArn' --output text)
LIS_ARN=$(aws --region sa-east-1 elbv2 describe-listeners \
  --load-balancer-arn "$LB_ARN" \
  --query 'Listeners[?Port==`443`].ListenerArn' --output text)
EC2_TG=$(aws --region sa-east-1 elbv2 describe-target-groups \
  --names telecom-tower-power-ec2-tg \
  --query 'TargetGroups[0].TargetGroupArn' --output text)
API_RULE=$(aws --region sa-east-1 elbv2 describe-rules \
  --listener-arn "$LIS_ARN" \
  --query "Rules[?Conditions[0].Values[0]=='api.telecomtowerpower.com.br'].RuleArn | [0]" \
  --output text)

# 2. Point api.* at the EC2 target group
aws --region sa-east-1 elbv2 modify-rule \
  --rule-arn "$API_RULE" \
  --actions Type=forward,TargetGroupArn="$EC2_TG"

# 3. Validate (Caddy on EC2 will proxy to Railway via the @api_host block)
curl -fsS https://api.telecomtowerpower.com.br/health
```

Roll back when Fargate recovers:

```bash
API_TG=$(aws --region sa-east-1 elbv2 describe-target-groups \
  --names telecom-tower-power-api-tg \
  --query 'TargetGroups[0].TargetGroupArn' --output text)

aws --region sa-east-1 elbv2 modify-rule \
  --rule-arn "$API_RULE" \
  --actions Type=forward,TargetGroupArn="$API_TG"
```

### C. Fargate AND Railway broken — promote the EC2 compose `api` container

The EC2 docker compose stack runs a secondary `api` container
(see [docker-compose.yml](../docker-compose.yml)). It is not exposed
externally — Caddy currently proxies `api.*` to Railway. To use the local
`api` container instead:

```bash
# On the EC2 host (i-045166a6a1933f507, EIP 18.229.14.122)
ssh ec2-user@18.229.14.122    # or via SSM Session Manager
cd /opt/telecom-tower-power

# 1. Edit Caddyfile: change the @api_host handle block to point at the
#    local api container:
#
#        handle @api_host {
#                reverse_proxy api:8000
#        }
#
# 2. Reload Caddy without dropping connections
docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile

# 3. Verify
curl -fsS http://localhost/health -H 'Host: api.telecomtowerpower.com.br'
```

Then perform the ALB swing from section **B** (point `api.*` rule at
`telecom-tower-power-ec2-tg`).

To revert: restore the original `reverse_proxy https://web-production-...`
block in [Caddyfile](../Caddyfile) and `caddy reload`.

### D. EC2 host down

`api.*` is unaffected (continues on Fargate). Affected hosts:
`app.*`, `www.*`, `docs.*`, `monitoring.*`, `prometheus.*`.

Recovery: relaunch from AMI / restore the EBS snapshot per
[ec2-deploy-notes.md](../docs/RAILWAY.md) and reattach the EIP
`18.229.14.122`.

### E. ALB itself broken (rare, AWS-side)

DNS-level failover: change Route 53 record for
`api.telecomtowerpower.com.br` to a CNAME pointing at
`web-production-90b1f.up.railway.app` (Railway terminates TLS on its own
domain, so the cert chain still works for clients that don't pin).

```bash
# Replace HZID and pre-built JSON change set
aws route53 change-resource-record-sets \
  --hosted-zone-id <HZID> \
  --change-batch file://failover-railway.json
```

TTL on the A-ALIAS is 60 s. Allow ~2 min for full propagation.

## Pre-flight checklist (do once per quarter)

- [ ] Railway replica responds: `curl -fsS https://web-production-90b1f.up.railway.app/health`
- [ ] EC2 compose `api` healthy: `docker compose exec api curl -fsS http://localhost:8000/health`
- [ ] Backups within retention: see [/memories/repo/] equivalents and the
      backup workflow.
- [ ] AWS CLI on operator machine has `elasticloadbalancing:ModifyRule`
      permissions: `aws elbv2 describe-rules --listener-arn "$LIS_ARN" --output table`

## Bus-factor mitigations baked in

- All ARNs and host names above are reproducible via `describe-*` calls;
  no hidden state outside AWS + this repo.
- The compose stack can be recreated end-to-end from
  [docker-compose.yml](../docker-compose.yml) + `.env` + ECR pulls.
- The trained coverage model is a portable
  [`coverage_model.npz`](../coverage_model.npz) (numpy), not a hosted black box.
- API surface is documented in [`openapi.json`](../openapi.json) — clients
  can repoint at any backend by swapping the base URL.
