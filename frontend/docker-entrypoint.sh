#!/bin/sh
set -e

# Extract DNS resolver from container, wrapping IPv6 in brackets for nginx
RESOLVER=$(awk '/^nameserver/{ip=$2; if(ip~/:/) printf "[%s]:53",ip; else printf "%s:53",ip; exit}' /etc/resolv.conf)
export RESOLVER
echo "DNS resolver: $RESOLVER"

envsubst '$PORT $BACKEND_URL $RESOLVER' < /etc/nginx/nginx.conf.template > /etc/nginx/conf.d/default.conf

# Inject runtime config so the React app can read BACKEND_URL at runtime
cat > /usr/share/nginx/html/config.js <<EOF
window.__RUNTIME_CONFIG__ = {
  API_BASE: "${BACKEND_URL:-}"
};
EOF
echo "Runtime config: API_BASE=${BACKEND_URL:-'(not set)'}"

exec nginx -g 'daemon off;'
