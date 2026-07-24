// DDS2 — сохранённый Workflow «review»: фан-аут профильных ревьюеров по diff.
// Запуск: Workflow({name:'review-deep'})  — для тяжёлого/ultracode-прогона.
// Повседневный лёгкий аналог без Workflow-tool: скилл /review (.claude/skills/review/SKILL.md).
//
// SOTA-приёмы здесь:
//  • cache-warmup (1→N): code-reviewer прогревает общий префикс (CLAUDE.md+rules+diff),
//    остальные читают кэш (0.1x) вместо полной цены за тот же префикс.
//  • явный {model:'opus'} на КАЖДОМ agent() — Workflow НЕ читает CLAUDE_CODE_SUBAGENT_MODEL,
//    топ-модель достигается только так (см. memory feedback_model_always_opus).
//  • structured-output по схеме → детерминированная агрегация вердикта в чистом JS.

export const meta = {
  name: 'review-deep',
  description: 'DDS2 фан-аут ревью по diff: профильные субагенты на Opus 4.8, cache-warmup, единый вердикт APPROVE/WARNING/BLOCK',
  phases: [
    { title: 'Scope', detail: 'git diff → какие ревьюеры нужны', model: 'opus' },
    { title: 'Review', detail: 'профильные ревьюеры на Opus, warmup', model: 'opus' },
  ],
}

// Агенты Workflow запускаются с cwd = корень репозитория, поэтому git-команды
// выполняются в текущей рабочей директории — без хардкода абсолютного пути.
const ROOT = 'корне репозитория DDS2 (твоя текущая рабочая директория)'

const SCOPE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['changedFiles', 'reviewers', 'rationale'],
  properties: {
    changedFiles: { type: 'array', items: { type: 'string' } },
    reviewers: {
      type: 'array',
      items: { type: 'string', enum: ['code-reviewer', 'frontend-reviewer', 'database-reviewer', 'security-reviewer', 'performance-optimizer', 'api-designer'] },
    },
    rationale: { type: 'string' },
  },
}

const FINDING_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['reviewer', 'verdict', 'critical', 'high', 'medium', 'findings'],
  properties: {
    reviewer: { type: 'string' },
    verdict: { type: 'string', enum: ['APPROVE', 'WARNING', 'BLOCK'] },
    critical: { type: 'integer', minimum: 0 },
    high: { type: 'integer', minimum: 0 },
    medium: { type: 'integer', minimum: 0 },
    findings: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        required: ['severity', 'file', 'issue'],
        properties: {
          severity: { type: 'string', enum: ['CRITICAL', 'HIGH', 'MEDIUM'] },
          file: { type: 'string' },
          line: { type: 'integer' },
          issue: { type: 'string' },
        },
      },
    },
  },
}

const MATRIX = [
  'код в backend/** или frontend-react/** → code-reviewer (всегда)',
  'frontend-react/** (.tsx/.ts) → + frontend-reviewer',
  'migrations/**, backend/models/**, *.sql, alembic → + database-reviewer',
  'backend/auth*, backend/rbac.py, backend/utils/crypto*, text(-SQL, пользовательский ввод → + security-reviewer',
  'backend/routers/**, новый endpoint, массовые выборки (.scalars().all()) → + performance-optimizer',
  'backend/routers/** + backend/schemas/** (контракт API изменился) → + api-designer',
].join('\n')

// ───────── Scope: что изменилось и кого звать ─────────
phase('Scope')
const scope = await agent(
  `Определи скоуп ревью DDS2. Выполни в ${ROOT}: \`git diff --staged --name-only && git diff --name-only\` и собери уникальные изменённые пути. По матрице выбери нужных ревьюеров (code-reviewer — всегда, если есть код):\n${MATRIX}\nВерни changedFiles, reviewers (подмножество перечисления), rationale (1-2 предложения почему такой набор).`,
  { label: 'scope', phase: 'Scope', schema: SCOPE_SCHEMA, model: 'opus' }
)

const files = (scope && scope.changedFiles) || []
if (!files.length) {
  log('diff пуст — нечего ревьюить')
  return { verdict: 'APPROVE', reason: 'no changes', reviews: [] }
}
let reviewers = (scope && scope.reviewers) || []
if (!reviewers.includes('code-reviewer')) reviewers = ['code-reviewer'].concat(reviewers)
log(`Скоуп: ${files.length} файлов → ревьюеры: ${reviewers.join(', ')}`)

const fileList = files.join(', ')
function reviewPrompt(r) {
  return `Ты субагент ${r} проекта DDS2. Отревьюй ТОЛЬКО изменения в этих файлах: ${fileList}.\nПрочитай \`git diff\` (и --staged) в ${ROOT} + окружающий код (не ревьюй в изоляции). Применяй свой профильный чеклист и iron-rules из CLAUDE.md.\nОтчёт ТОЛЬКО по находкам с уверенностью >80%. Верни reviewer="${r}", verdict (APPROVE/WARNING/BLOCK), critical/high/medium (счётчики), findings[] (severity, file, line, issue).`
}

// ───────── Review: warmup → остальные параллельно, все на Opus ─────────
phase('Review')
// cache-warmup: code-reviewer первым прогревает общий префикс
const warm = await agent(reviewPrompt('code-reviewer'),
  { label: 'review:code-reviewer', phase: 'Review', schema: FINDING_SCHEMA, model: 'opus', agentType: 'code-reviewer' })

const rest = reviewers.filter((r) => r !== 'code-reviewer')
const others = await parallel(rest.map((r) => () =>
  agent(reviewPrompt(r), { label: `review:${r}`, phase: 'Review', schema: FINDING_SCHEMA, model: 'opus', agentType: r })
))

const reviews = [warm].concat(others).filter(Boolean)

// ───────── Вердикт: детерминированная агрегация в JS ─────────
let crit = 0, high = 0, med = 0
for (const rv of reviews) { crit += rv.critical || 0; high += rv.high || 0; med += rv.medium || 0 }
const verdict = crit > 0 ? 'BLOCK' : high > 0 ? 'WARNING' : 'APPROVE'
log(`Вердикт: ${verdict} — CRITICAL ${crit} / HIGH ${high} / MEDIUM ${med}`)

return {
  verdict,
  totals: { critical: crit, high, medium: med },
  byReviewer: reviews.map((r) => `${r.reviewer}: ${r.verdict} (C${r.critical || 0}/H${r.high || 0}/M${r.medium || 0})`),
  findings: reviews.flatMap((r) => (r.findings || []).map((f) => Object.assign({ reviewer: r.reviewer }, f))),
  changedFiles: files,
}
