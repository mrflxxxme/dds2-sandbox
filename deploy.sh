#!/bin/zsh
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
    refs_frontend.py)    echo "frontend/pages/refs.py" ;;
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
needs_rebuild=false
changed_files=""

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
  changed_files="$changed_files ${fname%.py}"
  if [ "$fname" = "requirements.txt" ]; then
    needs_rebuild=true
  fi
done

if [ $copied -eq 0 ]; then
  echo "❌ Нет файлов для копирования"
  exit 1
fi

echo ""
echo "📦 Скопировано: $copied файл(ов)"
echo "🔄 Обновите страницу в браузере"

auto_msg="update:${changed_files}"
echo ""
echo -n "📝 Git push? (y/n): "
read do_git
if [ "$do_git" = "y" ]; then
  cd "$PROJECT_DIR"
  git add .
  git commit -m "$auto_msg"
  git push
  echo "✅ Запушено: $auto_msg"
fi

if $needs_rebuild; then
  echo ""
  echo "🐳 requirements.txt изменился — пересобираю..."
  docker-compose build --no-cache && docker-compose up -d
fi

echo "🎉 Готово!"
