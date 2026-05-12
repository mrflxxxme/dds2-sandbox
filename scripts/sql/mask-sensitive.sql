-- ─── Маскировка чувствительных полей после восстановления прод-снимка ────────
-- Запускается из scripts/load-prod-snapshot.sh ПОСЛЕ pg_restore.
-- Цель: локалка не сможет:
--   а) дёргать прод WB / Telegram API ключами клиентов
--   б) выдать прод-пароль реальному пользователю
--   в) принять прод-инвайт
-- ─────────────────────────────────────────────────────────────────────────────

BEGIN;

-- 1. Fernet-зашифрованные API-ключи (WB Statistics/Content/Ads и т.д.)
--    encrypted_key NOT NULL → пишем заведомо-невалидный токен.
--    is_active = FALSE → бизнес-логика и scheduler-jobs обходят его стороной.
UPDATE integration_keys
SET encrypted_key = 'MASKED_FOR_LOCAL_DEV',
    is_active = FALSE;

-- 2. Пароли пользователей → bcrypt('localdev').
--    Логин под любым прод-email с паролем "localdev".
UPDATE users
SET password_hash = '$2b$12$KIXxPfQYCqQ/PIRfQYIWreLuVjvqUVHTPTbPzFJl3xJ9MFsK7sEhq';

-- 3. Активные инвайты → невалидные (чтобы случайные ссылки из прод-почты не сработали).
UPDATE project_invites
SET invite_token = 'LOCAL_DEV_INVALID_' || id::text;

COMMIT;

\echo '✅ Sensitive fields masked: integration_keys, users, project_invites.'
