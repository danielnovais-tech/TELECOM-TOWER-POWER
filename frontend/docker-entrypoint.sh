#!/bin/sh
set -e
envsubst '$PORT $BACKEND_URL' < /etc/nginx/nginx.conf.template > /etc/nginx/conf.d/default.conf

# Inject runtime config so the React app can read BACKEND_URL at runtime
cat > /usr/share/nginx/html/config.js <<EOF
window.__RUNTIME_CONFIG__ = {
  API_BASE: "${BACKEND_URL:-}"
};
EOF
echo "Runtime config: API_BASE=${BACKEND_URL:-'(not set)'}"

exec nginx -g 'daemon off;'
