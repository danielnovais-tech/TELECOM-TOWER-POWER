#!/bin/sh
# load_secrets.sh – Source this to bridge /run/secrets/ files into env vars.
# Usage: . /app/load_secrets.sh && exec python my_app.py
for _sf in /run/secrets/*; do
    [ -f "$_sf" ] || continue
    _name=$(basename "$_sf" | tr '[:lower:]-' '[:upper:]_')
    if eval "[ -z \"\${${_name}:-}\" ]"; then
        export "$_name"="$(cat "$_sf")"
    fi
done
unset _sf _name
