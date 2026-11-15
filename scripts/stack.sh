#!/usr/bin/env bash
# Local stack control. Run through pnpm from the repo root:
#
#   pnpm start            start Docker if needed, boot all containers, migrate, print URLs
#   pnpm start --build    same, rebuilding images first (after Dockerfile / dependency changes)
#   pnpm stop             stop all containers (data volumes are kept)
#   pnpm restart          stop, then start
#   pnpm logs             follow logs of every service
#   pnpm status           list containers and their state
#   pnpm scrape           scan all listing portals now (domoplius, skelbiu, aruodas, alio)
#   pnpm scrape alio 5    scan one spider, 5 pages (any spider: infostatyba, tpdris, ...)
#   pnpm inspect domoplius --follow 2
#                         read-only Playwright map of a site: buttons, ids, classes, selectors,
#                         forms, card patterns, XHR endpoints → scrapers/recon/out/*/report.md
#
# Scheduled scans run by themselves in the scraper_worker container (see config/celery.py).
set -euo pipefail

cd "$(dirname "$0")/.."

step() { printf '\n\033[1;34m▶ %s\033[0m\n' "$1"; }

# Wait until a URL answers with any HTTP status. Args: url, label, timeout seconds.
wait_for_http() {
  local url=$1 label=$2 timeout=$3 waited=0
  until [ "$(curl -s -o /dev/null -w '%{http_code}' "$url" || true)" != "000" ]; do
    if [ "$waited" -ge "$timeout" ]; then
      echo "  $label did not answer within ${timeout}s. Check: docker compose logs $label"
      return 1
    fi
    sleep 2; waited=$((waited + 2))
  done
  echo "  $label is up"
}

ensure_docker() {
  if docker info >/dev/null 2>&1; then return; fi
  step "Starting Docker Desktop"
  open -a Docker
  local waited=0
  until docker info >/dev/null 2>&1; do
    if [ "$waited" -ge 120 ]; then echo "  Docker did not start within 120s."; exit 1; fi
    sleep 2; waited=$((waited + 2))
  done
  echo "  Docker is running"
}

start() {
  local build=""
  [ "${1:-}" = "--build" ] && build="--build"

  if [ ! -f .env ]; then
    step "No .env found, copying .env.example"
    cp .env.example .env
  fi

  ensure_docker

  # Nominatim's first import gets OOM-killed below ~6 GB (osm2pgsql "server closed the connection").
  local mem_gb
  mem_gb=$(docker info --format '{{.MemTotal}}' | awk '{printf "%d", $1/1073741824}')
  if [ "$mem_gb" -lt 6 ]; then
    printf '\n\033[1;33m! Docker has only %s GB memory. Nominatim import needs ~8 GB:\n' "$mem_gb"
    printf '  Docker Desktop > Settings > Resources > Memory, then pnpm restart.\033[0m\n'
  fi

  step "Starting containers"
  docker compose up -d $build

  step "Applying migrations"
  local attempt
  for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if docker compose exec -T backend python manage.py migrate --noinput; then break; fi
    if [ "$attempt" -eq 10 ]; then echo "  Migrations failed. Check: docker compose logs backend"; exit 1; fi
    sleep 3
  done

  step "Waiting for services"
  wait_for_http http://localhost:8000/api/docs backend 90 || true
  wait_for_http http://localhost:5173 frontend 180 || true
  if curl -sf http://localhost:7070/status >/dev/null 2>&1; then
    echo "  nominatim is up"
  else
    echo "  nominatim is still importing (first boot takes 5-10 min): docker compose logs -f nominatim"
  fi

  cat <<'EOF'

✅ Stack is up
  Frontend   http://localhost:5173
  API docs   http://localhost:8000/api/docs
  Admin      http://localhost:8000/admin
  Mailpit    http://localhost:8025

  pnpm logs | pnpm status | pnpm restart | pnpm stop
EOF
}

stop() {
  if ! docker info >/dev/null 2>&1; then echo "Docker is not running, nothing to stop."; return; fi
  step "Stopping containers"
  docker compose down
}

scrape() {
  local spiders=("domoplius" "skelbiu" "aruodas" "alio") pages=2 failed=()
  if [ $# -ge 1 ]; then spiders=("$1"); fi
  if [ $# -ge 2 ]; then pages=$2; fi
  ensure_docker
  # Reuse the long-running scraper_worker container (same image, mounts and env as
  # the `scrapers` service) instead of building/starting a second container.
  docker compose up -d scraper_worker >/dev/null
  for spider in "${spiders[@]}"; do
    step "Scanning $spider (max $pages pages)"
    if ! docker compose exec -T scraper_worker scrapy crawl "$spider" -a max_pages="$pages" \
      -s LOG_LEVEL=INFO; then
      failed+=("$spider")
    fi
  done
  step "Listings in database"
  docker compose exec -T postgres psql -U postgres -d realestate -Atc \
    "select source, count(*) from listings_listing group by source order by 2 desc"
  if [ ${#failed[@]} -gt 0 ]; then echo "Failed: ${failed[*]}"; return 1; fi
}

cmd=${1:-start}
shift || true
case "$cmd" in
  start) start "$@" ;;
  stop) stop ;;
  restart) stop; start "$@" ;;
  logs) docker compose logs -f "$@" ;;
  status) docker compose ps ;;
  scrape) scrape "$@" ;;
  inspect)
    ensure_docker
    docker compose up -d scraper_worker >/dev/null
    docker compose exec -T scraper_worker python -m recon.inspect_site "$@"
    ;;
  *) sed -n '2,15p' "$0"; exit 1 ;;
esac
