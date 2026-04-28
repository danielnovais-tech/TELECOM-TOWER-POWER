# 10 — Secure SDLC Policy

| | |
|---|---|
| **Owner** | Engineering |
| **Review cadence** | Annual |
| **Approver** | CTO |
| **Last reviewed** | 2026-04 |
| **Mapped controls** | CC3.2, CC5.1, CC5.2, CC7.1, CC8.1, PI1.1, PI1.2 |

## 1. Threat modelling

- Required section in PR template for any change that touches authentication, authorization, data flow, or sub-processor integration.
- STRIDE-lite categories: Spoofing, Tampering, Repudiation, Information disclosure, DoS, Elevation of privilege.
- Output captured in PR; persistent threat-model doc updated annually for the platform overall.

## 2. Secure coding standards

### OWASP Top 10 mitigations

| Risk | Mitigation |
|---|---|
| A01 Broken access control | `verify_api_key` + `require_tier` decorators; tenant-scoped queries; IDOR test suite |
| A02 Cryptographic failures | KMS at rest, TLS 1.2+ in transit, no homebrew crypto, see [`08-encryption.md`](08-encryption.md) |
| A03 Injection | SQLAlchemy parameterized queries everywhere; Pydantic input validation |
| A04 Insecure design | Threat modelling per change; CAB review for schema/infra changes |
| A05 Security misconfiguration | IaC + GitHub Actions; AWS Config rules + drift alarms |
| A06 Vulnerable components | Dependabot (daily) + ECR image scan-on-push |
| A07 Auth & session failures | Cognito (managed); no session cookies in API path; Bearer token validation |
| A08 Software & data integrity | All deploys via signed GitHub Actions; container images scanned + tagged with commit SHA |
| A09 Logging & monitoring failures | See [`09-logging-monitoring.md`](09-logging-monitoring.md) |
| A10 SSRF | All outbound HTTP via egress allowlist (security group + Squid proxy on EC2) |

### Coding rules

- Type hints on all Python public functions.
- Pydantic models on every API request/response.
- No `eval`, `exec`, `pickle.loads` of untrusted input, `subprocess(shell=True)` with user input.
- Secrets sourced from env (not hardcoded); `git-secrets` pre-commit hook blocks common secret patterns.

## 3. Code review

- ≥1 reviewer per PR.
- Code-owners file maps directories to teams.
- Security lead is a code-owner of `sso_auth.py`, `verify_api_key`, `stripe_webhook_service.py`, `secrets/`, `.github/workflows/`.

## 4. CI gates

Every PR must pass before merge:

- Unit tests (`pytest`).
- Integration tests against ephemeral Postgres.
- Lint (`ruff`).
- Type check (`mypy --strict` on hot paths).
- Security scan (Dependabot + Bandit).
- Container image scan (ECR scan-on-push).
- `mkdocs build --strict` for docs.

## 5. Vulnerability management

| Severity | Patch SLA |
|---|---|
| Critical | 7 days |
| High | 30 days |
| Medium | 90 days |
| Low | best-effort |

Tracked via Dependabot alerts and ECR image-scan findings. Exceptions require CTO sign-off + compensating control + expiry date.

## 6. Penetration testing

- Annual external pentest by an independent firm.
- Findings tracked in `evidence/security/pentest-YYYY/`; remediation per severity SLA above.
- Bug bounty (informal) at `security@telecomtowerpower.com.br` with 90-day disclosure window.

## 7. Pre-prod environments

- Local: Docker Compose; uses synthetic data (`sample_receivers.csv`).
- Staging: Optional ECS staging service for major changes; uses anonymized prod snapshot (no real customer data).
- Production: Real customer data; full controls.

## 8. Output integrity (PI1.4)

- PDF generation is deterministic given the same input; checksum recorded in audit log.
- Replay test in CI: a pinned input must produce a pinned-checksum PDF or CI fails.
