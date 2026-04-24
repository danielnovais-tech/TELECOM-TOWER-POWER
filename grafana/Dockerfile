FROM grafana/grafana:13.0.1

# Provisioning: datasources, dashboards, alerting contact points
COPY provisioning/ /etc/grafana/provisioning/

# Dashboard JSON (placed under /etc/grafana to avoid being masked by the
# /var/lib/grafana data volume mounted in docker-compose)
COPY dashboards/ /etc/grafana/dashboards/

# Entrypoint: hydrate SLACK_WEBHOOK_URL from Docker secret before launching Grafana
USER root
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
USER grafana

EXPOSE 3000

ENTRYPOINT ["/entrypoint.sh"]
