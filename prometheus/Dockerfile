FROM prom/prometheus:v3.11.2

COPY prometheus.yml.tpl /etc/prometheus/prometheus.yml.tpl
COPY alert_rules.yml /etc/prometheus/alert_rules.yml
COPY --chmod=0755 entrypoint.sh /entrypoint.sh

EXPOSE 9090

# Override upstream ENTRYPOINT so we can render the config from a template
# at container start. entrypoint.sh exec's /bin/prometheus with the standard
# flags, so behaviour is otherwise identical to the upstream image.
ENTRYPOINT ["/entrypoint.sh"]
