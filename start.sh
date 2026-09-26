#!/bin/sh
# Start the backend in Docker: database, API, admin worker.
#
#   ./start.sh            → http://localhost:8000
#   ./start.sh --public   → also a public https://….trycloudflare.com address (for the Vercel frontend, a phone)
#   ./start.sh --https    → Caddy + Let's Encrypt for DOMAIN from .env (a server with its own domain)
#   ./start.sh --stop     → stop everything (data stays)
#
# The first run asks for the OpenAI key and writes .env; put the index dump (index-*.dump) into data/export/.
set -e
cd "$(dirname "$0")"

command -v docker >/dev/null || { echo "Docker is not installed: https://docs.docker.com/get-docker/"; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker is not running: start Docker Desktop and try again."; exit 1; }

profiles="server"
case "$1" in
    --public) profiles="server,public" ;;
    --https) profiles="server,https" ;;
    --stop) COMPOSE_PROFILES=server,public,https docker compose stop; exit 0 ;;
esac

if [ ! -f .env ]; then
    echo "First run: creating .env"
    printf "OpenAI API key (sk-…): "
    read -r key
    secret() { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$1"; }
    admin_password=$(secret 16)
    sed -e "s|^OPENAI_API_KEY=.*|OPENAI_API_KEY=$key|" \
        -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(secret 24)|" \
        -e "s|^ADMIN_LOGIN=.*|ADMIN_LOGIN=admin|" \
        -e "s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=$admin_password|" \
        -e "s|^CORS_ORIGINS=.*|CORS_ORIGINS=*|" \
        .env.example > .env
    echo "  admin login: admin / $admin_password (also in .env)"
fi

mkdir -p data/export
ls data/export/*.dump >/dev/null 2>&1 || echo "Note: no index dump in data/export/: answers will be not_found until you put one there and restart."

echo "Building and starting (the first build downloads ~3 GB and takes 10-20 min)…"
COMPOSE_PROFILES=$profiles docker compose up -d --build --remove-orphans

port=$(grep -E '^API_PORT=' .env | cut -d= -f2)
port=${port:-8000}
printf "Waiting for the API to load its models"
for _ in $(seq 1 90); do
    if curl -fs "http://localhost:$port/health" 2>/dev/null | grep -q '"models_loaded":true'; then
        echo " ok"
        echo "API:       http://localhost:$port   (docs: http://localhost:$port/docs)"
        if [ "$profiles" = "server,public" ]; then
            for _ in $(seq 1 30); do
                url=$(docker compose logs tunnel 2>/dev/null | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1)
                [ -n "$url" ] && break
                sleep 2
            done
            echo "Public:    ${url:-not ready yet: docker compose logs tunnel}"
            echo "           frontend on Vercel: NEXT_PUBLIC_API_URL=$url (the address changes on every restart)"
        fi
        echo "Logs:      docker compose logs -f api      Stop: ./start.sh --stop"
        exit 0
    fi
    printf "."
    sleep 10
done
echo
echo "The API didn't come up in 15 min. Last log lines:"
docker compose logs --tail 40 api
exit 1
