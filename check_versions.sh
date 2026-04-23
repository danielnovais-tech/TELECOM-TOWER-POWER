get_version() {
    pkg=$1
    current=$2
    latest=$(curl -s "https://pypi.org/pypi/$pkg/json" | python3 -c "import sys,json; data=json.load(sys.stdin); print(data['info']['version'])" 2>/dev/null)
    if [ -n "$latest" ]; then
        if [ -n "$current" ]; then
            if [ "$current" != "$latest" ]; then
                echo "$pkg $current→$latest"
            fi
        else
            echo "$pkg $latest"
        fi
    fi
}
export -f get_version

echo "--- Pinned Comparison ---"
printf "fastapi 0.136.0\nuvicorn 0.44.0\naiohttp 3.13.5\npydantic 2.13.2\npython-multipart 0.0.26\nreportlab 4.4.10\nstreamlit 1.56.0\nrequests 2.33.1\nfolium 0.20.0\nstreamlit-folium 0.27.1\npandas 2.2.3\nmangum >=0.19.0\n" | xargs -P 10 -n 2 bash -c 'get_version "$0" "$1"'

echo "--- Unpinned Latest ---"
printf "stripe\nprometheus-client\nprometheus-fastapi-instrumentator\npsycopg2-binary\npython-json-logger\nalembic\nsqlalchemy\nasyncpg\naiosqlite\nredis\nrq\nboto3\naiofiles\nopenpyxl\ngeopy\nmatplotlib\nnumpy\nwebsockets\n" | xargs -P 10 -n 1 bash -c 'get_version "$0"'
