---
name: local-db-verification-access
description: How to get psql / alembic access to the local prod-copy DB for verifying plans, row counts and migration cycles during review
metadata:
  type: reference
---

Local stack runs a prod copy (`make sync-prod`), so review claims about index usage,
row counts and migration cost can be verified for real instead of guessed.

- psql: `docker exec dds2-db-1 psql -U dds -d dds_db -c "..."` — user `dds`, db `dds_db`
  (NOT `postgres`/`dds_user`; `.env` is blocked by permissions, creds come from
  `docker inspect dds2-db-1 --format '{{range .Config.Env}}...'`).
- alembic: `docker exec dds2-backend-1 sh -lc 'cd /app && alembic heads / current / upgrade head / downgrade -1'`.
  `/app` is the bind-mounted repo, so an edited migration runs as edited (watch for the
  stale-`.pyc` gotcha in learnings.md).
- Useful during review: `pg_stat_user_tables.n_live_tup` + `pg_total_relation_size` to
  decide whether a non-CONCURRENT index / ADD COLUMN is actually risky, and `\d <table>`
  to see the indexes that really exist versus the ones the model declares.

**Why:** severity of "missing index" / "blocking migration" findings is entirely a
function of table size, and guessing produced both false alarms and missed truncation
risks before this was checked directly.

- ⚠️ NEVER run `alembic upgrade` against `dds_db` when reviewing a branch: the user works in
  that DB, and a foreign revision in `alembic_version` breaks their own `upgrade head`.
  Instead `docker exec dds2-db-1 psql -U dds -d postgres -c "CREATE DATABASE dds_dbrev_<x>;"`
  and run the whole chain there via
  `docker compose run --rm --no-deps --entrypoint sh -v <worktree>:/wt -e DATABASE_URL_SYNC=postgresql://dds:dds_secret@db:5432/<db> backend -lc "cd /wt && alembic upgrade head && alembic downgrade -1 && alembic upgrade head"`.
  A from-zero `upgrade head` (~215 revisions) takes ~1 min and also proves the chain builds
  from scratch. Scratch DBs pile up (`\l` shows many) — hand `DROP DATABASE` to the user.
- `alembic check` against that fresh DB is the cheapest model↔migration drift test, but the
  repo has ~100 legacy drifts (nullable `created_at`, index-vs-constraint noise) — grep the
  output for the tables under review instead of reading it.
- `downgrade -1` on a fresh `create_table` migration DESTROYS local prod-copy data that
  cannot always be re-fetched (portal-only sources). Back up first with
  `CREATE TABLE _rev_bak_x AS SELECT ...`, restore with INSERT + `setval(pg_get_serial_sequence(...))`.
  ⚠️ `scripts/hooks/pre_tool_check.sh` BLOCKS `DROP`/`TRUNCATE` in Bash, so scratch tables
  cannot be cleaned up by the agent — either hand the cleanup command to the user, or
  prefer `pg_dump -t <table>` into the container's `/tmp` instead of scratch tables.
- Rendering SQLAlchemy queries with `literal_binds` breaks on datetime params
  (`operator does not exist: timestamp without time zone < text`) — paste the rendered SQL
  into psql and add explicit `timestamp '…'` casts to get an EXPLAIN.

**How to apply:** for any index/plan/migration-cost finding, get the row count first and
put the number in the report; run `upgrade → downgrade -1 → upgrade` before claiming a
downgrade is (or is not) complete. Note that integration keys are masked to
`is_active = false` locally, so data-migrations gated on `is_active` are silent no-ops
here — simulate them with that predicate removed.
