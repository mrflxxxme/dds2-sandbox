#!/usr/bin/env bash
# DDS2 worktree/docker frontend dev-хелпер — turbopack-reload + one-off гейты.
#
#   scripts/wt-dev.sh reload             # wipe .next + restart frontend-контейнера + curl до 200
#   scripts/wt-dev.sh gates [paths...]   # tsc + eslint (+ vitest, если передан *.test.* / __tests__) в one-off контейнере
#
# Автоопределяет запущенный *frontend-react* контейнер (dds2wt-* в приоритете —
# worktree-стенд), его образ, node_modules-volume и опубликованный порт. Гейтит
# frontend-react ТЕКУЩЕГО git-дерева (worktree или main), примонтированного :ro
# (root-файлы не сорят turbopack). Пути можно давать и как frontend-react/… , и как src/… .
#
# Зачем скрипт, а не команды напрямую: (1) не копипастить рецепт; (2) обёртка прячет
# от хука pre_tool_check ложно-опасный `docker run --rm … -frontend-react`.
set -uo pipefail

# ── Docker бинарь (symlink /usr/local/bin/docker бывает битый после ребута) ──
DOCKER="${DOCKER:-}"
if [ -z "$DOCKER" ]; then
  if command -v docker >/dev/null 2>&1 && docker version >/dev/null 2>&1; then
    DOCKER=docker
  else
    DOCKER=/Applications/Docker.app/Contents/Resources/bin/docker
  fi
fi

# ── Frontend-контейнер: dds2wt-* приоритетно, иначе любой *frontend-react* ──
FRONT=$($DOCKER ps --format '{{.Names}}' | grep -E 'frontend-react' | grep -E '^dds2wt' | head -1)
[ -z "$FRONT" ] && FRONT=$($DOCKER ps --format '{{.Names}}' | grep -E 'frontend-react' | head -1)
if [ -z "$FRONT" ]; then
  echo "wt-dev: не найден запущенный *frontend-react* контейнер" >&2
  exit 1
fi

reload() {
  echo "wt-dev: reload $FRONT (wipe .next + restart)"
  $DOCKER exec "$FRONT" sh -c 'find /app/.next -mindepth 1 -delete' 2>/dev/null || true
  $DOCKER restart "$FRONT" >/dev/null
  local port
  port=$($DOCKER port "$FRONT" 3000 2>/dev/null | sed -E 's/.*:([0-9]+)$/\1/' | head -1)
  [ -z "$port" ] && port=3000
  echo "wt-dev: жду http://localhost:$port …"
  local code=000
  for _ in $(seq 1 40); do
    code=$(curl -s -o /dev/null -w '%{http_code}' --retry-all-errors "http://localhost:$port/" 2>/dev/null || echo 000)
    [ "$code" = "200" ] && { echo "wt-dev: frontend 200 ✓ (:$port)"; return 0; }
    sleep 2
  done
  echo "wt-dev: не дождался 200 (последний код: $code)" >&2
  return 1
}

gates() {
  local wt img nmvol es_targets="" vitest=""
  wt=$(git rev-parse --show-toplevel)
  img=$($DOCKER inspect "$FRONT" --format '{{.Config.Image}}')
  nmvol=$($DOCKER inspect "$FRONT" --format '{{range .Mounts}}{{if eq .Destination "/app/node_modules"}}{{.Name}}{{end}}{{end}}')
  if [ -z "$nmvol" ]; then
    echo "wt-dev: не нашёл node_modules-volume у $FRONT" >&2
    exit 1
  fi
  # Нормализуем пути к frontend-react-относительным (снимаем префикс frontend-react/)
  # и оборачиваем в одинарные кавычки — в путях есть (main)/[slug], иначе sh контейнера
  # спотыкается на «(». Одинарных кавычек в путях не бывает, экранировать нечего.
  for p in "$@"; do
    p=${p#frontend-react/}
    es_targets="$es_targets '$p'"
    case "$p" in
      *.test.ts|*.test.tsx|*__tests__*) vitest="$vitest '$p'" ;;
    esac
  done
  [ -z "$es_targets" ] && es_targets="src"
  echo "wt-dev: gates ($img · vol $nmvol) → $wt/frontend-react"
  $DOCKER run --rm --user 100:101 \
    -v "$wt/frontend-react":/app:ro -v "$nmvol":/app/node_modules \
    --entrypoint sh "$img" -c "
      cd /app && rc=0
      echo '── tsc ──';    node node_modules/typescript/bin/tsc --noEmit --tsBuildInfoFile /tmp/t.info || rc=1
      echo '── eslint ──';  node node_modules/.bin/eslint $es_targets || rc=1
      ${vitest:+echo '── vitest ──'; node node_modules/.bin/vitest run $vitest || rc=1;}
      exit \$rc"
}

cmd="${1:-}"
shift || true
case "$cmd" in
  reload) reload ;;
  gates)  gates "$@" ;;
  *) echo "usage: wt-dev.sh {reload | gates [paths...]}" >&2; exit 2 ;;
esac
