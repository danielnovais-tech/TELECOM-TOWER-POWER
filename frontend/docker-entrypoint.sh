#!/bin/sh
set -e
# Extract DNS resolver, wrapping IPv6 addresses in brackets for nginx
export RESOLVER=$(awk '/^nameserver/{
  if ($2 ~ /:/) printf "[%s]:53", $2;
  else printf "%s:53", $2;
  exit
}' /etc/resolv.conf)
echo "Using DNS resolver: $RESOLVER"
envsubst '$PORT $BACKEND_URL $RESOLVER' < /etc/nginx/nginx.conf.template > /etc/nginx/conf.d/default.conf

# Inject runtime config so the React app can read BACKEND_URL at runtime
cat > /usr/share/nginx/html/config.js <<EOF
window.__RUNTIME_CONFIG__ = {
  API_BASE: "${BACKEND_URL:-}"
};
EOF
echo "Runtime config: API_BASE=${BACKEND_URL:-'(not set)'}"

exec nginx -g 'daemon off;'
