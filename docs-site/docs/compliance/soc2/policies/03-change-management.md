# 03 — Change Management Policy

| | |
|---|---|
| **Owner** | Engineering |
| **Review cadence** | Annual |
| **Approver** | CTO |
| **Last reviewed** | 2026-04 |
| **Mapped controls** | CC8.1.1, CC8.1.2, CC8.1.3, CC8.1.4, CC3.4 |

## 1. Principle

No production change reaches customers without (a) automated tests, (b) peer review, and (c) an audit trail.

## 2. Scope

- Application code (`telecom_tower_power_api.py`, frontend, workers).
- Infrastructure (Terraform/CloudFormation/`template.yaml`/`ecs-task-definition.json`).
- CI/CD workflows themselves (`.github/workflows/`).
- Database schema (`migrations/` Alembic).
- Secrets and configuration.
- Public docs (`docs-site/`).

## 3. Standard change flow

1. **Branch:** Engineer opens a feature branch off `main`.
2. **PR:** Pull request includes:
   - Description of change.
   - Threat-model section (impact on confidentiality/integrity/availability).
   - Test evidence.
   - For schema changes: migration plan + rollback plan.
3. **Review:** ≥1 reviewer with the relevant code-owner mapping (`.github/CODEOWNERS`). For infra or schema PRs, ≥1 reviewer from CAB.
4. **CI:** All checks must pass — unit tests, integration tests, lint, type-check, security scan (Dependabot, ECR scan-on-push), `mkdocs build --strict`.
5. **Merge:** Squash merge to `main`. Branch protection blocks force-push and requires up-to-date branch.
6. **Deploy:** Automated via the matching workflow under `.github/workflows/`. All 16 workflows use `concurrency:` groups to serialize per-environment.
7. **Post-deploy:** Health checks; on failure, automated rollback (where supported) or paged on-call.

## 4. Emergency change

For incidents requiring immediate action (P1):

1. On-call engineer may deploy without pre-merge review **only if** a peer is paged simultaneously and confirms verbally/in Slack.
2. Within 48 hours: retrospective PR documenting the change, root cause, and corrective action.
3. Logged in `evidence/changes/emergency/` with timestamp, deployer, witness, and ticket link.

## 5. Schema changes

- Alembic migrations only. Manual SQL on production database is prohibited.
- Migrations are forward-compatible (additive) by default. Destructive migrations require a 2-phase rollout (deploy code that tolerates both schemas → run migration → deploy code that uses new schema).
- Backups verified before any destructive migration (see [`06-business-continuity.md`](06-business-continuity.md)).

## 6. Configuration changes

- Environment variables and secrets live in AWS SSM Parameter Store (SecureString, KMS-encrypted).
- Sync to runtime targets via:
  - `update-ec2-stripe-secrets.yml` (EC2 Docker Compose).
  - `update-ec2-alerting-secrets.yml` (EC2 Alertmanager + Grafana).
  - ECS task-definition `secrets:` references for the API container.
- All secret rotations are logged via CloudTrail; the workflow run itself is evidence.

## 7. Tracked metrics

- Mean lead time for change (commit → production).
- Change failure rate (deployments requiring rollback or hotfix within 24 h).
- Time-to-restore on incident.

These DORA metrics are reviewed monthly in the SRE staff meeting.
