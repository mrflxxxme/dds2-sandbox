#!/usr/bin/env bash
# Генерирует Mermaid ER-диаграмму из SQLAlchemy моделей backend/models/
# Использование: bash scripts/generate_er_diagram.sh > docs/ER_DIAGRAM.md
# Запуск через хук: post-commit или вручную при изменениях в backend/models/
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MODELS_DIR="$ROOT_DIR/backend/models"

if [ ! -d "$MODELS_DIR" ]; then
    echo "Error: $MODELS_DIR not found" >&2
    exit 1
fi

cat <<'EOF'
# ER-диаграмма DDS2

> Auto-generated from `backend/models/*.py` by `scripts/generate_er_diagram.sh`.
> Перегенерировать: `bash scripts/generate_er_diagram.sh > docs/ER_DIAGRAM.md`

```mermaid
erDiagram
EOF

# Извлекаем классы моделей и их FK
python3 <<'PYEOF'
import os
import re
from pathlib import Path

MODELS_DIR = Path(os.environ.get('MODELS_DIR', 'backend/models'))

class_pat = re.compile(r'^class\s+(\w+)\s*\(([^)]+)\)\s*:', re.MULTILINE)
table_pat = re.compile(r'__tablename__\s*=\s*[\'"](\w+)[\'"]')
fk_pat = re.compile(r'ForeignKey\s*\(\s*[\'"]([^\'"]+)[\'"]')
column_pat = re.compile(r'^\s+(\w+)\s*[:=]\s*(?:Mapped\[[^\]]+\]\s*=\s*)?mapped_column\s*\(\s*([^,)]+)', re.MULTILINE)

models = {}  # class_name -> {"table": str, "fields": [(name, type)], "fks": [target_table]}

for py_file in MODELS_DIR.glob('*.py'):
    if py_file.name == '__init__.py':
        continue
    text = py_file.read_text(errors='ignore')
    for m in class_pat.finditer(text):
        cls_name = m.group(1)
        # Только модели (наследуют Base или *Mixin)
        bases = m.group(2)
        if 'Base' not in bases and 'Mixin' not in bases:
            continue
        # Найти __tablename__ ниже
        block_start = m.end()
        next_class = class_pat.search(text, block_start)
        block_end = next_class.start() if next_class else len(text)
        block = text[block_start:block_end]

        tname_m = table_pat.search(block)
        if not tname_m:
            continue
        table = tname_m.group(1)

        fks = []
        for fk_m in fk_pat.finditer(block):
            target = fk_m.group(1).split('.')[0]
            fks.append(target)

        # Топ-5 полей
        fields = []
        for col_m in column_pat.finditer(block):
            fname = col_m.group(1)
            ftype = col_m.group(2).strip()
            if fname.startswith('_'):
                continue
            fields.append((fname, ftype))
            if len(fields) >= 5:
                break

        models[cls_name] = {"table": table, "fields": fields, "fks": fks}

# Печать сущностей
table_to_class = {v['table']: k for k, v in models.items()}

for cls, info in sorted(models.items()):
    table = info['table']
    print(f"    {table} {{")
    for fname, ftype in info['fields']:
        # Упрощаем тип
        ftype_short = ftype[:20].replace('"', '').replace("'", '')
        print(f"        {ftype_short} {fname}")
    print(f"    }}")

# Печать связей
print()
seen_rels = set()
for cls, info in sorted(models.items()):
    table = info['table']
    for fk_target in info['fks']:
        rel_key = (table, fk_target)
        if rel_key in seen_rels or fk_target == table:
            continue
        seen_rels.add(rel_key)
        # FK = many-to-one
        print(f"    {fk_target} ||--o{{ {table} : has")
PYEOF

echo '```'
echo ""
echo "## Статистика"
MODEL_COUNT=$(find "$MODELS_DIR" -maxdepth 1 -name "*.py" ! -name "__init__.py" | wc -l | tr -d ' ')
echo ""
echo "- Файлов моделей: $MODEL_COUNT"
echo "- Сгенерировано: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
