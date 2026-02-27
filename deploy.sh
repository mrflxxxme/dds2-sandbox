#!/bin/zsh
# deploy.sh — Копирует файлы в проект и пересобирает
# Использование: ./deploy.sh файл1 файл2 ...

set -e
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

get_dest() {
  case "$1" in
    models.py)           echo "backend/models.py" ;;
    main.py)             echo "backend/main.py" ;;
    service.py)          echo "backend/etl/service.py" ;;
    schemas.py)          echo "backend/schemas.py" ;;
    cost_backend.py)     echo "backend/routers/cost.py" ;;
    planning_backend.py) echo "backend/routers/planning.py" ;;
    import_txn.py)       echo "backend/routers/import_txn.py" ;;
    refs.py)             echo "backend/routers/refs.py" ;;
    reports.py)          echo "backend/routers/reports.py" ;;
    api_client.py)       echo "frontend/api_client.py" ;;
    app.py)              echo "frontend/app.py" ;;
    cost.py)             echo "frontend/pages/cost.py" ;;
    planning.py)         echo "frontend/pages/planning.py" ;;
    dashboard.py)        echo "frontend/pages/dashboard.py" ;;
    import_page.py)      echo "frontend/pages/import_page.py" ;;
    transactions.py)     echo "frontend/pages/transactions.py" ;;
    inbox.py)            echo "frontend/pages/inbox.py" ;;
    requirements.txt)    echo "requirements.txt" ;;
    docker-compose.yml)  echo "docker-compose.yml" ;;
    *)                   echo "" ;;
  esac
}

copied=0
rebuild_be=false
rebuild_fe=false

if [ $# -eq 0 ]; then
  echo "Использование: ./deploy.sh файл1 файл2 ..."
  exit 1
fi

for src in "$@"; do
  fname=$(basename "$src")
  dest=$(get_dest "$fname")

  if [ -z "$dest" ]; then
    echo "⚠️  $fname — не в маппинге, пропускаю"
    continue
  fi

  full_dest="$PROJECT_DIR/$dest"
  mkdir -p "$(dirname "$full_dest")"
  cp "$src" "$full_dest"
  echo "✅ $fname → $dest"
  copied=$((copied + 1))

  case "$dest" in
    backend/*|requirements.txt) rebuild_be=true ;;
    frontend/*) rebuild_fe=true ;;
  esac
done

if [ $copied -eq 0 ]; then
  echo "❌ Нет файлов для копирования"
  exit 1
fi

echo ""
echo "📦 Скопировано: $copied файл(ов)"

echo ""
echo -n "📝 Git commit? (y/n): "
read do_git
if [ "$do_git" = "y" ]; then
  cd "$PROJECT_DIR"
  git add .
  echo -n "Сообщение коммита: "
  read msg
  git commit -m "${msg:-update}"
  git push
  echo "✅ Запушено на GitHub"
fi

echo ""
if $rebuild_be && $rebuild_fe; then
  echo "🐳 Пересобираю backend + frontend..."
  docker-compose build --no-cache && docker-compose up -d
elif $rebuild_be; then
  echo "🐳 Пересобираю backend..."
  docker-compose build --no-cache backend && docker-compose up -d backend
elif $rebuild_fe; then
  echo "🐳 Пересобираю frontend..."
  docker-compose build --no-cache frontend && docker-compose up -d frontend
fi

echo "🎉 Готово!"
