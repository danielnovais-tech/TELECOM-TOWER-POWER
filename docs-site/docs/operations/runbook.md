# Runbook

Operational procedures for the production EC2 environment.

## Quick reference

| Resource | Value |
|---|---|
| EC2 instance ID | `i-045166a6a1933f507` |
| Region | `sa-east-1` |
| SSH user | `ubuntu` |
| Project path on host | `/home/ubuntu/TELECOM-TOWER-POWER` |
| Secrets path on host | `/home/ubuntu/TELECOM-TOWER-POWER/secrets/` |
| Caddy entry point | `:80` (ALB terminates TLS) |
| Railway API (custom domain) | `https://api.telecomtowerpower.com.br` |
| Railway edge target | `web-production-90b1f.up.railway.app` (pode ser rotacionado — re-confirmar no painel da Railway antes de mexer no DNS) |

!!! danger "NÃO remova o registro TXT de verificação da Railway"
    O Route 53 contém um TXT `_railway-verify.api.telecomtowerpower.com.br`.
    A Railway usa esse registro para manter válido o certificado Let's Encrypt
    emitido para `api.telecomtowerpower.com.br`. Se qualquer automação
    (Terraform, external-dns, scripts de limpeza, etc.) apagar esse registro,
    a perna SECONDARY do failover vai falhar com erro de TLS na próxima vez
    que o ALB ficar unhealthy. Ele nunca deve ser editado sem antes confirmar
    o valor atual no painel da Railway.

!!! note "Detectar drift antes de um incidente"
    Execute `scripts/verify_failover.sh` (somente leitura, seguro em cron/CI).
    Ele verifica se o CNAME SECONDARY ainda aponta para o edge correto da
    Railway, se o TXT `_railway-verify` está presente e se o edge ainda serve
    um certificado que cobre `api.telecomtowerpower.com.br`. Se a Railway
    rotacionar o edge, rode novamente `scripts/setup_failover.sh` com o novo
    valor: `RAILWAY_DNS=<novo>.up.railway.app ./scripts/setup_failover.sh`.

## Common operations

### Deploy latest main to EC2

Trigger the workflow:

```bash
gh workflow run deploy-ec2-docker.yml
```

The workflow builds, pushes the API image, and runs `docker compose pull && docker compose up -d` on EC2 via SSM. Rolling replacement is performed per service — no manual downtime window needed.

### Sync Stripe secrets to EC2

```bash
gh workflow run update-ec2-stripe-secrets.yml
```

Reads `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` from GitHub Actions secrets and writes them to `/home/ubuntu/TELECOM-TOWER-POWER/secrets/`. Containers consuming these secrets are restarted automatically.

### Sync alerting secrets (Slack + SES) to EC2

```bash
gh workflow run update-ec2-alerting-secrets.yml
```

Syncs `SLACK_WEBHOOK_URL`, `SES_SMTP_USERNAME`, `SES_SMTP_PASSWORD`. After sync, Grafana is restarted and a health check is retried up to 30 × 2 s against `localhost:3001/api/health`.

### Reload Caddy after a config change

```bash
gh workflow run deploy-caddy.yml
```

Copies the `Caddyfile` to EC2 and runs `caddy reload`. Verifies HTTP 200 on `www.*`, `app.*`, `api.*`, `monitoring.*`, and `prometheus.*`.

## Troubleshooting

### Grafana health check fails during alerting-secrets workflow

- Ensure the port is **3001** on the EC2 host (not 3000 — that's the React frontend).
- SSH in and check: `docker compose logs grafana --tail=50`.
- Verify the secret files exist and are non-empty: `ls -la /home/ubuntu/TELECOM-TOWER-POWER/secrets/`.

### 502 on `api.telecomtowerpower.com.br`

- First check Railway status — the Caddy config proxies 100% of API traffic there.
- Check Caddy logs on EC2: `docker compose logs caddy --tail=100`.
- Confirm ALB target is healthy: AWS console → Target Groups → `ttp-caddy-tg`.

### 5xx alert fires

The `high-5xx-rate` alert triggers when the API emits more than 10 errors/minute over a 1-minute window. Steps:

1. Check Grafana dashboard for the affected endpoint.
2. `docker compose logs api --tail=200` on EC2, or check Railway logs if traffic is routed there.
3. If the spike is from a specific customer, check rate limits in `key_store.json`.

### Monitoring subdomain returns 404/502

- `monitoring.*` and `prometheus.*` bypass Caddy and hit ALB target groups directly.
- Verify targets are healthy: `ttp-grafana-tg` (port 3001), `ttp-prometheus-tg` (port 9090).
- If the EC2 instance was replaced, re-register it with `scripts/manage_ec2_alb.sh register`.

## Security reminders

- Secrets are **never** committed. If a secret leaks into git, rotate it immediately and force-push a history rewrite only as a last resort — prefer rotation.
- All remote EC2 execution goes through SSM; no SSH keys are configured in CI.
- Secret files on EC2 are mounted read-only at `/run/secrets/<name>` inside containers.
