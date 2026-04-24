# IAM bootstrap for GitHub Actions OIDC

Account-specific IAM artifacts for the GitHub Actions workflows in
`.github/workflows/`. Checked in because:

- They contain **no secrets** (just ARNs, zone IDs, account IDs, policy JSON).
- They are the source-of-truth for the least-privilege scoping of each
  workflow — reviewing a diff here is reviewing a security boundary.
- They must be re-applicable if the role is ever accidentally deleted.

## Roles in this directory

| Role | Used by | Trust (who can assume) | Permissions |
|------|---------|------------------------|-------------|
| `Route53FailoverRotateRole` | [`failover-rotate.yml`](../../.github/workflows/failover-rotate.yml) | OIDC from `environment:production-dns` (protected, requires approval) | Route 53 write, scoped to hosted zone `Z01723123PIF6651ZDGY5` |
| `Route53ReadOnlyForDriftCheck` *(not in this dir yet)* | [`failover-drift-check.yml`](../../.github/workflows/failover-drift-check.yml) | OIDC from branch `main` | Route 53 read-only (`ListHostedZonesByName`, `ListResourceRecordSets`) |

## Repo variables referenced

| Variable | Value |
|----------|-------|
| `AWS_FAILOVER_ROTATE_ROLE_ARN` | `arn:aws:iam::490083271496:role/Route53FailoverRotateRole` |
| `AWS_FAILOVER_DRIFT_ROLE_ARN`  | `arn:aws:iam::490083271496:role/Route53ReadOnlyForDriftCheck` |

## Bootstrap: `Route53FailoverRotateRole`

One-time, run by an IAM admin against account `490083271496`:

```bash
cd $(git rev-parse --show-toplevel)

aws iam create-role \
  --role-name Route53FailoverRotateRole \
  --assume-role-policy-document file://scripts/iam/failover-rotate-trust.json \
  --description "OIDC role for .github/workflows/failover-rotate.yml (zone-scoped Route 53 write)" \
  --no-cli-pager

aws iam put-role-policy \
  --role-name Route53FailoverRotateRole \
  --policy-name FailoverRotatePermissions \
  --policy-document file://scripts/iam/failover-rotate-permissions.json \
  --no-cli-pager

# Wire up the repo variable
gh variable set AWS_FAILOVER_ROTATE_ROLE_ARN \
  --body "arn:aws:iam::490083271496:role/Route53FailoverRotateRole"
```

### Updating the policy

Edit `failover-rotate-permissions.json`, commit, then re-apply:

```bash
aws iam put-role-policy \
  --role-name Route53FailoverRotateRole \
  --policy-name FailoverRotatePermissions \
  --policy-document file://scripts/iam/failover-rotate-permissions.json \
  --no-cli-pager
```

### Trust-policy subject claim

The trust policy restricts `sts.amazonaws.com:sub` to

    repo:danielnovais-tech/TELECOM-TOWER-POWER:environment:production-dns

This means the role can **only** be assumed by a job that declares
`environment: production-dns` in its YAML. Combined with the GitHub
Environment's required-reviewer protection rule, every DNS rotation
requires an explicit human approval click before AWS credentials are ever
issued.

## Why not Terraform / CDK?

Two reasons:

1. These are one-shot, rarely-changed security artifacts. The operational
   overhead of a TF state backend for two roles is not worth it.
2. Applying them by hand keeps IAM changes visible — a misbehaving IaC
   apply cannot silently widen permissions.

If this grows past ~5 roles, revisit the decision.

## GitHub Environment setup (manual, UI only)

The `production-dns` environment must be configured in the GitHub UI —
there is no API for protection rules as of this writing:

1. Repo → Settings → Environments → New environment → `production-dns`
2. **Required reviewers**: add at least one human (you).
3. **Deployment branches and tags**: `Protected branches only` *(or
   `Selected branches and tags` → pattern `main`)*.

Without step 2, the workflow will run without approval and the whole
safety design collapses.
