---
name: sweep-job-donor-trap
description: Scheduler sweep-jobs copied from draft_staleness_watch inherit two hidden traps (no Project.is_deleted filter, session held across Telegram HTTP) — check both on every new job
metadata:
  type: project
---

Каждый новый «обход всех проектов» джоб, списанный с `backend/scheduler/jobs/draft_staleness_watch.py`, проверять на две ловушки донора:

1. **Донор не фильтрует `Project.is_deleted`** (`select(Project).where(Project.id == pid)`) — и это безопасно ТОЛЬКО потому, что список проектов ему даёт `backend/scheduler/helpers.get_sync_project_ids`, который фильтрует `Project.is_deleted == False`. Джоб со своим собственным сбором project_ids копирует незащищённый паттерн и начинает слать по удалённым проектам вечно (поймано на Ф4 «Дизайн карточек», `design_notify._get_target_project_ids`).
2. **Донор держит сессию открытой вокруг `asyncio.gather(send_analytics_message...)`** — у него это один короткий gather по 1–3 чатам, поэтому сходит с рук. При копировании в джоб с ЦИКЛОМ отправок (per-task напоминания + SELECT на итерацию) получается `idle in transaction` на десятки минут = грабли из learnings.md (клин пула 2026-07-16).

**Why:** оба раза донор выглядит эталоном, а его безопасность держится на внешнем контексте, которого в новом джобе нет.

**How to apply:** при ревью нового `scheduler/jobs/*.py` — грепнуть, откуда берутся project_ids (есть ли `is_deleted`), и проверить, что `async with AsyncSessionLocal()` закрывается ДО любых HTTP-отправок (собрать данные → выйти из сессии → слать). Связано с [[design-transitions-canon]].
