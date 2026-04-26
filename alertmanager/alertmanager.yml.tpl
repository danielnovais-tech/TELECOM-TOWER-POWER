# ─────────────────────────────────────────────────────────────
# REFERENCE / DOCUMENTATION ONLY
# This template is NOT consumed at runtime.
# The actual Alertmanager config is generated dynamically by
# entrypoint.sh via an unquoted heredoc that performs env
# substitution at container start.
# ─────────────────────────────────────────────────────────────
global:
  resolve_timeout: 5m
  smtp_smarthost: "${SMTP_HOST}"
  smtp_from: "${SMTP_FROM}"
  smtp_auth_username: "${SES_SMTP_USERNAME}"
  smtp_auth_password: "${SES_SMTP_PASSWORD}"
  smtp_require_tls: true
  slack_api_url: "${SLACK_WEBHOOK_URL}"

route:
  group_by: ["alertname", "severity"]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  receiver: default
  routes:
    - match:
        severity: critical
      receiver: critical
      group_wait: 15s
      repeat_interval: 1h
    - match:
        severity: warning
      receiver: warning
      repeat_interval: 2h

receivers:
  - name: default
    slack_configs:
      - channel: "#alerts"
        send_resolved: true

  - name: critical
    slack_configs:
      - channel: "#alerts-critical"
        send_resolved: true
        title: '{{ .GroupLabels.alertname }}'
        text: '{{ range .Alerts }}{{ .Annotations.summary }}{{ end }}'
    pagerduty_configs:
      - routing_key: "${PAGERDUTY_ROUTING_KEY}"
        send_resolved: true
        severity: '{{ if .CommonLabels.severity }}{{ .CommonLabels.severity }}{{ else }}critical{{ end }}'
        description: '{{ .GroupLabels.alertname }}: {{ range .Alerts }}{{ .Annotations.summary }} {{ end }}'
    email_configs:
      - to: "${ALERT_EMAIL_TO}"
        send_resolved: true

  - name: warning
    slack_configs:
      - channel: "#alerts-warning"
        send_resolved: true
        title: '{{ .GroupLabels.alertname }}'
        text: '{{ range .Alerts }}{{ .Annotations.summary }}{{ end }}'

inhibit_rules:
  - source_match:
      severity: critical
    target_match:
      severity: warning
    equal: ["alertname"]
