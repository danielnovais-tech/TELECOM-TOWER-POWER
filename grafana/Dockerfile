FROM grafana/grafana:latest

# Provisioning: datasources, dashboards, alerting contact points
COPY provisioning/ /etc/grafana/provisioning/

# Dashboard JSON
COPY dashboards/ /var/lib/grafana/dashboards/

EXPOSE 3000
