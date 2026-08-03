---
name: env-bash-tooling-broken
description: In dds2 worktrees the Bash tool often has no grep/sed/tail/python — use Grep/Read/Glob tools instead of shell pipelines
metadata:
  type: project
---

В worktree-сессиях dds2 Bash-инструмент запускается с урезанным Git Bash: `grep`, `sed`, `tail`, `where`, `python` возвращают `command not found`, а `cmd.exe /c ...` падает с `fatal error - add_item ("C:\Program Files\Git", "/") failed`. Проверено 2026-08-02 в worktree `dds2-card-design-spec-8a2392`.

**Why:** Ревью схемы БД требует агрегатов по многим файлам (например, «посчитать alembic heads» = собрать все `revision`/`down_revision` и вычесть). Попытка сделать это одноразовым shell/python-скриптом стоила 4 провалившихся вызова подряд.

**How to apply:** Для любых выборок по репозиторию сразу брать Grep (`output_mode: content`, `-o`) / Glob / Read, не шелл. Если аналитика реально требует кода (граф ревизий, диффы множеств) — либо считать по выводу Grep вручную, либо честно записать в вердикт, что гейт (`alembic heads`, `alembic upgrade/downgrade`) не прогнан и нужен транскрипт от исполнителя, а не выдавать статический разбор за прогон. Перед тем как полагаться на это, один дешёвый пробный вызов не помешает — окружение может почениться.
