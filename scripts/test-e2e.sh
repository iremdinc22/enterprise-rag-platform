#!/bin/sh
# A unique project, no private env file, no published ports, no persistent volumes.
set -eu
cd "$(dirname "$0")/.."
project="rag-e2e-$(date -u +%Y%m%d%H%M%S)-$$"
compose() {
    docker compose --env-file /dev/null -f compose.e2e.yaml -p "$project" "$@"
}
cleanup() {
    result=$?
    trap - EXIT
    compose down --timeout 20 || result=1
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
compose config --quiet
compose build e2e
compose up -d --no-build --wait --wait-timeout 90 qdrant redis worker
# Defense in depth: the actual network must have no external routing.
[ "$(docker network inspect "${project}_isolated" --format '{{.Internal}}')" = true ]
# Refuse accidental storage/port additions to the dedicated service definitions.
for service in qdrant redis worker; do
    id=$(compose ps -q "$service")
    mounts=$(docker inspect "$id" --format '{{range .Mounts}}{{.Type}} {{end}}')
    case "$mounts" in *bind*|*volume*) echo 'E2E refuses persistent/shared mounts' >&2; exit 1;; esac
    [ "$(docker inspect "$id" --format '{{len .HostConfig.PortBindings}}')" = 0 ]
done
if ! compose run --rm --no-deps e2e; then
    compose ps
    compose logs --no-color --tail 80 worker redis qdrant
    exit 1
fi
