FROM grafana/grafana:latest

# Provisioning: datasources, dashboards, alerting contact points
COPY provisioning/ /etc/grafana/provisioning/

# Dashboard JSON
COPY dashboards/ /var/lib/grafana/dashboards/

# Entrypoint: hydrate SLACK_WEBHOOK_URL from Docker secret before launching Grafana
USER root
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
USER grafana

EXPOSE 3000

ENTRYPOINT ["/entrypoint.sh"]
