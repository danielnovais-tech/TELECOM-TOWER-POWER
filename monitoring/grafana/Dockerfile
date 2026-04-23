FROM grafana/grafana:12.2.0

USER root

# Provisioning: datasources, dashboards, alerting contact points/policies/rules.
COPY provisioning/ /etc/grafana/provisioning/

# Dashboard JSON placed under /etc/grafana/dashboards/ to avoid being masked
# if a named volume is mounted at /var/lib/grafana. Path matches
# provisioning/dashboards/dashboards.yml.
COPY dashboards/ /etc/grafana/dashboards/

# Railway exposes secrets as env vars directly; no Docker secret hydration
# needed here. Defaults only so provisioning YAML interpolation succeeds
# even when SLACK_WEBHOOK_URL / ALERT_EMAIL_TO are unset.
ENV SLACK_CHANNEL="#alerts" \
    ALERT_EMAIL_TO="daniel.novais@sempreceub.com"

COPY entrypoint.sh /custom-entrypoint.sh
RUN chmod +x /custom-entrypoint.sh

EXPOSE 3000

ENTRYPOINT ["/custom-entrypoint.sh"]
