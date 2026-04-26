#!/bin/sh
set -e

# Read Docker secrets (fall back to env vars or empty strings)
SES_SMTP_USERNAME=$(cat /run/secrets/ses_smtp_username 2>/dev/null || echo "${SES_SMTP_USERNAME:-}")
SES_SMTP_PASSWORD=$(cat /run/secrets/ses_smtp_password 2>/dev/null || echo "${SES_SMTP_PASSWORD:-}")
SLACK_WEBHOOK_URL=$(cat /run/secrets/slack_webhook_url 2>/dev/null || echo "${SLACK_WEBHOOK_URL:-}")
PAGERDUTY_ROUTING_KEY=$(cat /run/secrets/pagerduty_routing_key 2>/dev/null || echo "${PAGERDUTY_ROUTING_KEY:-}")

# Build the config dynamically — only include slack if a real URL is set
SLACK_GLOBAL=""
SLACK_DEFAULT=""
SLACK_CRITICAL=""
SLACK_WARNING=""
if [ -n "$SLACK_WEBHOOK_URL" ] && echo "$SLACK_WEBHOOK_URL" | grep -q "^https://"; then
  SLACK_GLOBAL="  slack_api_url: \"${SLACK_WEBHOOK_URL}\""
  SLACK_DEFAULT='    slack_configs:
      - channel: "#alerts"
        send_resolved: true'
  SLACK_CRITICAL='    slack_configs:
      - channel: "#alerts-critical"
        send_resolved: true
        title: '"'"'{{ .GroupLabels.alertname }}'"'"'
        text: '"'"'{{ range .Alerts }}{{ .Annotations.summary }}{{ end }}'"'"''
  SLACK_WARNING='    slack_configs:
      - channel: "#alerts-warning"
        send_resolved: true
        title: '"'"'{{ .GroupLabels.alertname }}'"'"'
        text: '"'"'{{ range .Alerts }}{{ .Annotations.summary }}{{ end }}'"'"''
fi

# PagerDuty — only attached to the `critical` receiver (we never page on warnings)
PD_CRITICAL=""
if [ -n "$PAGERDUTY_ROUTING_KEY" ]; then
  PD_CRITICAL='    pagerduty_configs:
      - routing_key: "'"${PAGERDUTY_ROUTING_KEY}"'"
        send_resolved: true
        severity: '"'"'{{ if .CommonLabels.severity }}{{ .CommonLabels.severity }}{{ else }}critical{{ end }}'"'"'
        description: '"'"'{{ .GroupLabels.alertname }}: {{ range .Alerts }}{{ .Annotations.summary }} {{ end }}'"'"''
fi

cat > /etc/alertmanager/alertmanager.yml <<ENDCFG
global:
  resolve_timeout: 5m
  smtp_smarthost: "${SMTP_HOST}"
  smtp_from: "${SMTP_FROM}"
  smtp_auth_username: "${SES_SMTP_USERNAME}"
  smtp_auth_password: "${SES_SMTP_PASSWORD}"
  smtp_require_tls: true
${SLACK_GLOBAL}

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
${SLACK_DEFAULT}

  - name: critical
${SLACK_CRITICAL}
${PD_CRITICAL}
    email_configs:
      - to: "${ALERT_EMAIL_TO}"
        send_resolved: true

  - name: warning
${SLACK_WARNING}

inhibit_rules:
  - source_match:
      severity: critical
    target_match:
      severity: warning
    equal: ["alertname"]
ENDCFG

exec /bin/alertmanager \
  --config.file=/etc/alertmanager/alertmanager.yml \
  --storage.path=/alertmanager
