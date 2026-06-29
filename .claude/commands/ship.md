---
description: "Отправка фичи DDS2: /verify → коммит → push dev → наблюдение за CI → /learn. Только по явному /ship (есть side-effect: push)."
---

# /ship — verify → commit → push → watch CI

Сшивает рутину после написания кода в одну команду. **Никогда не пушит красное.** Запускается только вручную по `/ship` (пушит в remote — side-effect).

## Шаги
1. **Verify.** Прогони `/verify` (полный). Если вердикт **WARNING** или **BLOCK** — **СТОП**, не коммить: выведи находки и спроси юзера. Коммит только при APPROVE / зелёных гейтах.

2. **mypy-гейт (анти-CI-трап).** Если менялся `backend/services/**` или `backend/models/**`:
   ```bash
   docker compose exec backend mypy backend/services backend/models
   ```
   Зелёный локальный pytest ≠ зелёный CI (CI гоняет mypy). Красный mypy — СТОП.

3. **Commit.** Conventional-префикс по сути изменений: `feat:` / `fix:` / `infra:` / `refactor:` / `test:` (русский текст ok). Документацию — в тот же коммит, что и код.

4. **Push.** `git push origin dev:dev` (ветка `dev`; деплой идёт `dev` → CI green → `main` → auto-deploy, не через SSH).

5. **Watch CI.** Дождись результата (gh установлен):
   ```bash
   gh run watch --exit-status || gh run list --branch dev --limit 1
   ```
   Красный CI — покажи лог упавшего job (`gh run view --log-failed`), предложи `/build-fix`.

6. **Learn.** Если diff трогал `backend/**` — прогони `/learn` (синхронизация документации/правил).

## Отчёт
```
SHIP
  Verify:  APPROVE / WARNING / BLOCK
  mypy:    OK / FAIL / SKIP
  Commit:  <hash> <type>: <subject>
  Push:    dev → origin OK / FAIL
  CI:      green / red (<run-url>)
  Learn:   done / skip
```

## Стоп-условия (не пушить)
- `/verify` не APPROVE.
- mypy красный.
- Незакоммиченные «мусорные» файлы в `git status` (временные `.report*`, `.v*.html` и т.п.) — сначала разобраться, не тащить в коммит.
