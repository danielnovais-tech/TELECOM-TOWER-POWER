FROM prom/alertmanager:v0.32.0

COPY alertmanager.yml.tpl /etc/alertmanager/alertmanager.yml.tpl
COPY --chmod=0755 entrypoint.sh /entrypoint.sh

EXPOSE 9093

ENTRYPOINT ["/entrypoint.sh"]
