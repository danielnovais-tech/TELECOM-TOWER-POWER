FROM grafana/grafana:13.0.1

# Provisioning: datasources, dashboards, alerting contact points
COPY provisioning/ /etc/grafana/provisioning/

# Dashboard JSON (placed under /etc/grafana to avoid being masked by the
# /var/lib/grafana data volume mounted in docker-compose)
COPY dashboards/ /etc/grafana/dashboards/

# Public status page: anonymous Viewer role lands directly on public-status.json.
# Authenticated admin login still works via the standard form for editing.
ENV GF_AUTH_ANONYMOUS_ENABLED="true" \
    GF_AUTH_ANONYMOUS_ORG_ROLE="Viewer" \
    GF_AUTH_ANONYMOUS_HIDE_VERSION="true" \
    GF_USERS_VIEWERS_CAN_EDIT="false" \
    GF_SECURITY_ALLOW_EMBEDDING="true" \
    GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH="/etc/grafana/dashboards/public-status.json"

# Entrypoint: hydrate SLACK_WEBHOOK_URL from Docker secret before launching Grafana
USER root
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
USER grafana

EXPOSE 3000

ENTRYPOINT ["/entrypoint.sh"]
