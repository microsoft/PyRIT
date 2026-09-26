#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

set -euo pipefail

if [[ $# -lt 2 || $# -gt 3 ]]; then
    echo "Usage: $0 IMAGE {import|gui|jupyter} [readiness-timeout-seconds]" >&2
    exit 2
fi

image=$1
mode=$2
timeout_seconds=${3:-120}
if [[ ! "$timeout_seconds" =~ ^[1-9][0-9]*$ ]]; then
    echo "Readiness timeout must be a positive integer." >&2
    exit 2
fi

case "$mode" in
    import)
        docker run --rm --entrypoint /opt/venv/bin/python "$image" \
            -c "import pyrit; print(f'PyRIT version: {pyrit.__version__}')"
        exit 0
        ;;
    gui)
        port=8000
        endpoint=/api/health
        ;;
    jupyter)
        port=8888
        endpoint=/api
        ;;
    *)
        echo "Unknown smoke-test mode: $mode" >&2
        exit 2
        ;;
esac

container_id=""
cleanup() {
    local status=$?
    trap - EXIT
    if [[ -n "$container_id" ]]; then
        if (( status != 0 )); then
            echo "::group::Container diagnostics ($mode)"
            docker inspect --format '{{json .State}}' "$container_id" ||
                echo "::warning::Could not inspect container $container_id"
            docker logs --tail 200 "$container_id" ||
                echo "::warning::Could not read container logs for $container_id"
            echo "::endgroup::"
        fi
        if ! docker rm --force "$container_id"; then
            echo "::error::Could not remove container $container_id"
            if (( status == 0 )); then
                status=1
            fi
        fi
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

container_id=$(docker create --env "PYRIT_MODE=$mode" --publish "127.0.0.1::$port" "$image")
docker start "$container_id"
address=$(docker port "$container_id" "$port/tcp")
base_url="http://$address"

echo "Waiting up to ${timeout_seconds}s for $mode at $base_url$endpoint"
deadline=$((SECONDS + timeout_seconds))
ready=false
last_response="No HTTP response"
while (( SECONDS < deadline )); do
    running=$(docker inspect --format '{{.State.Running}}' "$container_id")
    if [[ "$running" != true ]]; then
        echo "::error::Container exited before $mode was ready."
        exit 1
    fi
    remaining=$((deadline - SECONDS))
    if (( remaining <= 0 )); then
        break
    fi
    request_timeout=$((remaining < 5 ? remaining : 5))
    if last_response=$(curl --fail --silent --show-error --output /dev/null --write-out '%{http_code}' \
        --connect-timeout 2 --max-time "$request_timeout" "$base_url$endpoint" 2>&1) &&
        [[ "$last_response" == 200 ]]; then
        ready=true
        break
    fi
    if (( SECONDS < deadline )); then
        sleep 1
    fi
done
if [[ "$ready" != true ]]; then
    echo "::error::$mode did not become ready within ${timeout_seconds}s. Last response: $last_response"
    exit 1
fi

if [[ "$mode" == gui ]]; then
    response=$(curl --fail --silent --show-error --connect-timeout 2 --max-time 5 "$base_url/")
    if ! grep -iq '<!doctype html>' <<< "$response"; then
        echo "::error::Frontend HTML was not served. Response: ${response:0:500}"
        exit 1
    fi
fi
echo "$mode smoke checks passed."
