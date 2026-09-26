#!/bin/sh
# Update the server to origin/main and restart what changed (run by .github/workflows/ci.yml over SSH,
# or by hand: sh docker/deploy.sh). The database, the index and downloaded files live in volumes and stay.
set -e
cd "$(dirname "$0")/.."
git fetch --quiet origin main
git reset --quiet --hard origin/main
COMPOSE_PROFILES=${COMPOSE_PROFILES:-server} docker compose up -d --build --remove-orphans
docker image prune -f > /dev/null
# Wait for the API to load its models and answer (first start with a dump restore takes a few minutes).
for i in $(seq 1 60); do
    if docker compose exec -T api curl -fs http://localhost:8000/health 2>/dev/null | grep -q '"models_loaded":true'; then
        echo "deployed $(git rev-parse --short HEAD): API healthy"
        exit 0
    fi
    sleep 10
done
echo "API not healthy after 10 min: docker compose logs api" >&2
docker compose logs --tail 50 api >&2
exit 1
