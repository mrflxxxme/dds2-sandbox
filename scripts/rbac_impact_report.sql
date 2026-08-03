-- Кто потеряет доступ после включения page-гейтов RBAC. ТОЛЬКО SELECT.
-- Каталог страниц скопирован из backend/rbac.py (ветка с design-tasks).
WITH catalog(page, added_at, section) AS (VALUES
  ('dashboard',          DATE '2026-03-26', NULL),
  ('import',             DATE '2026-03-26', 'finance'),
  ('txn',                DATE '2026-03-26', 'finance'),
  ('inbox',              DATE '2026-03-26', 'finance'),
  ('reports',            DATE '2026-03-26', 'finance'),
  ('cost',               DATE '2026-03-26', 'finance'),
  ('refs',               DATE '2026-03-26', 'finance'),
  ('salary',             DATE '2026-07-28', 'finance'),
  ('warehouse',          DATE '2026-07-30', 'warehouse'),
  ('assembly',           DATE '2026-03-26', 'warehouse'),
  ('assembly-analytics', DATE '2026-06-14', 'warehouse'),
  ('logistics',          DATE '2026-03-26', 'warehouse'),
  ('fbo',                DATE '2026-03-26', 'warehouse'),
  ('stocks',             DATE '2026-03-26', 'warehouse'),
  ('stock-analytics',    DATE '2026-03-26', 'warehouse'),
  ('measurements',       DATE '2026-07-30', 'warehouse'),
  ('barcode-labels',     DATE '2026-07-23', 'warehouse'),
  ('fbs',                DATE '2026-07-26', 'warehouse'),
  ('planning',           DATE '2026-03-26', 'orders'),
  ('container',          DATE '2026-03-26', 'orders'),
  ('supply-chain',       DATE '2026-04-09', 'supply'),
  ('funnel',             DATE '2026-03-26', 'sales'),
  ('reviews',            DATE '2026-07-30', 'sales'),
  ('card-exchange',      DATE '2026-08-02', 'sales'),
  ('ads-manager',        DATE '2026-07-23', 'sales'),
  ('ab-tests',           DATE '2026-07-23', 'sales'),
  ('design-tasks',       DATE '2026-08-03', 'sales'),
  ('trends',             DATE '2026-03-26', 'sales'),
  ('opiu',               DATE '2026-03-26', 'sales'),
  ('plan-fact',          DATE '2026-03-26', 'sales'),
  ('geography',          DATE '2026-03-26', 'sales'),
  ('ai-chat',            DATE '2026-04-07', 'ai'),
  ('monitoring',         DATE '2026-03-26', 'settings'),
  ('raw-data',           DATE '2026-07-12', 'settings'),
  ('project-settings',   DATE '2026-03-26', 'settings'),
  ('team',               DATE '2026-03-26', 'settings')
),
cat AS (
  SELECT page, added_at, section,
         page NOT IN ('salary','raw-data','project-settings','team') AS inheritable
  FROM catalog
),
-- Домены, закрываемые гейтом -> ключи каталога («достаточно любого»)
gated(domain, keys) AS (VALUES
  ('Воронка+Ценообразование (funnel/pricing)', ARRAY['funnel']),
  ('Управление рекламой (ads-manager)',        ARRAY['ads-manager']),
  ('Метрики и тренды (trends)',                ARRAY['trends']),
  ('Куда заказывают (geography)',              ARRAY['geography']),
  ('ОПИУ (opiu)',                              ARRAY['opiu']),
  ('Отзывы (reviews)',                         ARRAY['reviews']),
  ('Биржа карточек (card-exchange)',           ARRAY['card-exchange']),
  ('Замеры (measurements)',                    ARRAY['measurements']),
  ('Поставки FBO (fbo)',                       ARRAY['fbo']),
  ('Логистика/порталы ФФ (logistics)',         ARRAY['logistics']),
  ('FBS Wildberries (fbs)',                    ARRAY['fbs']),
  ('АБ-тесты фото (ab-tests)',                 ARRAY['ab-tests'])
),
m AS (
  SELECT pm.id, pm.project_id, pm.user_id, pm.role, pm.pages_updated_at,
         CASE WHEN pm.pages IS NULL OR pm.pages = '' THEN ARRAY[]::text[]
              WHEN jsonb_typeof(pm.pages::jsonb) <> 'array' THEN ARRAY[]::text[]
              ELSE ARRAY(SELECT jsonb_array_elements_text(pm.pages::jsonb)) END AS held
  FROM project_members pm
  WHERE pm.is_deleted = false AND pm.role IN ('editor','viewer')
),
mm AS (
  SELECT m.*, GREATEST(m.pages_updated_at::date, DATE '2026-03-26') AS cutoff FROM m
),
hs AS (
  SELECT mm.id, array_agg(DISTINCT c.section) FILTER (WHERE c.section IS NOT NULL) AS secs
  FROM mm JOIN cat c ON c.page = ANY(mm.held) GROUP BY mm.id
),
gr AS (
  SELECT mm.id, array_agg(c.page) AS gp
  FROM mm JOIN cat c ON c.inheritable AND c.added_at <= mm.cutoff GROUP BY mm.id
),
bl AS (
  SELECT mm.id,
         COALESCE(cardinality(gr.gp) > 0 AND NOT EXISTS (
           SELECT 1 FROM unnest(gr.gp) x WHERE NOT (x = ANY(mm.held))), false) AS is_blanket
  FROM mm LEFT JOIN gr ON gr.id = mm.id
),
eff AS (
  SELECT mm.id, mm.project_id, mm.user_id, mm.role, mm.pages_updated_at, mm.held,
         mm.held || COALESCE(ARRAY(
           SELECT c.page FROM cat c
           WHERE cardinality(mm.held) > 0
             AND mm.pages_updated_at IS NOT NULL
             AND NOT (c.page = ANY(mm.held))
             AND c.inheritable
             AND c.added_at > mm.cutoff
             AND (bl.is_blanket OR c.section = ANY(hs.secs))
         ), ARRAY[]::text[]) AS effective
  FROM mm LEFT JOIN bl ON bl.id = mm.id LEFT JOIN hs ON hs.id = mm.id
)
SELECT p.name AS project,
       COALESCE(NULLIF(TRIM(COALESCE(u.first_name,'')||' '||COALESCE(u.last_name,'')),''), u.username) AS who,
       eff.role,
       eff.pages_updated_at::date AS configured_at,
       ('design-tasks' = ANY(eff.effective)) AS gets_design_tasks,
       COALESCE(array_agg(g.domain ORDER BY g.domain) FILTER (WHERE g.domain IS NOT NULL), '{}') AS loses
FROM eff
JOIN users u ON u.id = eff.user_id
JOIN projects p ON p.id = eff.project_id AND p.is_deleted = false
LEFT JOIN gated g ON NOT (eff.effective && g.keys)
GROUP BY p.name, who, eff.role, eff.pages_updated_at, eff.effective
ORDER BY p.name, who;
