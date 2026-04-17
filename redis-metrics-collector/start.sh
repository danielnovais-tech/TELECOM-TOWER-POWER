#!/bin/sh
exec redis_exporter --web.listen-address=":${PORT:-9121}"
