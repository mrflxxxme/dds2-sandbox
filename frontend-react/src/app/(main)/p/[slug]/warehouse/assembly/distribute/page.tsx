'use client';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import { dropCommittedRows } from '@/lib/utils/assemblyDraftReconcile';
import { inTransitMap, subtractInTransitFromRows } from '@/lib/assembly/reconcileInTransit';
import { parseBoxSize, effectiveBoxesPerPallet, maxPalletHeightCm, packMonoPallets, MONO_MAX_PALLET_ARTICLES } from '@/lib/utils/boxPallet';
import { normalizeDraft, consolidatePrebookWholePallets, reconcileFillWithReserved, type NormalizeDraftCtx } from '@/lib/utils/normalizeDraft';
import { topUpPrebookLooseBoxes, releaseUnfixableLooseBoxes } from '@/lib/utils/assemblyRoundBoxes';
import { allocatePairs } from '@/lib/utils/assemblyPreview';
import { scopedNormalizeDraft, mergeDraftRows, directionKey } from '@/lib/utils/scopedNormalizeDraft';
import { palletFootprint, planTopUpBoxes, type TopUpCandidate } from '@/lib/assembly/prebookFootprint';
import { buildPrebookGroups } from '@/lib/assembly/buildPrebookGroups';
import { NEED_SUPPLY_DAYS, NEED_ANALYSIS_DAYS } from '@/lib/assembly/needParams';
import { applyAcceptanceRedistToPrebook } from '@/lib/assembly/prebookRedistribute';
import { buildCategoryOf, buildCompatClassOf, classLabelOf, inScopeOf } from '@/lib/assembly/categoryCompat';
import { subtractReserveFromArticles, restrictArticlesToFf, reservedTotal, carveScopeFromDraft, type DraftReserveMap } from '@/lib/assembly/draftReserve';
import { loadAutoSyncSharedData, runDraftAutoSync, getLastAutoSyncPassAt, markAutoSyncPass, WB_STOCKS_STALE_HOURS, type DraftSyncOutcome } from '@/lib/assembly/draftAutoSyncRunner';
import { returnPalletToPrebook } from '@/lib/assembly/draftDistribution';
import { Toast } from '@/components';
import TabLayout from '@/components/TabLayout';
import DraftPreview from './components/DraftPreview';
import UrgentShipPanel from './components/UrgentShipPanel';
import { WarehouseNeedView } from '../../analytics/components/WarehouseNeedView';
import { BoxMultiplicityView } from '../../box-multiplicity/BoxMultiplicityView';
import { PalletSizesView } from '../../pallet-sizes/PalletSizesView';
import { BoxWeightSetting } from './components/BoxWeightSetting';
import ForecastView from './components/ForecastView';
import PreDistributionView from './components/PreDistributionView';
import DraftMatrixView from './components/DraftMatrixView';
import DraftHistoryView from './components/DraftHistoryView';
import CategoryDraftModal from './components/CategoryDraftModal';
import PrebookView, { type PrebookGroup, type PrebookAcceptanceMark, type PrebookMonoPallet } from './components/PrebookView';
import { WarehouseExclusionSettings } from '../../analytics/components/WarehouseExclusionSettings';
import type {
    AssemblyDraft,
    AssemblyDraftDistribution,
    AssemblyDraftRow,
    PackageType,
    HandedUnit,
    StockNeedResponse,
    Warehouse,
    AcceptanceCheckPerItem,
    AcceptanceFlags,
    PalletCategoryCompat,
} from '@/types/api';

const AUTOSAVE_DEBOUNCE_MS = 5000;

type AssemblyTab = 'draft' | 'matrix' | 'history' | 'need' | 'box' | 'pallets' | 'forecast' | 'settings' | 'pre-dist' | 'prebook';
const TABS: { key: AssemblyTab; label: string }[] = [
    { key: 'draft', label: '📝 Черновик сборки' },
    { key: 'matrix', label: '✏️ Ручная раскладка' },
    { key: 'history', label: '🕘 История' },
    { key: 'need', label: '🏬 Потребность по складам' },
    { key: 'box', label: '📦 Кратность' },
    { key: 'pallets', label: '🚚 Паллеты' },
    { key: 'pre-dist', label: '🚚 Предраспределение' },
    { key: 'prebook', label: '🅿️ Предбронь' },
    { key: 'forecast', label: '📊 Прогноз / Локализация' },
    { key: 'settings', label: '⚙️ Настройки складов' },
];

/** Схлопнуть дубли строк по (nm_id, упаковка, баркод), оставляя первую.
 *  Баркод В КЛЮЧЕ обязателен (зеркало backend `_dedupe_rows`): одна WB-карточка
 *  (nm_id) может нести несколько баркодов (размерные варианты), а карточка без
 *  article_wb уходит в nm_id=0 — без баркода в ключе разные физические товары
 *  схлопнулись бы в один и при сохранении/коммите потерялись/уехали не туда. */
function dedupeRows(rows: AssemblyDraftRow[]): AssemblyDraftRow[] {
    const seen = new Set<string>();
    return rows.filter(r => {
        // as_is в ключе (зеркало backend `_dedupe_rows`): частичная «Оставить так»
        // и обычная строка того же SKU — разные строки, keep-first их не схлопывает.
        const k = `${r.nm_id}-${r.package_type || 'BOX'}-${r.barcode || ''}-${r.as_is ? 1 : 0}`;
        if (seen.has(k)) return false;
        seen.add(k);
        return true;
    });
}

/** 409 CAS-гварда update_draft: черновик изменила другая вкладка/окно. */
function isVersionConflict(e: unknown): boolean {
    return e instanceof Error && e.message.includes('DRAFT_VERSION_CONFLICT');
}

/** Единая страница «Сборка»: вкладки Черновик / Потребность по складам / Кратность /
 *  Паллеты. Черновик — синглтон проекта (getOrCreateCurrentDraft), редактор строк +
 *  предпросмотр и создание заявок на одном экране. */
export default function AssemblyDraftPage() {
    const params = useParams();
    const router = useRouter();
    const searchParams = useSearchParams();
    const slug = params.slug as string;

    // Активная вкладка ← из ?tab=. useSearchParams пуст на 1-м рендере — дефолт 'draft'.
    const tabParam = searchParams.get('tab');
    // Явный черновик ← из ?draft= (переключатель черновиков / категорийные черновики).
    // Без параметра — «текущий» singleton (get_or_create_current_draft, который
    // консолидирует ТОЛЬКО бесскоупные черновики — категорийные живут параллельно).
    const draftParam = searchParams.get('draft');
    const activeTab: AssemblyTab =
        tabParam === 'matrix' || tabParam === 'history' || tabParam === 'need' || tabParam === 'box' || tabParam === 'pallets' || tabParam === 'forecast' || tabParam === 'settings' || tabParam === 'pre-dist' || tabParam === 'prebook'
            ? tabParam
            : 'draft';

    const setTab = useCallback((key: string) => {
        const sp = new URLSearchParams(searchParams.toString());
        if (key === 'draft') sp.delete('tab');
        else sp.set('tab', key);
        const qs = sp.toString();
        router.replace(qs ? `?${qs}` : `/p/${slug}/warehouse/assembly/distribute`, { scroll: false });
    }, [searchParams, router, slug]);

    // Переключение черновика (?draft=): null — «текущий» основной (без параметра).
    const switchDraft = useCallback((id: number | null) => {
        const sp = new URLSearchParams(searchParams.toString());
        if (id == null) sp.delete('draft'); else sp.set('draft', String(id));
        const qs = sp.toString();
        router.replace(qs ? `?${qs}` : `/p/${slug}/warehouse/assembly/distribute`, { scroll: false });
    }, [searchParams, router, slug]);

    // ─── State ───────────────────────────────────────────────────────────
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);

    const [draft, setDraft] = useState<AssemblyDraft | null>(null);
    const [name, setName] = useState('');
    const [editingName, setEditingName] = useState(false);
    const [comment, setComment] = useState('');
    const [estimatedReadyDate, setEstimatedReadyDate] = useState<string>('');
    const [palletsCount, setPalletsCount] = useState<number>(1);
    const [palletWeightKg, setPalletWeightKg] = useState<number>(0);

    const [sourceWarehouseIds, setSourceWarehouseIds] = useState<number[]>([]);
    const [targetWarehouseNames, setTargetWarehouseNames] = useState<string[]>([]);
    const [rows, setRows] = useState<AssemblyDraftRow[]>([]);
    const [prebook, setPrebook] = useState<AssemblyDraftRow[]>([]);
    const [nmPpb, setNmPpb] = useState<Map<number, number | null>>(new Map());
    /** Кратность per (nm × ФФ-склад) — короб может отличаться по складам (22 Хамза / 30 Газпром). */
    const [nmPpbByWh, setNmPpbByWh] = useState<Map<number, Record<number, number>>>(new Map());
    const [nmBoxSize, setNmBoxSize] = useState<Map<number, string | null>>(new Map());
    const [nmMeta, setNmMeta] = useState<Map<number, { subject: string; brand: string }>>(new Map());
    const [palletOverrides, setPalletOverrides] = useState<Record<string, number>>({});
    const [geomState, setGeomState] = useState<'loading' | 'ready' | 'error'>('loading');
    const [coldStartShares, setColdStartShares] = useState<Record<string, number> | null>(null);
    const [handedUnits, setHandedUnits] = useState<HandedUnit[]>([]);
    const [newcomerNmIds, setNewcomerNmIds] = useState<Set<number>>(new Set());

    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
    const [stockNeed, setStockNeed] = useState<StockNeedResponse | null>(null);

    // Правила совместимости категорий на паллете + ручные override-категории
    // (эффективная категория = override ?? предмет WB). Best-effort загрузка.
    const [compatRules, setCompatRules] = useState<PalletCategoryCompat | null>(null);
    const [catOverrides, setCatOverrides] = useState<Record<string, string>>({});
    // Резерв стока ДРУГИМИ черновиками (barcode → {ff → qty}) + список черновиков
    // проекта для переключателя. Обновляется на смене черновика и после commit.
    const [draftsReserved, setDraftsReserved] = useState<DraftReserveMap>({});
    const [allDrafts, setAllDrafts] = useState<AssemblyDraft[]>([]);
    // Список черновиков ЗАГРУЖЕН хотя бы раз: до этого foreignScopeCategories
    // разово пуст — авто-синк матрицы стрельнул бы без чужих скоупов и
    // спланировал бы бесскоупному черновику чужие категории (ревью LOW).
    const [draftsLoaded, setDraftsLoaded] = useState(false);
    const [catModalOpen, setCatModalOpen] = useState(false);
    // Резерв ВСЕХ черновиков (без exclude текущего) — линза модалки «По категориям»:
    // она выбирает категории для НОВОГО черновика, срез/резерв текущего к ней не
    // относится. null до загрузки → фолбэк на draftsReserved.
    const [modalReserved, setModalReserved] = useState<DraftReserveMap | null>(null);

    // Приёмка WB для предброни (⌛ на карточках) — грузится при наличии предброни.
    const [prebookAcceptance, setPrebookAcceptance] = useState<Map<number, AcceptanceCheckPerItem>>(new Map());
    const [prebookAccLoading, setPrebookAccLoading] = useState(false);
    const prebookAccSigRef = useRef<string>('');
    const prebookAccFetchingRef = useRef(false);
    // Тик автообновления приёмки (10 мин = TTL пер-баркод кэша бэка): входит в
    // сигнатуру проверки → бейджи не протухают в долгой сессии без изменений.
    const [accRefreshTick, setAccRefreshTick] = useState(0);
    // Whitelist складов, куда можно делать предзаявку без лимита приёмки (для кнопки «Создать предзаявку»).
    const [preorderWbs, setPreorderWbs] = useState<Set<string>>(new Set());
    const [preorderLoaded, setPreorderLoaded] = useState(false);
    const redistBusyRef = useRef(false);
    const redistSigRef = useRef<string>('');

    const lastSavedJsonRef = useRef<string>('');
    const initialLoadRef = useRef(false);
    // Версия черновика (updated_at последнего ПРИМЕНЁННОГО состояния) — CAS фоновых
    // писателей: PUT со stale-версией сервер отбивает 409 вместо молчаливого
    // full-replace (прод-кейс: вторая вкладка воскресила очищенный черновик).
    const draftVersionRef = useRef<string>('');
    // Взведённый таймер автосейва — чтобы конкурирующие операции (carve категорий)
    // могли его погасить, а не ловить PUT полного до-carve снимка поверх очистки.
    const autosaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    // id текущего черновика (синглтон). null до первой загрузки.
    const draftId = draft?.id ?? null;

    const showToast = useCallback((message: string, type: 'success' | 'error') => setToast({ message, type }), []);

    // Провенанс «из предброни»: набор `${nm_id}::${wb}`, чей контент попал в rows из
    // предброни. Живёт в ref (не в rows — normalizeDraft пересобирает строки и стёр бы
    // флаг), персистится в distribution.prebook_origin. Обновляется хендлерами prebook→
    // draft ПЕРЕД updateAssemblyDraft (buildDistribution читает ref), сбрасывается на re-fill.
    const prebookOriginRef = useRef<Set<string>>(new Set());

    // РУЧНЫЕ SKU (✋, ставит матрица-редактор): страница их НЕ правит, но ОБЯЗАНА
    // протаскивать в каждый свой PUT — снимок buildDistribution без manual_nms
    // затирал флаги, и следующий авто-синк матрицы «вычищал» гвардед-новинки,
    // размеченные руками, в 0 (ловили живьём: кастрюли, план 80 шт → 0).
    const manualNmsRef = useRef<number[]>([]);

    // Сужение self-heal до тронутых направлений `${pkg}::${wb}`, привязанное к версии
    // черновика (`updated_at`). Пер-направленческий хендлер ставит scope ПЕРЕД applyDraft;
    // self-heal читает его ТОЛЬКО если версия совпала → нормализует лишь тронутое (пустой
    // набор = вычитающая операция «На ФФ»/«Удалить» → no-op). На любую иную версию (загрузка,
    // внешний reload, PUT самого self-heal) scope не совпадёт → ПОЛНЫЙ проход (сеть
    // безопасности инварианта). additive-хендлер после своего PUT кладёт scope на новую
    // версию, subtractive — пустой scope, чтобы трейлинг-проход self-heal не рескан-нул всё.
    // fromSync: версия записана АВТО-синком с расчётом (не ручным действием) — такой
    // скоуп гасит только self-heal, но НЕ консолидацию по приёмке (синк может слить
    // хвосты в ≥1 целую паллету в предброни — промотировать после него ЕСТЬ что;
    // прод 2026-07-22: 5 паллет апл→Екб застряли в предброни при открытом приёме).
    const healScopeRef = useRef<{ ts: string; only: Set<string>; fromSync?: boolean } | null>(null);

    // ─── Apply a draft payload into state (initial load + reload after commit) ─
    const applyDraft = useCallback((d: AssemblyDraft) => {
        setDraft(d);
        setName(d.name);
        setComment(d.comment || '');
        setEstimatedReadyDate(d.distribution.estimated_ready_date || '');
        setPalletsCount(d.distribution.pallets_count || 1);
        setPalletWeightKg(d.distribution.pallet_weight_kg || 0);
        setSourceWarehouseIds(d.distribution.source_warehouse_ids || []);
        setTargetWarehouseNames(d.distribution.target_warehouse_names || []);
        setRows(dedupeRows(d.distribution.rows || []));
        setColdStartShares(d.distribution.cold_start_shares || null);
        setHandedUnits(d.distribution.handed_units || []);
        setPrebook(d.distribution.prebook || []);
        prebookOriginRef.current = new Set(d.distribution.prebook_origin || []);
        manualNmsRef.current = d.distribution.manual_nms || [];
        setNewcomerNmIds(new Set(d.newcomer_nm_ids || []));
        lastSavedJsonRef.current = JSON.stringify(d.distribution);
        draftVersionRef.current = d.updated_at;
    }, []);

    // ─── Load current draft (singleton) + reference data ─────────────────
    useEffect(() => {
        let cancelled = false;
        const load = async () => {
            setLoading(true);
            setError(null);
            try {
                const [draftResp, whs, stockNeedResp] = await Promise.all([
                    draftParam ? api.getAssemblyDraft(Number(draftParam)) : api.getOrCreateCurrentDraft(),
                    api.getWarehouses(),
                    // Флаги ТЕ ЖЕ, что у матрицы черновика (DraftMatrixView): loc-opt
                    // (гео-привязка спроса по speed-карте вместо bench по складу-источнику —
                    // рвёт петлю «пусто→заказы уезжают в ЦФО→need=0») + only_available
                    // (greedy-cap до целевой локализации с весами воришек). Иначе панель
                    // «Добавить из потребности» строила target по сырому bench без всей
                    // механики распределения (аудит 2026-07-09).
                    api.getStockNeed(NEED_SUPPLY_DAYS, NEED_ANALYSIS_DAYS, 'actual', true, true, 0).catch(() => null) as Promise<StockNeedResponse | null>,
                ]);
                if (cancelled) return;
                applyDraft(draftResp);
                setWarehouses(whs);
                setStockNeed(stockNeedResp);
                initialLoadRef.current = true;

                // «Один мир»: строки черновика сверяем с УЖЕ ЕДУЩИМ в активных
                // заявках (вкл. PRE_DISTRIBUTED — резерв машины). Заявки внешних
                // потоков (предраспределение машины, ручные) dropCommittedRows не
                // видит → черновик предлагал дубль уже едущего (кейс «швабры апл»
                // 2026-07-10). Вычитаем per (nm, WB-склад); state → автосейв
                // зафиксирует очистку на сервере. Сбой запроса — не блокируем.
                try {
                    const dist = draftResp.distribution;
                    const nmIds = [...new Set([...(dist.rows || []), ...(dist.prebook || [])].map(r => r.nm_id).filter(Boolean))];
                    if (nmIds.length > 0) {
                        const transitResp = await api.getAssemblyInTransit(nmIds);
                        if (cancelled) return;
                        const transit = inTransitMap(transitResp.items || []);
                        if (transit.size > 0) {
                            const recRows = subtractInTransitFromRows(dedupeRows(dist.rows || []), transit);
                            // Предбронь сверяем ПОСЛЕ заявок: остаток transit после rows.
                            const recPrebook = subtractInTransitFromRows(dist.prebook || [], recRows.remainingTransit);
                            if (recRows.changed || recPrebook.changed) {
                                // Вычет персистится СРАЗУ, не автосейвом через 5с: self-heal стартует
                                // раньше и берёт базу свежим GET — не-вычтенная серверная версия
                                // вернулась бы его PUT'ом, а смена rows сбросила бы таймер автосейва
                                // (вычет не сохранялся никогда; коммит отгружал дубль уже едущего).
                                // Дельта-гейт бэка усечение пропускает (режется только прирост).
                                try {
                                    const persisted = await api.updateAssemblyDraft(draftResp.id, {
                                        distribution: { ...dist, rows: recRows.rows, prebook: recPrebook.rows },
                                        base_updated_at: draftResp.updated_at,
                                    });
                                    if (cancelled) return;
                                    applyDraft(persisted);
                                } catch {
                                    // PUT не прошёл — старое поведение: локальный стейт + автосейв.
                                    setRows(recRows.rows);
                                    setPrebook(recPrebook.rows);
                                }
                                const units = recRows.subtractedUnits + recPrebook.subtractedUnits;
                                const skus = new Set([...recRows.touchedNm, ...recPrebook.touchedNm]).size;
                                showToast(`⚠ Вычтено уже едущее в заявках: ${formatNumber(units, 0)} шт · ${formatNumber(skus, 0)} SKU`, 'success');
                            }
                        }
                    }
                } catch { /* best-effort: черновик остаётся как есть */ }
            } catch (e: unknown) {
                if (!cancelled) setError(e instanceof Error ? e.message : 'Ошибка загрузки черновика');
            } finally {
                if (!cancelled) setLoading(false);
            }
        };
        load();
        return () => { cancelled = true; };
    }, [applyDraft, showToast, draftParam]);

    // Кратность короба + размер + предмет/бренд per nm (для редактора и предпросмотра).
    const loadGeometry = useCallback((cancelledRef?: { current: boolean }) => {
        api.getBoxMultiplicity()
            .then(resp => {
                if (cancelledRef?.current) return;
                const m = new Map<number, number | null>();
                const byWh = new Map<number, Record<number, number>>();
                const sizes = new Map<number, string | null>();
                const meta = new Map<number, { subject: string; brand: string }>();
                for (const r of resp.items) {
                    let ppb: number | null = null;
                    if (r.box_qty_override && r.box_qty_override > 0 && r.use_box_multiplicity) {
                        ppb = r.box_qty_override;
                        // override глобальный — per-ФФ карта не нужна (везде одинаково).
                    } else {
                        let best = 0;
                        let perWh: Record<number, number> | null = null;
                        for (const p of r.per_warehouse) {
                            if (p.box_qty && p.box_qty > 0 && p.use_box_multiplicity) {
                                if (best === 0 || p.box_qty < best) best = p.box_qty;
                                (perWh ??= {})[p.warehouse_id] = p.box_qty;
                            }
                        }
                        ppb = best > 0 ? best : null;
                        // Кратность может отличаться по складам (22 на Хамзе / 30 на Газпроме):
                        // расчёт порций обязан мерить коробом СВОЕГО ФФ, иначе min резал бы
                        // физически целые коробы чужого склада.
                        if (perWh) byWh.set(r.nm_id, perWh);
                    }
                    let boxSize: string | null = null;
                    let bestStock = -1;
                    for (const p of r.per_warehouse) {
                        if (!p.box_size || !parseBoxSize(p.box_size)) continue;
                        if (p.rf_stock > bestStock) { boxSize = p.box_size; bestStock = p.rf_stock; }
                    }
                    m.set(r.nm_id, ppb);
                    sizes.set(r.nm_id, boxSize);
                    meta.set(r.nm_id, { subject: r.subject || '', brand: r.brand || '' });
                }
                setNmPpb(m);
                setNmPpbByWh(byWh);
                setNmBoxSize(sizes);
                setNmMeta(meta);
                setGeomState('ready');
            })
            .catch(() => {
                if (cancelledRef?.current) return;
                // Сбой РЕФЕТЧА (фокус/возврат с «Кратности») при уже загруженных картах
                // не роняет страницу в 'error': со stale-геометрией всё работает, а
                // 'error' молча убивал кнопки предброни до следующего удачного фокуса
                // (прод-кейс «Дозабить из Газпром не реагирует»). Первый лоад — честный error.
                setGeomState(s => (s === 'ready' ? s : 'error'));
            });
    }, []);

    useEffect(() => {
        const cancelled = { current: false };
        loadGeometry(cancelled);
        return () => { cancelled.current = true; };
    }, [loadGeometry]);

    // Возврат с вкладки «Кратность» → перечитать кратности: юзер только что их правил,
    // а карта nmPpb иначе живёт до F5 — расчёт «не видел» свежезаданную кратность
    // (симптом: SKU с только что указанной кратностью всё ещё «без кратности»/россыпью).
    const prevTabRef = useRef<AssemblyTab>(activeTab);
    useEffect(() => {
        if (prevTabRef.current === 'box' && activeTab !== 'box') loadGeometry();
        prevTabRef.current = activeTab;
    }, [activeTab, loadGeometry]);

    // Кратность могли править в ДРУГОЙ вкладке браузера (черновик и «Кратность» открыты
    // порознь) — там нет внутри-аппного перехода таба, и карта nmPpb жила бы до F5.
    // Перечитываем геометрию при возврате фокуса/видимости. geomState остаётся 'ready' —
    // без мигания; бэкенд get_box_multiplicity не кэшируется, GET дешёвый.
    useEffect(() => {
        // Возврат таба даёт и visibilitychange, и focus — коалесцируем, чтобы не слать GET дважды.
        let last = 0;
        const refresh = () => {
            if (document.visibilityState !== 'visible') return;
            const now = Date.now();
            if (now - last < 500) return;
            last = now;
            loadGeometry();
        };
        window.addEventListener('visibilitychange', refresh);
        window.addEventListener('focus', refresh);
        return () => {
            window.removeEventListener('visibilitychange', refresh);
            window.removeEventListener('focus', refresh);
        };
    }, [loadGeometry]);

    useEffect(() => {
        let cancelled = false;
        api.getPalletBoxesBySize()
            .then(ov => { if (!cancelled) setPalletOverrides(ov || {}); })
            .catch(() => { /* best-effort */ });
        // Правила совместимости категорий + override-категории — для классов паллет.
        api.getPalletCategoryCompat()
            .then(r => { if (!cancelled) setCompatRules(r); })
            .catch(() => { /* best-effort: без правил классы = '*' (микс как раньше) */ });
        api.getCategoryOverrides()
            .then(m => { if (!cancelled) setCatOverrides(m || {}); })
            .catch(() => { /* best-effort */ });
        return () => { cancelled = true; };
    }, []);

    // Резерв стока другими черновиками + список черновиков (переключатель в шапке).
    const refreshReserved = useCallback(async (cancelledRef?: { current: boolean }) => {
        try {
            const [res, drafts] = await Promise.all([
                api.getDraftsReserved(draftId ?? undefined),
                api.listAssemblyDrafts(),
            ]);
            if (cancelledRef?.current) return;
            setDraftsReserved(res.reserved || {});
            setAllDrafts(drafts || []);
            setDraftsLoaded(true);
        } catch { /* best-effort: без резерва работаем как раньше */ }
    }, [draftId]);

    useEffect(() => {
        if (!draftId) return;
        const c = { current: false };
        refreshReserved(c);
        return () => { c.current = true; };
    }, [draftId, refreshReserved]);

    // ─── Категории: эффективная категория, классы совместимости, скоуп ────
    const categoryOf = useMemo(() => buildCategoryOf(catOverrides, (nm) => nmMeta.get(nm)?.subject), [catOverrides, nmMeta]);
    const classOf = useMemo(() => buildCompatClassOf(compatRules, categoryOf), [compatRules, categoryOf]);
    const classLabel = useCallback((cls: string) => classLabelOf(cls, compatRules), [compatRules]);
    const categoryScope = useMemo(() => draft?.distribution.category_scope ?? null, [draft]);
    const scopeFfId = draft?.distribution.scope_ff_id ?? null;
    const isScoped = !!(categoryScope && categoryScope.length > 0);
    const inScope = useMemo(() => inScopeOf(categoryScope, categoryOf), [categoryScope, categoryOf]);
    // Категории живых ЧУЖИХ категорийных черновиков — владение категорией:
    // бесскоупный черновик их не планирует (матрица получает пропом).
    const foreignScopeCategories = useMemo(
        () => allDrafts.filter(d => d.id !== draftId).flatMap(d => d.distribution.category_scope || []),
        [allDrafts, draftId],
    );

    // ЕДИНАЯ точка коррекции доступного ФФ: резерв других черновиков вычтен,
    // ФФ-ограничение скоупа применено. freeByNm / предбронь / пулы дозабора /
    // информ-блоки наследуют автоматически. Показ стока (rf_stocks.stock) не трогаем.
    const effArticles = useMemo(() => {
        let arts = subtractReserveFromArticles(stockNeed?.articles ?? [], draftsReserved);
        arts = restrictArticlesToFf(arts, scopeFfId);
        return arts;
    }, [stockNeed, draftsReserved, scopeFfId]);
    const reservedUnits = useMemo(() => reservedTotal(draftsReserved), [draftsReserved]);

    // Кратность для «Срочно к отправке» — стабильная ссылка: инлайн-лямбда
    // пересчитывала бы buildUrgentShip на каждом рендере страницы (ревью MEDIUM).
    const urgentPpbOf = useCallback((nm: number) => nmPpb.get(nm), [nmPpb]);

    // Резерв других черновиков per nm (для «Срочно к отправке»: ffFree честный).
    // draftsReserved ключуется баркодом — мапим на nm через статьи потребности.
    const urgentReservedByNm = useMemo(() => {
        const bcToNm = new Map<string, number>();
        for (const a of stockNeed?.articles ?? []) if (a.barcode) bcToNm.set(a.barcode, a.nm_id);
        const m = new Map<number, number>();
        for (const [bc, byFf] of Object.entries(draftsReserved)) {
            const nm = bcToNm.get(bc);
            if (nm == null) continue;
            const total = Object.values(byFf).reduce((s, v) => s + (v || 0), 0);
            if (total > 0) m.set(nm, (m.get(nm) || 0) + total);
        }
        return m;
    }, [stockNeed, draftsReserved]);

    // Статьи для модалки «По категориям»: БЕЗ restrictArticlesToFf текущего черновика
    // (его scope_ff_id занулял «свободно» чужих ФФ — категории «исчезали» из списка)
    // и с резервом ВСЕХ черновиков, включая текущий (новый черновик — отдельный).
    const modalArticles = useMemo(
        () => subtractReserveFromArticles(stockNeed?.articles ?? [], modalReserved ?? draftsReserved),
        [stockNeed, modalReserved, draftsReserved],
    );
    const openCatModal = useCallback(() => {
        setCatModalOpen(true);
        setModalReserved(null);
        void api.getDraftsReserved(undefined)
            .then(r => setModalReserved(r.reserved || {}))
            .catch(() => { /* фолбэк — draftsReserved (без резерва текущего) */ });
    }, []);

    // ─── Страничный авто-синк ВСЕХ черновиков (заход + раз в час) ─────────
    // Наполнение каждого черновика приводится к живому расчёту headless-раннером
    // (та же цепочка, что у матрицы): категорийные по очереди (держат резерв) →
    // главный от остатка. Ручные решения (✋, rows-ячейки prebook_origin) священны,
    // предбронь пересобирается целиком. Гард свежести: остатки WB протухли →
    // синк останавливается и говорит об этом, а не синкает к неправде.
    const [pageSync, setPageSync] = useState<{ running: boolean; lastAt: number | null; note: string | null }>({ running: false, lastAt: null, note: null });
    const pageSyncBusyRef = useRef(false);
    const runAllDraftsSync = useCallback(async (trigger: 'load' | 'hourly' | 'focus') => {
        if (pageSyncBusyRef.current) return;
        // Гейт повторов — МОДУЛЬНЫЙ маркер, действует и на «заход»: каждый вход на
        // страницу запускал полный проход с PUT'ами по всем черновикам и выедал
        // write-бакет — входной запрос страницы ловил 429 (прод 2026-07-20).
        // Свежесинканное = актуальное; час/фокус догонят.
        if (Date.now() - getLastAutoSyncPassAt() < 30 * 60_000) {
            if (getLastAutoSyncPassAt() > 0) setPageSync((s) => ({ ...s, lastAt: s.lastAt ?? getLastAutoSyncPassAt() }));
            return;
        }
        if (document.visibilityState === 'hidden') return;
        pageSyncBusyRef.current = true;
        setPageSync((s) => ({ ...s, running: true }));
        try {
            // Заход: переиспользуем то, что страница уже загрузила (stockNeed +
            // геометрия) — ноль дублей тяжёлых GET. Часовой тик: потребность свежая,
            // геометрия страницы актуальна и так (рефетч на фокус).
            const geomReuse = geomState === 'ready'
                ? { nmPpb, nmPpbByWh, nmBoxSize, palletOverrides }
                : null;
            const shared = await loadAutoSyncSharedData({
                stockNeed: trigger === 'load' ? stockNeed : null,
                geometry: geomReuse,
            });
            // null-штамп = зеркало остатков пусто/поле не отдано — это ХУДШИЙ кейс,
            // не «свежий»: fail-closed, синк к нулевому WB-стоку хуже пропуска (ревью MEDIUM).
            if (shared.wbStocksAgeHours == null || shared.wbStocksAgeHours > WB_STOCKS_STALE_HOURS) {
                // Гейт повторов двигаем и на стоп-пути: иначе каждый focus заново
                // качал бы shared-данные при устойчиво протухших остатках (ревью LOW).
                markAutoSyncPass();
                const ageNote = shared.wbStocksAgeHours == null
                    ? '⚠ Возраст остатков WB неизвестен (зеркало пусто?) — авто-синк остановлен'
                    : `⚠ Остатки WB не обновлялись ~${formatNumber(shared.wbStocksAgeHours, 0)} ч — авто-синк остановлен (расчёт ехал бы по старым данным)`;
                setPageSync({ running: false, lastAt: Date.now(), note: ageNote });
                return;
            }
            const drafts = await api.listAssemblyDrafts();
            const ordered = [
                ...drafts.filter((d) => (d.distribution.category_scope?.length ?? 0) > 0),
                ...drafts.filter((d) => (d.distribution.category_scope?.length ?? 0) === 0),
            ];
            const outcomes: DraftSyncOutcome[] = [];
            for (const d of ordered) {
                // Черновик, открытый в «Ручной раскладке», матрица синкает сама —
                // не воюем с её локальным стейтом.
                if (activeTab === 'matrix' && d.id === draftId) continue;
                // Владение категорией: категории живых ЧУЖИХ категорийных черновиков
                // бесскоупный черновик не планирует (дедуп по резерву ловил только
                // занятый ФФ-сток — SKU с available=0 консервировался в обоих).
                const foreignScopes = new Set(
                    drafts.filter((x) => x.id !== d.id).flatMap((x) => x.distribution.category_scope || []),
                );
                const o = await runDraftAutoSync(d, shared, categoryOf, classOf, foreignScopes);
                outcomes.push(o);
                if (o.status === 'synced' && o.updated && o.draftId === draftId) {
                    healScopeRef.current = { ts: o.updated.updated_at, only: new Set(), fromSync: true };
                    applyDraft(o.updated);
                }
                // Пейсинг write-бакета: пауза после реального PUT — серия по N
                // черновикам не должна съедать лимит одним залпом.
                if (o.status === 'synced') await new Promise((res) => setTimeout(res, 2000));
            }
            markAutoSyncPass();
            const synced = outcomes.filter((o) => o.status === 'synced');
            const accFailed = outcomes.filter((o) => o.reason === 'acceptance-failed');
            const errs = outcomes.filter((o) => o.reason === 'error');
            setPageSync({
                running: false,
                lastAt: Date.now(),
                note: accFailed.length > 0
                    ? '⚠ Приёмка WB недоступна — часть черновиков не синкована (повтор через час)'
                    : errs.length > 0
                        ? `⚠ Синк не прошёл: ${errs.map((o) => `«${o.name}»`).join(', ')} (повтор через час)`
                        : null,
            });
            if (synced.length > 0) {
                showToast(`⟳ Авто-синк с потребностью: ${synced.map((o) => `«${o.name}» ${formatNumber(o.before ?? 0, 0)} → ${formatNumber(o.after ?? 0, 0)} шт`).join(' · ')}`, 'success');
                void refreshReserved();
            }
        } catch (e) {
            setPageSync({ running: false, lastAt: Date.now(), note: '⚠ Авто-синк не удался — повтор через час' });
            void e;
        } finally {
            pageSyncBusyRef.current = false;
            setPageSync((s) => ({ ...s, running: false }));
        }
    }, [activeTab, draftId, categoryOf, classOf, applyDraft, refreshReserved, showToast, stockNeed, geomState, nmPpb, nmPpbByWh, nmBoxSize, palletOverrides]);
    // Заход на страницу: один запуск после готовности черновика и геометрии.
    const pageSyncKickedRef = useRef(false);
    useEffect(() => {
        if (pageSyncKickedRef.current || loading || geomState !== 'ready' || !draftId) return;
        pageSyncKickedRef.current = true;
        void runAllDraftsSync('load');
    }, [loading, geomState, draftId, runAllDraftsSync]);
    // Часовой тик + догон при возврате фокуса (30-мин гейт внутри). Таймер живёт
    // на ref: deps-версия пересоздавала интервал при каждой смене вкладки/черновика
    // и отсчёт часа никогда не добегал (ревью MEDIUM).
    const runAllDraftsSyncRef = useRef(runAllDraftsSync);
    useEffect(() => { runAllDraftsSyncRef.current = runAllDraftsSync; }, [runAllDraftsSync]);
    useEffect(() => {
        const id = setInterval(() => { void runAllDraftsSyncRef.current('hourly'); }, 3_600_000);
        const onFocus = () => { void runAllDraftsSyncRef.current('focus'); };
        window.addEventListener('focus', onFocus);
        return () => { clearInterval(id); window.removeEventListener('focus', onFocus); };
    }, []);

    // ─── Build current distribution snapshot ─────────────────────────────
    const buildDistribution = useCallback((): AssemblyDraftDistribution => ({
        source_warehouse_ids: sourceWarehouseIds,
        target_warehouse_names: targetWarehouseNames,
        rows,
        pallets_count: palletsCount,
        pallet_weight_kg: palletWeightKg,
        estimated_ready_date: estimatedReadyDate || null,
        cold_start_shares: coldStartShares,
        handed_units: handedUnits,
        prebook,
        prebook_origin: [...prebookOriginRef.current],
        // ✋-флаги матрицы: страница их не меняет, но обязана нести в каждом PUT.
        manual_nms: manualNmsRef.current,
        // Скоуп категорийного черновика ОБЯЗАН переезжать в каждый снимок —
        // иначе первый же автосейв стёр бы его из JSONB.
        category_scope: categoryScope,
        scope_ff_id: scopeFfId,
    }), [sourceWarehouseIds, targetWarehouseNames, rows, palletsCount, palletWeightKg, estimatedReadyDate, coldStartShares, handedUnits, prebook, categoryScope, scopeFfId]);

    // ─── Нормализатор инварианта «целые коробы + целые паллеты» ───────────
    // Контекст из текущего состояния страницы: геометрия (ppb/размер), округа,
    // ─── «↩ Вернуть паллету в предбронь» (отмена «Дозабить»/«Оставить так») ──
    const handleReturnPalletToPrebook = useCallback(async (args: { ffId: number; wb: string; pkg: PackageType; items: { nmId: number; units: number }[] }) => {
        if (!draftId) return;
        const res = returnPalletToPrebook(rows, prebook, args.ffId, args.wb, args.pkg, args.items);
        if (!res) { showToast('Возвращать нечего — строки уже изменились, обновите страницу', 'error'); return; }
        // Снимок провенанса: при упавшем PUT удалённые ключи возвращаются, иначе
        // следующий успешный PUT персистил бы прун без самого возврата (ревью LOW).
        const originSnap = new Set(prebookOriginRef.current);
        try {
            // Провенанс: ячейка без остатка в строках больше не «из предброни» —
            // авто-синк снова владеет ею (вернётся в строки, только если расчёт
            // сам соберёт целую паллету).
            const hasCellLeft = (nm: number) => res.rows.some(r => r.nm_id === nm && (r.tgt?.[args.wb] || 0) > 0);
            for (const it of args.items) {
                if (!hasCellLeft(it.nmId)) prebookOriginRef.current.delete(`${it.nmId}::${args.wb}`);
            }
            const targetNames = Array.from(new Set(res.rows.flatMap(r => Object.keys(r.tgt))));
            const sourceIds = Array.from(new Set(res.rows.flatMap(r => Object.keys(r.src).map(Number).filter(n => Number.isFinite(n) && n > 0))));
            const updated = await api.updateAssemblyDraft(draftId, {
                distribution: { ...buildDistribution(), rows: res.rows, prebook: res.prebook, source_warehouse_ids: sourceIds, target_warehouse_names: targetNames },
                event: { event_type: 'MATRIX_EDIT', summary: `↩ Паллета возвращена в предбронь: «${args.wb}» — ${formatNumber(res.returnedUnits, 0)} шт` },
            });
            healScopeRef.current = { ts: updated.updated_at, only: new Set() }; // вычитающая → self-heal no-op
            applyDraft(updated);
            showToast(`↩ Возвращено в предбронь: «${args.wb}» — ${formatNumber(res.returnedUnits, 0)} шт (коробы снова ждут в предброни)`, 'success');
        } catch (e) {
            prebookOriginRef.current = originSnap;
            showToast(e instanceof Error ? e.message : 'Ошибка возврата в предбронь', 'error');
        }
    }, [draftId, rows, prebook, buildDistribution, applyDraft, showToast]);

    // свободный ФФ (доступно − уже в черновике) для добивки коробов вверх. Новинки
    // cold-start (ppb=null) — россыпь (исключение), их не палетизируем и не добиваем.
    const buildNormalizeCtx = useCallback((freshRows: AssemblyDraftRow[]): NormalizeDraftCtx => {
        const inDraft: Record<number, Record<number, number>> = {};
        for (const r of freshRows) {
            const m = (inDraft[r.nm_id] ??= {});
            for (const [ff, q] of Object.entries(r.src)) m[Number(ff)] = (m[Number(ff)] || 0) + (q || 0);
        }
        const freeByNm: Record<number, Record<number, number>> = {};
        // effArticles: доступное уже за вычетом резерва других черновиков и с
        // ФФ-ограничением скоупа — добор коробов не тронет чужой план.
        for (const a of effArticles) {
            const pool: Record<number, number> = {};
            for (const [ff, st] of Object.entries(a.rf_stocks || {})) {
                const free = (st.available || 0) - (inDraft[a.nm_id]?.[Number(ff)] || 0);
                if (free > 0) pool[Number(ff)] = free;
            }
            if (Object.keys(pool).length) freeByNm[a.nm_id] = pool;
        }
        return {
            ppbOf: (nm) => nmPpb.get(nm),
            ppbAt: (nm, ffId) => nmPpbByWh.get(nm)?.[ffId] ?? null,
            boxSizeOf: (nm) => nmBoxSize.get(nm) ?? null,
            overrides: palletOverrides,
            freeByNm,
            // Классы совместимости: SKU разных классов не делят смешанную BOX-паллету.
            classOf,
        };
    }, [effArticles, nmPpb, nmPpbByWh, nmBoxSize, palletOverrides, classOf]);

    // ЛОКАЛЬНАЯ нормализация (rows+prebook) без I/O — единый конвейер для self-heal
    // (normalizeAndSave) и ЛЮБОГО сохранения (saveDraft/autosave). Инвариант «целые
    // коробы + целые паллеты» обязан держать КАЖДЫЙ PUT, а не только applyDraft-пути:
    // сырой автосейв записывал под-паллетный хвост в rows, и в открытой вкладке его
    // никто не чинил до F5 (ловили живьём: хамза→СПБ Шушары, 9 коробов = 0.9 паллеты
    // висели в черновике «неполной паллетой»).
    const normalizeLocal = useCallback((
        baseRows: AssemblyDraftRow[],
        basePrebook: AssemblyDraftRow[],
        only?: Set<string>,
    ) => {
        // Пул добора (freeByNm) СТРОИТСЯ ОТ ПОЛНОГО черновика (вычитает занятое И rows, И
        // prebook по ВСЕМ направлениям) — даже при сужении `only` конкуренция за короб
        // честная. Сама нормализация — только тронутых `${pkg}::${wb}` (см.
        // scopedNormalizeDraft); `only=undefined` — полный проход (загрузка, saveDraft).
        const ctx = buildNormalizeCtx([...baseRows, ...basePrebook]);
        return scopedNormalizeDraft(baseRows, basePrebook, ctx, only);
    }, [buildNormalizeCtx]);

    // ─── Save draft (manual + autosave) ──────────────────────────────────
    const saveDraft = useCallback(async (silent = false): Promise<boolean> => {
        if (!draftId) return false;
        // Re-shipment guard (stale page / second tab): reconcile against server and
        // drop rows a partial commit already turned into AssemblyRequests.
        let effectiveRows = rows;
        try {
            const server = await api.getAssemblyDraft(draftId);
            const reconciled = dropCommittedRows(rows, server.distribution.rows || []);
            if (reconciled.length !== rows.length) {
                effectiveRows = reconciled;
                setRows(reconciled);
            }
        } catch { /* keep local rows */ }

        // Инвариант на КАЖДЫЙ сейв (включая автосейв): сырые строки не пишем —
        // под-паллетные хвосты уезжают в предбронь тем же конвейером, что self-heal.
        // Без геометрии — сейв как есть (self-heal добьёт на следующей загрузке).
        let effPrebook = prebook;
        if (geomState === 'ready') {
            const norm = normalizeLocal(effectiveRows, prebook);
            if (norm.changed) {
                effectiveRows = norm.rows;
                effPrebook = norm.prebook;
                setRows(norm.rows);
                setPrebook(norm.prebook);
            }
        }
        const dist: AssemblyDraftDistribution = { ...buildDistribution(), rows: effectiveRows, prebook: effPrebook };
        const json = JSON.stringify(dist);
        if (json === lastSavedJsonRef.current && !silent) return true;

        setSaving(true);
        try {
            const updated = await api.updateAssemblyDraft(draftId, {
                name, distribution: dist, comment: comment || null,
                base_updated_at: draftVersionRef.current || undefined,
            });
            lastSavedJsonRef.current = JSON.stringify(updated.distribution);
            draftVersionRef.current = updated.updated_at;
            if (!silent) showToast('Черновик сохранён', 'success');
            return true;
        } catch (e: unknown) {
            if (isVersionConflict(e)) {
                // Черновик изменила другая вкладка — наш снимок протух: перечитываем
                // вместо записи (иначе full-replace воскресил бы затёртое).
                try { applyDraft(await api.getAssemblyDraft(draftId)); } catch { /* следующий цикл */ }
                showToast('Черновик изменён в другой вкладке — данные обновлены (несохранённые правки этой вкладки отменены)', 'error');
                return false;
            }
            showToast(e instanceof Error ? e.message : 'Ошибка сохранения', 'error');
            return false;
        } finally {
            setSaving(false);
        }
    }, [draftId, buildDistribution, name, comment, rows, prebook, geomState, normalizeLocal, showToast]);

    const ensureSaved = useCallback(() => saveDraft(true), [saveDraft]);

    // Перезагрузить текущий черновик (после партиального commit или долива из потребности).
    // ⚠ Именно ТЕКУЩИЙ (по id): getOrCreateCurrentDraft вернул бы singleton и молча
    // увёл бы со скоупленного черновика. Если черновик исчез (полный commit его
    // soft-удалил) — возвращаемся на основной. Заодно освежаем резерв других черновиков.
    const reloadDraft = useCallback(async () => {
        try {
            if (draftId && draftParam) {
                try {
                    applyDraft(await api.getAssemblyDraft(draftId));
                } catch {
                    switchDraft(null);   // черновик закоммичен целиком — на основной
                }
            } else {
                applyDraft(await api.getOrCreateCurrentDraft());
            }
            void refreshReserved();
        } catch { /* ignore */ }
    }, [applyDraft, draftId, draftParam, switchDraft, refreshReserved]);

    // Привести черновик `id` к инварианту (целые коробы + целые паллеты + моно ≤3) и
    // сохранить, если раскладка изменилась. Гейт по готовности геометрии (без габаритов
    // нормализация снесла бы строки) → null. `fresh` передаётся вызывающим, у которого
    // уже есть свежий черновик (add/remove возвращают его) — экономит лишний GET. Возвращает
    // финальный черновик (для `applyDraft` вместо ещё одного reload-GET). Бросает наверх.
    const normalizeAndSave = useCallback(async (
        id: number,
        fresh?: AssemblyDraft,
        only?: Set<string>,
    ): Promise<{ changed: boolean; droppedUnits: number; droppedToPrebook: number; prebookFilledUp: number; releasedTotal: number; draft: AssemblyDraft } | null> => {
        if (geomState !== 'ready') return null;
        const base = fresh ?? await api.getAssemblyDraft(id);
        const norm = normalizeLocal(base.distribution.rows || [], base.distribution.prebook || [], only);
        let draft = base;
        if (norm.changed) {
            try {
                draft = await api.updateAssemblyDraft(id, {
                    distribution: { ...base.distribution, rows: norm.rows, prebook: norm.prebook },
                    base_updated_at: base.updated_at,
                });
            } catch (e) {
                if (!isVersionConflict(e)) throw e;
                return null; // черновик уже изменили — heal пересчитается на новой версии
            }
        }
        return {
            changed: norm.changed,
            droppedUnits: norm.droppedUnits,
            droppedToPrebook: norm.droppedToPrebook,
            prebookFilledUp: norm.prebookFilledUp,
            releasedTotal: norm.releasedTotal,
            draft,
        };
    }, [geomState, normalizeLocal]);

    // ── SELF-HEAL: привести черновик к инварианту «целые коробы + целые паллеты».
    // Прогон на КАЖДОЕ изменение черновика (ключ draftId::updated_at), а не только на
    // загрузку: заполнение/добавление могло проскочить без нормализации (гонка с
    // загрузкой геометрии — ловили живьём), self-heal чинит следом. Идемпотентно:
    // на чистом черновике changed=false → PUT не идёт → цикла нет. Локальные ручные
    // правки НЕ триггерят (autosave не трогает draft-стейт), только applyDraft-пути.
    // Строки `as_is` («Оставить так») нормализатор пропускает насквозь.
    const selfHealKeyRef = useRef<string>('');
    const selfHealBusyRef = useRef(false);
    useEffect(() => {
        if (!draftId || geomState !== 'ready' || loading || selfHealBusyRef.current || redistBusyRef.current) return;
        const key = `${draftId}::${draft?.updated_at || ''}`;
        if (selfHealKeyRef.current === key) return;
        selfHealKeyRef.current = key;
        selfHealBusyRef.current = true;
        // Сужение: scope, взведённый пер-направленческим хендлером на ЭТУ версию черновика
        // → нормализуем лишь тронутые направления. Иначе (загрузка / внешний reload /
        // несовпадение версии) — undefined = полный проход (сеть безопасности инварианта).
        const scope = healScopeRef.current;
        const only = scope && scope.ts === (draft?.updated_at || '') ? scope.only : undefined;
        void (async () => {
            try {
                const res = await normalizeAndSave(draftId, undefined, only);
                if (res?.changed) {
                    // Трейлинг-проход по НОВОЙ версии не должен рескан-нуть весь черновик:
                    // пустой scope на неё → self-heal@новая = no-op (черновик уже целый).
                    healScopeRef.current = { ts: res.draft.updated_at, only: new Set() };
                    applyDraft(res.draft);
                    const parts: string[] = [];
                    if (res.droppedToPrebook > 0) parts.push(`${formatNumber(res.droppedToPrebook, 0)} шт недобора → в предбронь`);
                    if (res.prebookFilledUp > 0) parts.push(`хвосты предброни добиты до целых коробов свободным ФФ (+${formatNumber(res.prebookFilledUp, 0)} шт)`);
                    if (res.releasedTotal > 0) parts.push(`некратные остатки (добить нечем) → на ФФ, без резерва (−${formatNumber(res.releasedTotal, 0)} шт)`);
                    if (parts.length) {
                        showToast(`Черновик приведён к целым коробам и паллетам: ${parts.join(' · ')}`, 'success');
                    }
                }
            } catch { /* best-effort — не блокируем страницу */ }
            finally { selfHealBusyRef.current = false; }
        })();
    }, [draftId, draft, geomState, loading, normalizeAndSave, applyDraft, showToast]);

    // ─── Категорийные черновики: чипы / создание / роспуск ────────────────
    // Чипы переключателя: основной (бесскоупный) первым, затем категорийные по свежести.
    const draftChips = useMemo(() => {
        const qtyOf = (d: AssemblyDraft) => {
            const all = [...(d.distribution.rows || []), ...(d.distribution.prebook || [])];
            return all.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
        };
        const scoped = allDrafts.filter(d => (d.distribution.category_scope?.length ?? 0) > 0);
        const plain = allDrafts.filter(d => (d.distribution.category_scope?.length ?? 0) === 0);
        return [
            ...plain.slice(0, 1).map(d => ({ d, qty: qtyOf(d), scoped: false })),
            ...scoped.map(d => ({ d, qty: qtyOf(d), scoped: true })),
        ];
    }, [allDrafts]);

    const handleDissolveScoped = useCallback(async () => {
        if (!draftId || !isScoped) return;
        if (!window.confirm('Распустить категорийный черновик? Строки и предбронь удалятся, товар освободится на ФФ (в основном черновике появится как доступный).')) return;
        try {
            await api.deleteAssemblyDraft(draftId);
            showToast('Черновик распущен — товар освобождён на ФФ', 'success');
            switchDraft(null);
        } catch (e: unknown) {
            showToast(e instanceof Error ? e.message : 'Ошибка удаления черновика', 'error');
        }
    }, [draftId, isScoped, switchDraft, showToast]);

    // Создание категорийного черновика: carve строк выбранных категорий из ТЕКУЩЕГО
    // (обычного) черновика (не задваиваем план) → новый черновик со скоупом →
    // редирект на «Ручную раскладку» (авто-синк заполнит срез из потребности).
    const handleCreateScopedDraft = useCallback(async (categories: string[], ffId: number | null) => {
        try {
            // Гасим взведённый дебаунс автосейва: его PUT нёс бы ПОЛНЫЙ до-carve снимок
            // и лёг бы поверх cleanup-PUT (перенесённые строки воскресали в источнике —
            // план в двух черновиках, оба коммитят один сток). Несохранённые правки не
            // теряются: carve считается от текущего стейта, cleanup-PUT их персистит.
            if (autosaveTimerRef.current) {
                clearTimeout(autosaveTimerRef.current);
                autosaveTimerRef.current = null;
            }
            const scopedIn = inScopeOf(categories, categoryOf);
            let carveOut: { restRows: AssemblyDraftRow[]; restPrebook: AssemblyDraftRow[] } | null = null;
            let movedRows: AssemblyDraftRow[] = [];
            let movedPrebook: AssemblyDraftRow[] = [];
            if (!isScoped && draftId) {
                // ffId прокидываем в carve: при «только один ФФ» переезжают ТОЛЬКО
                // порции этого склада — категория с других ФФ остаётся в основном.
                const carve = carveScopeFromDraft({ rows, prebook }, scopedIn, ffId);
                if (carve.movedUnits > 0) {
                    const move = window.confirm(
                        `В текущем черновике уже есть ${formatNumber(carve.movedUnits, 0)} шт этих категорий${ffId != null ? ' (порции выбранного ФФ)' : ''} — перенести их в новый черновик? («Отмена» — оставить в текущем)`,
                    );
                    if (move) {
                        movedRows = carve.movedRows;
                        movedPrebook = carve.movedPrebook;
                        carveOut = { restRows: carve.restRows, restPrebook: carve.restPrebook };
                    }
                }
            }
            // CREATE-FIRST: сначала создаём новый черновик с перенесёнными строками и
            // только потом чистим источник. Если чистка упадёт — строки продублированы
            // (видно, чинится вручную), а не потеряны (ревью MED: сеть между двумя PUT).
            const created = await api.createAssemblyDraft({
                name: `Категории: ${categories.join(', ')}`.slice(0, 120),
                distribution: {
                    source_warehouse_ids: [],
                    target_warehouse_names: [],
                    rows: movedRows,
                    prebook: movedPrebook,
                    pallets_count: 1,
                    pallet_weight_kg: 0,
                    estimated_ready_date: null,
                    category_scope: categories,
                    scope_ff_id: ffId,
                    // ✋-флаги перенесённых SKU обязаны переехать вместе со строками:
                    // без них первый авто-синк «Ручной раскладки» пересчитал бы ручной
                    // план перенесённого SKU по потребности (класс бага «кастрюли 80→0»).
                    manual_nms: carveOut ? manualNmsRef.current.filter(nm => scopedIn(nm)) : [],
                },
            });
            if (carveOut && draftId) {
                try {
                    const cleaned = await api.updateAssemblyDraft(draftId, {
                        distribution: { ...buildDistribution(), rows: carveOut.restRows, prebook: carveOut.restPrebook },
                        event: { event_type: 'MATRIX_EDIT', summary: `Категории «${categories.join(', ')}» выделены в отдельный черновик` },
                    });
                    // Локальный стейт = очищенный источник: иначе любой поздний сейв
                    // (автосейв от следующей правки) вернул бы полные до-carve строки.
                    setRows(dedupeRows(cleaned.distribution.rows || []));
                    setPrebook(cleaned.distribution.prebook || []);
                    lastSavedJsonRef.current = JSON.stringify(cleaned.distribution);
                } catch {
                    showToast('⚠ Новый черновик создан, но строки не удалились из текущего — они сейчас в ОБОИХ черновиках, удалите дубль вручную', 'error');
                }
            }
            setCatModalOpen(false);
            // Резерв/список черновиков — СВЕЖИМИ до редиректа: авто-синк матрицы
            // нового черновика стартует сразу, со stale-резервом он считал бы план
            // без учёта только что созданного соседа (кросс-черновичный дубль).
            await refreshReserved();
            showToast(`Черновик «${created.name}» создан — раскладка считается на «✏️ Ручной раскладке»`, 'success');
            const sp = new URLSearchParams(searchParams.toString());
            sp.set('draft', String(created.id));
            sp.set('tab', 'matrix');
            router.replace(`?${sp.toString()}`, { scroll: false });
        } catch (e: unknown) {
            showToast(e instanceof Error ? e.message : 'Ошибка создания категорийного черновика', 'error');
        }
    }, [isScoped, draftId, rows, prebook, categoryOf, buildDistribution, refreshReserved, searchParams, router, showToast]);

    // Долив строк из встроенной вкладки «Потребность» → перезагрузить черновик и
    // переключиться на вкладку 'draft', чтобы пользователь увидел добавленное.
    const handleRowsAdded = useCallback(async () => {
        await reloadDraft();
        setTab('draft');
    }, [reloadDraft, setTab]);

    // ── «Заполнить черновик из потребности»: ЗАМЕНИТЬ весь черновик раскладкой, которую
    // считает вкладка «Потребность по складам» (с bump/локализацией) — точное соответствие.
    // Кнопка переключает на вкладку потребности (там монтируется расчёт + грузятся лимиты,
    // показывается скелетон), та строит строки для ВСЕХ SKU и отдаёт сюда через onFillAllRows.
    const [fillSignal, setFillSignal] = useState(0);
    const [filling, setFilling] = useState(false);

    const handleFillFromNeed = useCallback(() => {
        if (filling) return;
        // Категорийный черновик заполняется НЕ отсюда: расчёт скрытой «Потребности»
        // не знает про скоуп/ФФ-ограничение/резерв — авто-синк «Ручной раскладки»
        // считает то же самое, но на срезе. Уводим туда.
        if (isScoped) {
            showToast('Категорийный черновик заполняется на вкладке «✏️ Ручная раскладка» — авто-синк считает только его категории', 'error');
            setTab('matrix');
            return;
        }
        // Гонка «кликнул раньше кратностей»: расчёт потребности идёт десятки секунд, но
        // если геометрия к моменту коллбэка не готова — заполнение всё равно упрётся в
        // жёсткий гейт. Не запускаем расчёт вовсе, пока кратности не загружены.
        if (geomState !== 'ready') {
            showToast(geomState === 'error'
                ? 'Кратности коробов не загрузились — нажмите «Повторить загрузку» в красном баннере'
                : 'Кратности коробов ещё грузятся — подождите пару секунд и нажмите снова', 'error');
            return;
        }
        if (rows.length > 0 && !window.confirm('Заменить весь черновик раскладкой из «Потребность по складам»? Текущие строки и ручные правки будут удалены.')) return;
        // НЕ переключаем вкладку: расчёт идёт в скрытом инстансе WarehouseNeedView под
        // блокирующим оверлеем (иначе прыжок на вкладку + ожидание лимитов сбивали с толку).
        setFilling(true);
        setFillSignal(s => s + 1);
    }, [filling, rows.length, geomState, showToast, isScoped, setTab]);

    const handleFillAllRows = useCallback(async (newRows: AssemblyDraftRow[]) => {
        try {
            if (!draftId) return;
            if (newRows.length === 0) { showToast('В потребности нечего отгрузить', 'error'); return; }
            // ЖЁСТКИЙ ГЕЙТ: без загруженной геометрии заполнение ЗАПРЕЩЕНО — иначе сырые
            // строки потребности (некратные хвосты) молча легли бы в черновик без
            // округления и без сплита в предбронь (ловили живьём: «38 строк россыпью»).
            if (geomState !== 'ready') {
                showToast('Кратности коробов не загружены — обновите страницу и дождитесь загрузки, потом заполняйте', 'error');
                return;
            }
            // БЕЗ КРАТНОСТИ — НЕ УЧАСТВУЮТ В РАСЧЁТЕ (правило юзера): у SKU нет данных
            // «шт/короб» → ни коробами (нечем округлить), ни россыпью (невыгодно) не едет.
            // Такие видны в блоке «Без кратности» на вкладке черновика → «Указать кратность».
            const hasPpb = (nm: number) => (nmPpb.get(nm) || 0) > 0;
            const skippedNoPpb = newRows.filter(r => !hasPpb(r.nm_id));
            // Скоуп-гейт (страховка: fill для скоупленных заблокирован выше) — чужие
            // категории в категорийный черновик не попадают ни при каком пути.
            const usableRows = newRows.filter(r => hasPpb(r.nm_id) && inScope(r.nm_id));
            if (usableRows.length === 0) { showToast('У всех артикулов потребности нет кратности короба — заполните вкладку «Кратность»', 'error'); return; }
            const skippedUnits = skippedNoPpb.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
            // РЕЗЕРВ: зарезервированная предбронь (текущая) ПИНится к своим направлениям —
            // при заполнении сначала кладём эти коробы туда (вычитая из свежей потребности,
            // без задвоения), излишек/устаревшее — отпускается. Итог по (nm,склад)=потребность.
            const seeded = prebook.length ? reconcileFillWithReserved(usableRows, prebook) : usableRows;
            // ЦЕЛЫЕ ПАЛЛЕТЫ в черновик; целые коробы, не собравшие паллету (под-паллетный
            // хвост) → ПРЕДБРОНЬ (не теряем на ФФ). Пересчитывается при каждом заполнении.
            let keptRows = seeded;
            let newPrebook: AssemblyDraftRow[] = [];
            if (geomState === 'ready') {
                const norm = normalizeDraft(seeded, buildNormalizeCtx(seeded));
                keptRows = norm.rows;
                newPrebook = norm.dropped;
            }
            const targetNames = Array.from(new Set(keptRows.flatMap(r => Object.keys(r.tgt))));
            const sourceIds = Array.from(new Set(
                keptRows.flatMap(r => Object.keys(r.src).map(Number).filter(n => Number.isFinite(n) && n > 0)),
            ));
            // Полное заполнение = свежая раскладка: целые паллеты из потребности (не из
            // предброни). Сбрасываем провенанс — новые пометки добавят topup/консолидация.
            prebookOriginRef.current = new Set();
            const dist: AssemblyDraftDistribution = {
                ...buildDistribution(),
                rows: keptRows,
                prebook: newPrebook,
                source_warehouse_ids: sourceIds,
                target_warehouse_names: targetNames,
            };
            const units = keptRows.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
            const pbUnits = newPrebook.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
            const updated = await api.updateAssemblyDraft(draftId, { distribution: dist });
            applyDraft(updated);
            showToast(
                `Черновик: ${formatNumber(keptRows.length, 0)} строк · Σ ${formatNumber(units, 0)} шт`
                + (newPrebook.length ? ` · предбронь ${formatNumber(newPrebook.length, 0)} поз. (${formatNumber(pbUnits, 0)} шт)` : '')
                + (skippedNoPpb.length ? ` · ⚠️ без кратности пропущено ${formatNumber(skippedNoPpb.length, 0)} арт. (${formatNumber(skippedUnits, 0)} шт) — см. блок «Без кратности»` : ''),
                'success',
            );
        } catch (e: unknown) {
            showToast(e instanceof Error ? e.message : 'Ошибка заполнения черновика', 'error');
        } finally {
            setFillSignal(0);   // сброс сигнала, чтобы повторный расчёт не само-запускался
            setFilling(false);
        }
    }, [draftId, prebook, buildDistribution, applyDraft, showToast, geomState, nmPpb, buildNormalizeCtx, inScope]);

    // ── БЕЗ КРАТНОСТИ: артикулы со стоком на ФФ, у которых нет данных «шт/короб». ──
    // По правилу юзера НЕ участвуют в расчёте (fill/add их отфильтровывают): россыпью
    // возить невыгодно. Показываем списком с кнопкой «Указать кратность» (вкладка box).
    const noPpbArticles = useMemo(() => {
        if (geomState !== 'ready') return [];
        const out: { nm_id: number; vendor: string; rf: number }[] = [];
        for (const a of effArticles) {
            if (!inScope(a.nm_id)) continue;
            if ((nmPpb.get(a.nm_id) || 0) > 0) continue;
            const rf = Object.values(a.rf_stocks || {}).reduce((s, st) => s + (st.available || 0), 0);
            if (rf > 0) out.push({ nm_id: a.nm_id, vendor: a.vendor_code || `nm ${a.nm_id}`, rf });
        }
        return out.sort((x, y) => y.rf - x.rf);
    }, [geomState, effArticles, inScope, nmPpb]);

    // ── ЧАСТИЧНАЯ КРАТНОСТЬ: кратность задана НЕ на всех складах, где лежит остаток. ──
    // Товар едет со склада без своей кратности (машинная кратность есть на ЧУЖОМ складе,
    // где остатка нет) → эта порция поедет россыпью/псевдо-кратно. Показываем, чтобы
    // проставить кратность на складе-источнике. `nmPpbByWh` пуст = глобальный override
    // (кратно везде) → не частичная.
    const partialPpbArticles = useMemo(() => {
        if (geomState !== 'ready') return [];
        const out: { nm_id: number; vendor: string; rf: number }[] = [];
        for (const a of effArticles) {
            if (!inScope(a.nm_id)) continue;
            if ((nmPpb.get(a.nm_id) || 0) <= 0) continue;   // совсем без кратности — соседний блок
            const perWh = nmPpbByWh.get(a.nm_id);
            if (!perWh) continue;                            // глобальный override → кратно везде
            const uncovered = Object.entries(a.rf_stocks || {})
                .filter(([ff, st]) => (st.available || 0) > 0 && !(Number(ff) in perWh));
            if (uncovered.length === 0) continue;            // кратность на всех складах с остатком
            const rf = uncovered.reduce((s, [, st]) => s + (st.available || 0), 0);
            out.push({ nm_id: a.nm_id, vendor: a.vendor_code || `nm ${a.nm_id}`, rf });
        }
        return out.sort((x, y) => y.rf - x.rf);
    }, [geomState, effArticles, inScope, nmPpb, nmPpbByWh]);

    // ── БЕЗ РАЗМЕРА КОРОБКИ: кратность есть, а габаритов нет. Короба собрать можно,
    // паллету — нет (уедет монопаллетой/крупногабаритом). Показываем, чтобы задать размер.
    const noBoxSizeArticles = useMemo(() => {
        if (geomState !== 'ready') return [];
        const out: { nm_id: number; vendor: string; rf: number }[] = [];
        for (const a of effArticles) {
            if (!inScope(a.nm_id)) continue;
            if ((nmPpb.get(a.nm_id) || 0) <= 0) continue;   // без кратности — соседний блок
            if (nmBoxSize.get(a.nm_id)) continue;           // размер коробки есть
            const rf = Object.values(a.rf_stocks || {}).reduce((s, st) => s + (st.available || 0), 0);
            if (rf > 0) out.push({ nm_id: a.nm_id, vendor: a.vendor_code || `nm ${a.nm_id}`, rf });
        }
        return out.sort((x, y) => y.rf - x.rf);
    }, [geomState, effArticles, inScope, nmPpb, nmBoxSize]);

    // ── ПРЕДБРОНЬ: целые коробы, не собравшие паллету при заполнении. ──
    // Группы предброни: (упаковка × направление), с оценкой возможности дозабора.
    const prebookGroups = useMemo<PrebookGroup[]>(() => {
        if (prebook.length === 0) return [];
        // Единый билдер групп предброни (тот же, что у экрана машины) — источник доступности
        // раздела = наш ФФ-сток (stockNeed.articles.rf_stocks). Раньше тут был инлайн-дубль.
        const ffNameMap = new Map<number, string>();
        for (const w of stockNeed?.rf_warehouses ?? []) ffNameMap.set(w.id, w.name);
        // Пул дозабора: effArticles (резерв других черновиков вычтен, ФФ скоупа
        // применён) + в скоупленном черновике кандидаты только своих категорий.
        const articles = effArticles
            .filter(a => inScope(a.nm_id))
            .map(a => {
                const rfStocks: Record<number, number> = {};
                for (const [ff, st] of Object.entries(a.rf_stocks || {})) rfStocks[Number(ff)] = st.available || 0;
                return { nm_id: a.nm_id, vendor_code: a.vendor_code || '', rfStocks };
            });
        return buildPrebookGroups({
            prebook,
            usedRows: rows,
            articles,
            ffName: (ffId) => ffNameMap.get(ffId) || `ФФ ${ffId}`,
            ppbOf: (nm) => nmPpb.get(nm) || 0,
            ppbAt: (nm, ff) => nmPpbByWh.get(nm)?.[ff] ?? (nmPpb.get(nm) || 0),
            boxSizeOf: (nm) => nmBoxSize.get(nm) ?? null,
            palletOverrides,
            // Классы совместимости: BOX-карточки режутся на срезы per-класс.
            classOf,
            classLabelOf: classLabel,
        });
    }, [prebook, rows, stockNeed, effArticles, inScope, nmPpb, nmPpbByWh, nmBoxSize, palletOverrides, classOf, classLabel]);

    // Направления (упаковка × склад), ГОТОВЫЕ К СДАЧЕ по живой приёмке WB: тип
    // принимается И есть лимит (свободные ИЛИ платные дни > 0). Правило: черновик
    // содержит только готовое к сдаче наполнение — авто-вынос из предброни идёт
    // ТОЛЬКО по таким направлениям (все упаковки, не только моно). Остальное
    // (⌛ без лимита / приём закрыт / нет данных) держится в предброни: моно —
    // под предзаявку, короб — ждать открытия приёмки (или явные «Дозабить»/
    // «Оставить так» по клику). Флаги — уровня склада, берём из любого nm с данными.
    const readyPkgWbs = useMemo<Set<string>>(() => {
        const out = new Set<string>();
        const days = (m?: { free_days_14?: number; paid_days_14?: number } | null) =>
            (m?.free_days_14 ?? 0) + (m?.paid_days_14 ?? 0);
        for (const per of prebookAcceptance.values()) {
            for (const [wb, f] of Object.entries(per.availability || {})) {
                if (f.can_box && days(f.box_meta) > 0) out.add(`BOX::${wb}`);
                if (f.can_monopallet && days(f.mono_meta) > 0) out.add(`MONOPALLET::${wb}`);
                if (f.can_supersafe && days(f.super_meta) > 0) out.add(`SUPERSAFE::${wb}`);
            }
        }
        return out;
    }, [prebookAcceptance]);

    // Направления, по которым приёмка ПРОВЕРЕНА (данные есть). Демоция rows→prebook
    // делается ТОЛЬКО по проверенным: «нет данных» ≠ «не готов» (иначе при сбое
    // проверки весь черновик уехал бы в предбронь).
    const checkedPkgWbs = useMemo<Set<string>>(() => {
        const out = new Set<string>();
        for (const per of prebookAcceptance.values()) {
            for (const [wb, f] of Object.entries(per.availability || {})) {
                // «Проверено» пер-упаковочно: закрыт (флаг false — окончательный ответ WB)
                // ИЛИ открыт С коэффициентами (meta есть). can_X=true при meta=null =
                // coefficients не загрузились (WB 429, квота 6/мин) — бэк запекал такой
                // снимок в кэш на 10 мин, фронт видел «дней 0» и демотировал ВЕСЬ черновик
                // в предбронь одним eventless PUT (прод-кейс 17.07: откат 40 дозаборов).
                // Неоднозначные данные = «не проверено» → направление не трогаем.
                if (!f.can_box || f.box_meta != null) out.add(`BOX::${wb}`);
                if (!f.can_monopallet || f.mono_meta != null) out.add(`MONOPALLET::${wb}`);
                if (!f.can_supersafe || f.super_meta != null) out.add(`SUPERSAFE::${wb}`);
            }
        }
        return out;
    }, [prebookAcceptance]);

    // ЕДИНАЯ синхронизация «черновик = ТОЛЬКО готовое к сдаче» (по живой приёмке).
    // Ключевой принцип: пул КАЖДОГО проверенного направления = его порции в rows И в
    // prebook, упакованные ОДНОЙ упаковкой (упаковка суммы двух валидных наборов ≠
    // объединение их упаковок — раздельные проходы стравливали хвосты в rows, ловили
    // живьём 58 шт «без целой» в Казани). Раскладка результата:
    //   • ГОТОВОЕ направление (приём + лимит): целые паллеты → rows, хвост → prebook;
    //   • проверенное НЕ готовое (⌛/закрыто): всё → prebook (моно — под предзаявку);
    //   • непроверенное или as_is («Оставить так») — не трогаем.
    // PUT только при реальном изменении (канон-подпись) → идемпотентно, цикла нет.
    const consolidatingRef = useRef(false);
    // Канон-подпись приёмки (проверенные + готовые направления). Меняется ТОЛЬКО при новой
    // приёмке WB — не при клике юзера. Гейт консолидации: реагируем на приёмку и полный
    // fill/load, но НЕ на пер-направленческий клик (его обрабатывают хендлер + scoped self-heal).
    const consAccSigRef = useRef<string>('');
    const [consRearmTick, setConsRearmTick] = useState(0);
    useEffect(() => {
        if (!draftId || geomState !== 'ready' || consolidatingRef.current || redistBusyRef.current) return;
        // Приёмка ещё не загружена → ждём (эффект перезапустится по смене prebookAcceptance).
        if (prebookAcceptance.size === 0) return;
        if (rows.length === 0 && prebook.length === 0) return;
        // ГЕЙТ ИЗОЛЯЦИИ: если приёмка та же И на ЭТУ версию черновика взведён scope (клик по
        // одному направлению — additive scope={dir} или subtractive scope={}), консолидацию
        // НЕ гоняем: направление уже приведено хендлером + scoped self-heal, соседей не трогаем.
        // Полный fill/load scope не ставит (undefined) → идём. Новая приёмка (accChanged) →
        // идём всегда (промоция/демоция по живой приёмке — её прямая задача).
        // БЕЗОПАСНОСТЬ СКИПА держится на инварианте: self-heal НИКОГДА не собирает промотируемую
        // ЦЕЛУЮ ПАЛЛЕТУ (topUpPrebookLooseBoxes добивает предбронь лишь до целого КОРОБА; промоцию
        // целых паллет в rows делает ТОЛЬКО consolidatePrebookWholePallets — здесь). После скипа
        // промотировать нечего. Инвариант закреплён тестом scopedNormalize.test.ts («self-heal НЕ
        // промотирует предбронь в rows») — если round-boxes однажды научат паллет-уровневому
        // добору, тест упадёт: тогда этот гейт нельзя скипать без re-trigger.
        const accSig = [...checkedPkgWbs].sort().join(';') + '||' + [...readyPkgWbs].sort().join(';');
        const accChanged = consAccSigRef.current !== accSig;
        const scope = healScopeRef.current;
        // fromSync-версии гейт НЕ скипают: «инвариант скипа» (self-heal не собирает
        // целую паллету) для синк-PUT ложен — его normalizePair может слить хвосты
        // направления в ≥1 целую паллету в предброни, промотировать после него есть что.
        const scopedMutation = !!scope && scope.ts === (draft?.updated_at || '') && !scope.fromSync;
        if (!accChanged && scopedMutation) return;
        consolidatingRef.current = true;
        let cancelled = false;
        // CAS-токен = версия, которой соответствуют ЗАМЫКАНИЯ rows/prebook этого
        // прогона. Живой draftVersionRef.current в момент PUT «узаконивал» запись
        // от протухшего состояния: раннер промотировал паллету и applyDraft обновил
        // ref, а летящая консолидация со старыми rows проходила CAS и откатывала
        // промоцию через секунду (прод: пинг-понг событий 5144→5148 черновика 51).
        const baseVersion = draftVersionRef.current;
        void (async () => {
            try {
                // self-heal в полёте: считать сейчас — значит посчитать от устаревшего
                // стейта и перекрыть его PUT (ловили живьём: конс-PUT отменил добивку
                // 14→22). ДОЖИДАЕМСЯ, а не return: если self-heal закончится без PUT,
                // deps не изменятся и пропущенный прогон не повторился бы никогда
                // (демоция/промоция по свежей приёмке молча зависала). Если self-heal
                // всё же PUT-нул — deps изменились, наш прогон устарел → cancelled.
                while (selfHealBusyRef.current) await new Promise((r) => setTimeout(r, 120));
                if (cancelled) return;
                // Фиксируем обработанную приёмку ТОЛЬКО пройдя ожидание (не на отменённом
                // прогоне — иначе acceptance-driven консолидация после re-arm ложно скипнулась бы).
                consAccSigRef.current = accSig;
                // 1. Карвим из rows порции всех ПРОВЕРЕННЫХ направлений (кроме as_is) в пул.
                const keepRows: AssemblyDraftRow[] = [];   // as_is + непроверенные направления
                const pool: AssemblyDraftRow[] = [];
                for (const r of rows) {
                    const pkg = r.package_type || 'BOX';
                    if (r.as_is) { keepRows.push(r); continue; }
                    const checkedWbs = Object.keys(r.tgt).filter(wb => checkedPkgWbs.has(`${pkg}::${wb}`));
                    if (checkedWbs.length === 0) { keepRows.push(r); continue; }
                    const cur = { ...r, src: { ...r.src }, tgt: { ...r.tgt } };
                    for (const wb of checkedWbs) {
                        for (const [pairKey, q] of allocatePairs(cur.src, cur.tgt)) {
                            if ((q || 0) <= 0 || pairKey.slice(pairKey.indexOf('::') + 2) !== wb) continue;
                            const ff = pairKey.slice(0, pairKey.indexOf('::'));
                            pool.push({ nm_id: r.nm_id, barcode: r.barcode, vendor_code: r.vendor_code, src: { [ff]: q }, tgt: { [wb]: q }, package_type: pkg });
                            cur.tgt[wb] = Math.max(0, (cur.tgt[wb] || 0) - q);
                            cur.src[ff] = Math.max(0, (cur.src[ff] || 0) - q);
                        }
                        if ((cur.tgt[wb] || 0) <= 0) delete cur.tgt[wb];
                    }
                    for (const ff of Object.keys(cur.src)) if (cur.src[ff] <= 0) delete cur.src[ff];
                    if (Object.keys(cur.tgt).length > 0) keepRows.push(cur);
                }
                // 2. Единая упаковка пула (rows-порции + ВСЯ предбронь): готовые целые →
                //    черновик, всё прочее (хвосты, ⌛, закрытые, без геометрии) → предбронь.
                const fullPool = mergeDraftRows([...pool, ...prebook]);
                const keepInPrebook = (nm: number, wb: string, pkg: PackageType) =>
                    !readyPkgWbs.has(`${pkg}::${wb}`);
                const ctx = buildNormalizeCtx([...keepRows, ...fullPool]);
                // Добор до ЦЕЛОГО КОРОБА из свободного ФФ (строго с ФФ порции) — с ОБЕИХ
                // сторон упаковки. ДО: некратный хвост пула иначе уехал бы «россыпью
                // внутри целой паллеты» в rows (безопасно: срез упаковки теперь
                // короб-гранулярный и добитый короб не распилит). ПОСЛЕ: страховка для
                // хвостов, оставшихся в предброни. Именно в этом эффекте, а не только в
                // self-heal: этот PUT — последний в цепочке, добор мимо него терялся.
                // Пул ctx консистентен: первый добор его расходует, упаковка консервативна
                // per (nm, ff) и freeByNm не трогает, второй добор берёт остаток.
                const topPool = topUpPrebookLooseBoxes(fullPool, ctx.ppbOf, ctx.freeByNm ?? {}, ctx.ppbAt);
                // «Добить нечем» → НЕ резервируем (решение юзера): безнадёжная россыпь
                // снимается ДО упаковки (иначе уехала бы «внутри целой паллеты» в rows)
                // и возвращается на ФФ; целые коробы остаются.
                const relPool = releaseUnfixableLooseBoxes(topPool.changed ? topPool.rows : fullPool, ctx.ppbOf, ctx.freeByNm ?? {}, ctx.ppbAt);
                const packInput = relPool.changed ? relPool.rows : (topPool.changed ? topPool.rows : fullPool);
                const cons = consolidatePrebookWholePallets(packInput, ctx, keepInPrebook);
                const extractedRows = mergeDraftRows([...keepRows, ...cons.toDraft]);
                // Страховка на хвосты после упаковки (обычно no-op: пул уже строгий).
                const topTail = topUpPrebookLooseBoxes(cons.prebook, ctx.ppbOf, ctx.freeByNm ?? {}, ctx.ppbAt);
                const release = releaseUnfixableLooseBoxes(topTail.changed ? topTail.rows : cons.prebook, ctx.ppbOf, ctx.freeByNm ?? {}, ctx.ppbAt);
                const tailPrebook = release.changed ? release.rows : (topTail.changed ? topTail.rows : cons.prebook);
                // ГВАРД СТАБИЛЬНОСТИ (адверсарное ревью, CRITICAL): пул-упаковка моно может
                // извлечь в rows бины, которые rows-only re-pack self-heal'а НЕ воспроизводит
                // (упаковка популяционно-зависима) → вечный пинг-понг rows↔prebook (128 шт,
                // цикл 22 PUT на живом черновике). Пере-нормализуем извлечённое В ПАМЯТИ:
                // нестабильные извлечения откатываются в предбронь ДО PUT → фикс-поинт.
                const verify = normalizeDraft(extractedRows, buildNormalizeCtx([...extractedRows, ...tailPrebook]));
                const newRows = verify.changed ? verify.rows : extractedRows;
                const newPrebook = verify.dropped.length ? mergeDraftRows([...tailPrebook, ...verify.dropped]) : tailPrebook;
                const filledUpUnits = topPool.filledUp + topTail.filledUp;
                const releasedUnits = relPool.releasedUnits + release.releasedUnits + verify.releasedUnits;
                // 3. Реально ли что-то изменилось (канон-подпись обеих частей).
                const canon = (rs: AssemblyDraftRow[]) => rs
                    .map(r => `${r.nm_id}|${r.barcode}|${r.package_type || 'BOX'}|${r.as_is ? 1 : 0}|`
                        + Object.entries(r.src).filter(([, q]) => (q || 0) > 0).sort(([a], [b]) => (a < b ? -1 : 1)).map(([k, q]) => `${k}:${q}`).join(',') + '|'
                        + Object.entries(r.tgt).filter(([, q]) => (q || 0) > 0).sort(([a], [b]) => (a < b ? -1 : 1)).map(([k, q]) => `${k}:${q}`).join(','))
                    .sort().join(';');
                if (canon(newRows) === canon(rows) && canon(newPrebook) === canon(prebook)) return;
                // Косметическая перетасовка ФФ-источника (tgt-склады и штуки те же,
                // меняется только src) — НЕ пишем: allocatePairs/normalizeDraft каждый
                // прогон переназначают ФФ по-новому, canon (с src) их не гасит →
                // applyDraft перезапускает эффект → 5 PUT «переупаковка без смены
                // складов» подряд, забивающих историю и ломающих LIFO-откат
                // (прод 2026-07-20, черновик 51). Скипаем, если по (nm×pkg×as_is×
                // склад×штуки) ничего не сдвинулось и ничего не добито/освобождено.
                const canonTgt = (rs: AssemblyDraftRow[]) => rs
                    .map(r => `${r.nm_id}|${r.barcode}|${r.package_type || 'BOX'}|${r.as_is ? 1 : 0}|`
                        + Object.entries(r.tgt).filter(([, q]) => (q || 0) > 0).sort(([a], [b]) => (a < b ? -1 : 1)).map(([k, q]) => `${k}:${q}`).join(','))
                    .sort().join(';');
                if (filledUpUnits === 0 && releasedUnits === 0
                    && canonTgt(newRows) === canonTgt(rows) && canonTgt(newPrebook) === canonTgt(prebook)) return;
                // Бейдж «из предброни» = ТОЛЬКО ручной перенос (Дозабить / Оставить так /
                // Перенести паллеты). Авто-консолидация по приёмке WB поднимает целые
                // паллеты в черновик молча, БЕЗ метки провенанса — по требованию юзера.
                const targetNames = Array.from(new Set(newRows.flatMap(r => Object.keys(r.tgt))));
                const sourceIds = Array.from(new Set(newRows.flatMap(r => Object.keys(r.src).map(Number).filter(n => Number.isFinite(n) && n > 0))));
                // Сводка перемещений по складам (дельта rows) — ДО PUT: она же уходит
                // событием в историю. Раньше этот PUT был eventless — прод-диагностика
                // отката дозаборов была слепа (updated_at менялся без строки в истории).
                const sumBy = (rs: AssemblyDraftRow[]) => {
                    const m: Record<string, number> = {};
                    for (const r of rs) for (const [wb, q] of Object.entries(r.tgt)) m[wb] = (m[wb] || 0) + (q || 0);
                    return m;
                };
                const before = sumBy(rows); const after = sumBy(newRows);
                const toDraftList: string[] = []; const toPrebookList: string[] = [];
                for (const wb of new Set([...Object.keys(before), ...Object.keys(after)])) {
                    const d = (after[wb] || 0) - (before[wb] || 0);
                    if (d > 0) toDraftList.push(`${wb} +${formatNumber(d, 0)} шт`);
                    if (d < 0) toPrebookList.push(`${wb} −${formatNumber(-d, 0)} шт`);
                }
                const parts: string[] = [];
                if (toDraftList.length) parts.push(`в ЧЕРНОВИК (готово к сдаче, целыми паллетами): ${toDraftList.join(' · ')}`);
                if (toPrebookList.length) parts.push(`в ПРЕДБРОНЬ (хвосты / ⌛ / закрыто): ${toPrebookList.join(' · ')}`);
                if (filledUpUnits > 0) parts.push(`хвосты добиты до целых коробов свободным ФФ (+${formatNumber(filledUpUnits, 0)} шт)`);
                if (releasedUnits > 0) parts.push(`остатки меньше короба (добить нечем) возвращены на ФФ (−${formatNumber(releasedUnits, 0)} шт)`);
                // Прогон отменён (deps сменились, пока считали) → не пишем вовсе:
                // PUT от протухшего замыкания незачем даже пытаться (re-arm в finally).
                if (cancelled) return;
                let updated;
                try {
                    updated = await api.updateAssemblyDraft(draftId, {
                        distribution: { ...buildDistribution(), rows: newRows, prebook: newPrebook, source_warehouse_ids: sourceIds, target_warehouse_names: targetNames },
                        // СТРОГО baseVersion (версия замыканий), НЕ живой draftVersionRef:
                        // конкурентная запись (промоция раннера) → честный 409 → перечитка.
                        base_updated_at: baseVersion || undefined,
                        event: { event_type: 'ACCEPTANCE_SYNC', summary: `Синхронизация с приёмкой WB — ${parts.join(' | ') || 'переупаковка без смены складов'}` },
                    });
                } catch (e) {
                    if (isVersionConflict(e)) {
                        // Черновик изменила другая вкладка (прод-кейс «воскрешение после
                        // очистки»): наш расчёт от протухшего стейта — перечитываем и выходим.
                        try { applyDraft(await api.getAssemblyDraft(draftId)); } catch { /* ignore */ }
                        return;
                    }
                    throw e;
                }
                applyDraft(updated);
                if (parts.length) showToast(`Синхронизация с приёмкой WB — ${parts.join(' | ')}`, 'success');
            } catch { /* best-effort — не критично */ }
            finally {
                consolidatingRef.current = false;
                // Отменённый прогон (deps сменились, пока ждали self-heal): новая
                // инвокация эффекта могла отсечься по consolidatingRef — перевзводим,
                // иначе свежее состояние осталось бы не синхронизированным навсегда.
                if (cancelled) setConsRearmTick((t) => t + 1);
            }
        })();
        return () => { cancelled = true; };
    }, [draftId, draft, geomState, prebook, prebookGroups, rows, prebookAcceptance, readyPkgWbs, checkedPkgWbs, consRearmTick, buildNormalizeCtx, buildDistribution, applyDraft, showToast]);

    // АВТО-РЕДИСТРИБУЦИЯ ПРЕДБРОНИ по приёмке WB (self-heal на загрузке).
    // ⌛-склад (нет лимита приёмки), которого НЕТ в whitelist «Предзаявка без лимита»,
    // отгрузить нельзя — товар «висит» в под-вкладке «Предзаявка». Бэк
    // (check_acceptance_and_redistribute) уводит его на свободный/whitelist склад по
    // приоритету; здесь применяем splits к предброни, СОХРАНЯЯ источник ФФ. Триггер —
    // наличие ⌛-не-whitelist направления (checked, не ready, не в whitelist). Идемпотентно:
    // после переноса на whitelist/open splits цель не меняют → PUT не идёт. Координация с
    // self-heal/консолидацией — общие busy-refs (не PUT-им одновременно) + wait-loop.
    useEffect(() => {
        if (!draftId || geomState !== 'ready' || loading) return;
        if (redistBusyRef.current || consolidatingRef.current || selfHealBusyRef.current) return;
        if (prebookAcceptance.size === 0 || !preorderLoaded) return;   // ждём приёмку И whitelist
        if (prebook.length === 0) return;
        const isDead = (r: AssemblyDraftRow) => !r.as_is && Object.keys(r.tgt).some(wb => {
            const k = `${r.package_type || 'BOX'}::${wb}`;
            return checkedPkgWbs.has(k) && !readyPkgWbs.has(k) && !preorderWbs.has(wb);
        });
        if (!prebook.some(isDead)) return;
        // Прогон, не давший изменений, взводит sig и больше не бежит (до смены приёмки/whitelist/версии).
        const sig = `${draft?.updated_at || ''}|${[...checkedPkgWbs].sort().join(',')}|${[...readyPkgWbs].sort().join(',')}|${[...preorderWbs].sort().join(',')}`;
        if (redistSigRef.current === sig) return;
        redistSigRef.current = sig;
        redistBusyRef.current = true;
        let cancelled = false;
        void (async () => {
            try {
                while (selfHealBusyRef.current || consolidatingRef.current) await new Promise((r) => setTimeout(r, 120));
                if (cancelled) return;
                // Один item на баркод: суммируем цель по всем строкам предброни (as_is не трогаем).
                const bySku = new Map<string, { nm_id: number; barcode: string; distribution: Record<string, number> }>();
                for (const r of prebook) {
                    if (r.as_is) continue;
                    const key = `${r.nm_id}::${r.barcode}`;
                    const e = bySku.get(key) ?? { nm_id: r.nm_id, barcode: r.barcode, distribution: {} };
                    for (const [wb, q] of Object.entries(r.tgt)) e.distribution[wb] = (e.distribution[wb] || 0) + (q || 0);
                    bySku.set(key, e);
                }
                const items = [...bySku.values()].filter(it => Object.keys(it.distribution).length > 0);
                if (items.length === 0) return;
                const resp = await api.checkWbAcceptance({ items });
                if (cancelled) return;
                const byKey = new Map(resp.items.map(it => [`${it.nm_id}::${it.barcode}`, it]));
                const { rows: newPrebook, changed } = applyAcceptanceRedistToPrebook(prebook, byKey);
                if (!changed || cancelled) return;
                const merged = mergeDraftRows(newPrebook);
                const before: Record<string, number> = {};
                for (const r of prebook) for (const [wb, q] of Object.entries(r.tgt)) before[wb] = (before[wb] || 0) + (q || 0);
                const after: Record<string, number> = {};
                for (const r of merged) for (const [wb, q] of Object.entries(r.tgt)) after[wb] = (after[wb] || 0) + (q || 0);
                let updated;
                try {
                    updated = await api.updateAssemblyDraft(draftId, {
                        distribution: { ...buildDistribution(), prebook: merged },
                        base_updated_at: draftVersionRef.current || undefined,
                    });
                } catch (e) {
                    if (isVersionConflict(e)) {
                        try { applyDraft(await api.getAssemblyDraft(draftId)); } catch { /* ignore */ }
                        return;
                    }
                    throw e;
                }
                applyDraft(updated);
                const off: string[] = []; const onto: string[] = [];
                for (const wb of new Set([...Object.keys(before), ...Object.keys(after)])) {
                    const d = (after[wb] || 0) - (before[wb] || 0);
                    if (d < 0) off.push(`${wb} −${formatNumber(-d, 0)} шт`);
                    if (d > 0) onto.push(`${wb} +${formatNumber(d, 0)} шт`);
                }
                if (off.length || onto.length) {
                    showToast(`Предбронь синхронизирована с приёмкой WB — ⌛-склады без права предзаявки перераспределены: ${off.join(' · ')} → ${onto.join(' · ')}`, 'success');
                }
            } catch { /* best-effort — не блокируем страницу */ }
            finally {
                redistBusyRef.current = false;
                // StrictMode/отменённый прогон: sig уже взведён на ЭТУ версию, но PUT не
                // случился → без re-arm эффект не перезапустится сам. Сбрасываем sig, чтобы
                // следующая инвокация переоценила (idempotent: нет ⌛-не-whitelist → no-op).
                if (cancelled) redistSigRef.current = '';
            }
        })();
        return () => { cancelled = true; };
    }, [draftId, draft, geomState, loading, prebook, prebookAcceptance, preorderLoaded, checkedPkgWbs, readyPkgWbs, preorderWbs, buildDistribution, applyDraft, showToast]);

    // Whitelist «складов с разрешённой предзаявкой» — грузим один раз (для кнопки «Создать
    // предзаявку» И для авто-редистрибуции предброни). `preorderLoaded` отличает «ещё не
    // загружен» от «загружен пустой» — редистрибуция не должна считать ВСЁ не-whitelist до загрузки.
    useEffect(() => {
        let cancelled = false;
        void api.getPreorderAllowedWarehouses()
            .then(list => { if (!cancelled) { setPreorderWbs(new Set(list)); setPreorderLoaded(true); } })
            .catch(() => { if (!cancelled) setPreorderLoaded(true); /* пустой whitelist — кнопка неактивна */ });
        return () => { cancelled = true; };
    }, []);

    // Приёмка WB для предброни И ЧЕРНОВИКА: нужна консолидации (prebook→rows только
    // готовое) и ДЕМОЦИИ (rows→prebook не-готовых направлений — «черновик = готовое к
    // сдаче»), плюс карточкам для ⌛-бейджа. Сигнатура (nm × barcode × склады, без
    // количеств) — не перезапрашиваем без изменения структуры направлений.
    useEffect(() => {
        const id = setInterval(() => setAccRefreshTick(t => t + 1), 600_000);
        return () => clearInterval(id);
    }, []);
    useEffect(() => {
        const combined = [...rows, ...prebook];
        if (combined.length === 0) return;
        const sig = `${accRefreshTick}|` + combined.map(r => `${r.nm_id}:${r.barcode || ''}:${Object.keys(r.tgt).sort().join(',')}`).sort().join('|');
        if (sig === prebookAccSigRef.current || prebookAccFetchingRef.current) return;
        prebookAccFetchingRef.current = true;
        setPrebookAccLoading(true);
        void (async () => {
            try {
                // Один item на nm (строки rows/prebook одного nm сливаются): флаги приёмки —
                // уровня (склад × упаковка), количества на них не влияют.
                const byNm = new Map<number, { barcode: string; distribution: Record<string, number> }>();
                for (const r of combined) {
                    if (!r.barcode || Object.keys(r.tgt).length === 0) continue;
                    let e = byNm.get(r.nm_id);
                    if (!e) { e = { barcode: r.barcode, distribution: {} }; byNm.set(r.nm_id, e); }
                    for (const [wb, q] of Object.entries(r.tgt)) e.distribution[wb] = (e.distribution[wb] || 0) + (q || 0);
                }
                const items = [...byNm.entries()].map(([nm, e]) => ({ nm_id: nm, barcode: e.barcode, distribution: e.distribution }));
                if (items.length === 0) { prebookAccSigRef.current = sig; return; }
                const resp = await api.checkWbAcceptance({ items });
                const map = new Map<number, AcceptanceCheckPerItem>();
                for (const it of resp.items || []) map.set(it.nm_id, it);
                setPrebookAcceptance(map);
                prebookAccSigRef.current = sig;
            } catch { /* приёмка не критична — карточки покажут статус по клику */ }
            finally { prebookAccFetchingRef.current = false; setPrebookAccLoading(false); }
        })();
    }, [rows, prebook, accRefreshTick]);

    // Метки приёмки по направлению (`${pkg}::${wb}::${ffId}`) для бейджей предброни.
    // Флаги склада — уровня (склад × тип упаковки), одинаковы по SKU → берём первый с
    // данными. meta берём ИМЕННО для g.pkg (моно → mono_meta) — без priority-схлопа
    // «Потребности», поэтому моно-лимит виден корректно.
    const prebookAcceptanceMarks = useMemo<Map<string, PrebookAcceptanceMark>>(() => {
        const out = new Map<string, PrebookAcceptanceMark>();
        if (prebookAcceptance.size === 0) return out;
        for (const g of prebookGroups) {
            const key = `${g.pkg}::${g.wb}::${g.ffId}`;
            let flags: AcceptanceFlags | undefined;
            for (const it of g.items) {
                const f = prebookAcceptance.get(it.nm_id)?.availability?.[g.wb];
                if (f) { flags = f; break; }
            }
            if (!flags) { out.set(key, { checked: false, open: false, closed: false, noLimit: false }); continue; }
            const closed = !flags.can_box && !flags.can_monopallet && !flags.can_supersafe;
            const meta = g.pkg === 'MONOPALLET' ? flags.mono_meta : g.pkg === 'SUPERSAFE' ? flags.super_meta : flags.box_meta;
            const open = g.pkg === 'MONOPALLET' ? !!flags.can_monopallet : g.pkg === 'SUPERSAFE' ? !!flags.can_supersafe : !!flags.can_box;
            const freeDays = meta?.free_days_14;
            const paidDays = meta?.paid_days_14;
            const noLimit = open && (freeDays ?? 0) + (paidDays ?? 0) <= 0;
            out.set(key, { checked: true, open, closed, freeDays, paidDays, noLimit });
        }
        return out;
    }, [prebookGroups, prebookAcceptance]);

    // Удалить позицию (SKU×направление×ФФ) из предброни — коробы остаются на ФФ, не едут.
    // Снимается ТОЛЬКО порция этого ФФ (allocatePairs, как в handleDeleteDirection):
    // ✕ живёт в карточке ФФ-группы, а строка может сорсить wb с нескольких ФФ — снятие
    // всего tgt[wb] уносило бы порции чужих ФФ и перевирало привязку src к ФФ.
    const deletingItemRef = useRef(false);
    const handleDeletePrebookItem = useCallback(async (nm_id: number, wb: string, pkg: PackageType, ffId: number) => {
        // In-flight-гвард: два быстрых ✕ считали бы next от одного stale prebook —
        // второй PUT воскрешал бы первую удалённую позицию.
        if (!draftId || deletingItemRef.current) return;
        const chosenKey = String(ffId);
        const next = prebook
            .map(r => {
                if (r.nm_id !== nm_id || (r.package_type || 'BOX') !== pkg || !(r.tgt[wb] > 0) || !(r.src[chosenKey] > 0)) return r;
                const removed = allocatePairs(r.src, r.tgt).get(`${ffId}::${wb}`) || 0;
                if (removed <= 0) return r;
                const tgt = { ...r.tgt }; tgt[wb] = Math.max(0, (tgt[wb] || 0) - removed); if (tgt[wb] <= 0) delete tgt[wb];
                const src = { ...r.src }; src[chosenKey] = Math.max(0, (src[chosenKey] || 0) - removed); if (src[chosenKey] <= 0) delete src[chosenKey];
                return { ...r, tgt, src };
            })
            .filter(r => Object.keys(r.tgt).length > 0);
        deletingItemRef.current = true;
        try {
            const updated = await api.updateAssemblyDraft(draftId, {
                distribution: { ...buildDistribution(), prebook: next },
                event: { event_type: 'MATRIX_EDIT', summary: `Позиция nm ${nm_id} убрана из предброни «${wb}»` },
            });
            healScopeRef.current = { ts: updated.updated_at, only: new Set() }; // вычитающая → self-heal no-op
            applyDraft(updated);
            showToast('Удалено из предброни', 'success');
        } catch (e) { showToast(e instanceof Error ? e.message : 'Ошибка', 'error'); }
        finally { deletingItemRef.current = false; }
    }, [draftId, prebook, buildDistribution, applyDraft, showToast]);

    // Дозабить МИНИМАЛЬНО, с ОДНОГО ФФ: добираем целыми коробами до целых паллет
    // ровно на том ФФ, где лежит предбронь направления (короб с двух ФФ не собрать).
    // КОРОБ — смешанная паллета из ЛЮБЫХ box-SKU (склад принимает короб — доказано
    // тем, что предбронь туда уже разложена). МОНО — только SKU самой предброни.
    // Собранные целые паллеты уходят в черновик; хвост исходной предброни остаётся.
    const [toppingUp, setToppingUp] = useState<string | null>(null);
    const handleTopUpDirection = useCallback(async (pkg: PackageType, wb: string, ffId: number, cls?: string) => {
        if (!draftId) return;
        // НИКАКИХ тихих выходов: клик без реакции неотличим от «сломалось» (прод-кейс
        // «Дозабить из Газпром не реагирует» — геометрия молча упала в error).
        if (geomState !== 'ready') {
            showToast(geomState === 'error'
                ? 'Кратности коробов не загрузились — нажмите «↻ Повторить загрузку» в красном баннере сверху'
                : 'Кратности коробов ещё грузятся — подождите пару секунд и нажмите снова', 'error');
            return;
        }
        const chosenFf = ffId;
        const chosenKey = String(chosenFf);
        // Паллета собирается с ОДНОГО ФФ — работаем только с предбронью этого ФФ на wb.
        // `cls` (классы совместимости): дозабор целит в неполную паллету СВОЕГО класса —
        // порции и кандидаты только этого класса (ковёр не доложится пледами).
        const pbOnFf = prebook.filter(r => (r.package_type || 'BOX') === pkg && (r.tgt[wb] || 0) > 0 && (r.src[chosenKey] || 0) > 0
            && (!cls || classOf(r.nm_id) === cls));
        if (pbOnFf.length === 0) { showToast(`Предбронь «${wb}» уже изменилась (нет строк этого ФФ) — обновите страницу`, 'error'); return; }
        const key = `${pkg}::${wb}::${ffId}`;
        setToppingUp(key);
        try {
            const inUse: Record<number, Record<number, number>> = {};
            for (const r of [...rows, ...prebook]) { const m = (inUse[r.nm_id] ??= {}); for (const [ff, q] of Object.entries(r.src)) m[Number(ff)] = (m[Number(ff)] || 0) + (q || 0); }
            const pbNm = new Set(pbOnFf.map(r => r.nm_id));
            // ИСТИННЫЙ footprint предброни направления (Σ qty_i / upp_i, каждый SKU по своей
            // геометрии) — так же, как snapToWholePallets решает, что уедет. shortfall — доля
            // паллеты до целой; добираем ровно её (не по одному репрезентативному SKU).
            const uppAt = (nm: number): number => {
                const b = effectiveBoxesPerPallet(nmBoxSize.get(nm) ?? null, maxPalletHeightCm(wb), palletOverrides);
                const ppb = nmPpb.get(nm) || 0;
                return b && ppb ? b * ppb : 0;
            };
            // Порция именно chosenFf на wb (строка может сорсить wb с нескольких ФФ).
            const ffWbQty = (r: AssemblyDraftRow): number => allocatePairs(r.src, r.tgt).get(`${ffId}::${wb}`) || 0;
            const footprint = palletFootprint(pbOnFf.map(r => ({ nmId: r.nm_id, qty: ffWbQty(r) })), uppAt);
            const shortfallFp = Math.ceil(footprint) - footprint;
            // Пул кандидатов: box-SKU со свободным ФФ на chosenFf (топ по остатку).
            // effArticles — резерв других черновиков вычтен; скоуп/класс фильтруют пул.
            const pool = effArticles
                .map(a => {
                    const ppb = nmPpb.get(a.nm_id) || 0;
                    if (ppb <= 0 || !nmBoxSize.get(a.nm_id)) return null;
                    if (pkg !== 'BOX' && !pbNm.has(a.nm_id)) return null;
                    if (!inScope(a.nm_id)) return null;
                    if (cls && classOf(a.nm_id) !== cls) return null;
                    const freeBoxes = Math.floor(((a.rf_stocks?.[chosenFf]?.available || 0) - (inUse[a.nm_id]?.[chosenFf] || 0)) / ppb);
                    return freeBoxes > 0 ? { nm_id: a.nm_id, barcode: a.barcode, vendor_code: a.vendor_code, ppb, freeBoxes } : null;
                })
                .filter((x): x is NonNullable<typeof x> => x != null)
                .sort((a, b) => b.freeBoxes - a.freeBoxes)
                .slice(0, 30);
            // ПРОВЕРКА ПРИЁМКИ WB: SKU считается пригодным при открытом приёме И лимите
            // (дни>0) — «черновик = готовое к сдаче», ⌛-направления в черновик не едут.
            // Force-проверка может упасть (WB лимитит ~6 зап/мин, а страница фоново тоже
            // проверяет) → фолбэк на фоновый кэш приёмки (обновляется при изменениях).
            const okNm = new Set<number>();
            let dirOpen = false;
            let dirNoLimit = false;
            {
                // Приёмка из фонового кэша (НЕ force — суб-лимит 6/мин, серия кликов = 429;
                // см. коммент в handleShipPrebookAsIs).
                const accNm = [...pool.map(c => c.nm_id), ...pbOnFf.map(r => r.nm_id)];
                let perNm: Map<number, AcceptanceFlags | undefined> = new Map(
                    accNm.map(nm => [nm, prebookAcceptance.get(nm)?.availability?.[wb]]),
                );
                if (![...perNm.values()].some(Boolean)) {
                    try {
                        const items = [
                            ...pool.map(c => ({ nm_id: c.nm_id, barcode: c.barcode, distribution: { [wb]: c.ppb } })),
                            ...pbOnFf.map(r => ({ nm_id: r.nm_id, barcode: r.barcode, distribution: { [wb]: r.tgt[wb] || 0 } })),
                        ];
                        const resp = await api.checkWbAcceptance({ items });
                        perNm = new Map((resp.items || []).map(it => [it.nm_id, it.availability?.[wb]]));
                    } catch {
                        showToast('WB ограничивает частоту проверок приёмки — подождите минуту и нажмите ещё раз', 'error');
                        return;
                    }
                }
                for (const [nm, av] of perNm) {
                    const open = !!av && (pkg === 'BOX' ? !!av.can_box : pkg === 'MONOPALLET' ? !!av.can_monopallet : !!av.can_supersafe);
                    const meta = pkg === 'MONOPALLET' ? av?.mono_meta : pkg === 'SUPERSAFE' ? av?.super_meta : av?.box_meta;
                    const days = (meta?.free_days_14 ?? 0) + (meta?.paid_days_14 ?? 0);
                    if (open && days > 0) { okNm.add(nm); if (pbNm.has(nm)) dirOpen = true; }
                    else if (open && pbNm.has(nm)) dirNoLimit = true;
                }
            }
            // ⌛-режим (приём открыт, лимита нет): дозабор РАЗРЕШЁН, но результат остаётся
            // В ПРЕДБРОНИ — «черновик = готовое к сдаче» не нарушается, а целые паллеты
            // затем уходят «📋 Создать предзаявку» (ФФ собирает сейчас, слот WB — потом).
            // okNm не фильтруем: флаги приёмки — уровня (склад × упаковка), у ⌛-направления
            // они одинаково ⌛ для всех SKU пула.
            const prebookMode = !dirOpen && dirNoLimit;
            if (!dirOpen && !prebookMode) {
                showToast(`WB не принимает на «${wb}» — направление закрыто, дозабор невозможен`, 'error');
                return;
            }
            // Планируем добор целыми коробами по геометрии КАЖДОГО кандидата (короб = 1/bpp
            // паллеты) из ОТКРЫТЫХ по приёмке — ровно на shortfall до целой паллеты.
            const poolByNm = new Map(pool.map(c => [c.nm_id, c]));
            const plan = planTopUpBoxes(
                shortfallFp,
                pool.filter(c => prebookMode || okNm.has(c.nm_id)).map(c => ({
                    nmId: c.nm_id, ppb: c.ppb, freeBoxes: c.freeBoxes,
                    bpp: effectiveBoxesPerPallet(nmBoxSize.get(c.nm_id) ?? null, maxPalletHeightCm(wb), palletOverrides),
                })),
            );
            const candidates: AssemblyDraftRow[] = plan.rows.map(pr => {
                const c = poolByNm.get(pr.nmId)!;
                return { nm_id: c.nm_id, barcode: c.barcode, vendor_code: c.vendor_code, src: { [chosenKey]: pr.units }, tgt: { [wb]: pr.units }, package_type: pkg };
            });
            // В combined кладём ТОЛЬКО порцию (chosenFf→wb) каждой строки предброни, а не
            // строку целиком — иначе normalizeDraft затянул бы её другие склады/ФФ.
            const pbPortions: AssemblyDraftRow[] = pbOnFf
                .map(r => ({ nm_id: r.nm_id, barcode: r.barcode, vendor_code: r.vendor_code, src: { [chosenKey]: ffWbQty(r) }, tgt: { [wb]: ffWbQty(r) }, package_type: pkg }))
                .filter(r => (r.tgt[wb] || 0) > 0);
            const combined = [...pbPortions, ...candidates];
            const norm = normalizeDraft(combined, buildNormalizeCtx([...rows, ...combined]));
            // Россыпь запрещена всем (канон 2026-07-08): normalizeDraft исключений не
            // имеет — SKU без кратности (вкл. новинки) уходит в norm.dropped и ниже
            // возвращается в предбронь. norm.rows = только палетизированные строки.
            const palletizedRows = norm.rows;
            const keptUnits = palletizedRows.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
            if (keptUnits <= 0) { showToast('Не удалось дособрать целую паллету — не хватает свободного ФФ', 'error'); return; }
            // Новая предбронь: снимаем ТОЛЬКО порцию (chosenFf→wb) из затронутых строк
            // (остаток других складов/ФФ строки остаётся) + добавляем срез дозабора.
            const strippedPrebook = prebook
                .map(r => {
                    if ((r.package_type || 'BOX') !== pkg || !(r.tgt[wb] > 0) || !(r.src[chosenKey] > 0)) return r;
                    const removed = allocatePairs(r.src, r.tgt).get(`${ffId}::${wb}`) || 0;
                    if (removed <= 0) return r;
                    const tgt = { ...r.tgt }; tgt[wb] = Math.max(0, (tgt[wb] || 0) - removed); if (tgt[wb] <= 0) delete tgt[wb];
                    const src = { ...r.src }; src[chosenKey] = Math.max(0, (src[chosenKey] || 0) - removed); if (src[chosenKey] <= 0) delete src[chosenKey];
                    return { ...r, tgt, src };
                })
                .filter(r => Object.keys(r.tgt).length > 0);
            if (prebookMode) {
                // ⌛: паллетизированный результат остаётся В ПРЕДБРОНИ (rows не трогаем) —
                // авто-консолидация его не промотирует (направление не в readyPkgWbs),
                // целые паллеты уходят кнопкой «📋 Создать предзаявку».
                // Провенанс И для предброни: авто-синк матрицы заменил бы собранные
                // паллеты хвостом расчёта — ячейка помечается как ручное решение.
                for (const r of palletizedRows) prebookOriginRef.current.add(`${r.nm_id}::${wb}`);
                const nextPrebook = mergeDraftRows([...strippedPrebook, ...palletizedRows, ...norm.dropped.filter(r => pbNm.has(r.nm_id))]);
                const updated = await api.updateAssemblyDraft(draftId, { distribution: { ...buildDistribution(), prebook: nextPrebook }, event: { event_type: 'PREBOOK_TOPUP', summary: `Дозабор ⌛-направления «${wb}» (в предбронь)` } });
                healScopeRef.current = { ts: updated.updated_at, only: new Set() };
                applyDraft(updated);
                showToast(`Дособрано в предбронь «${wb}»: +${formatNumber(keptUnits, 0)} шт целыми паллетами (приём ⌛ — теперь «📋 Создать предзаявку»)`, 'success');
                return;
            }
            // Провенанс: дозабранные паллеты собраны с участием предброни направления.
            for (const r of palletizedRows) prebookOriginRef.current.add(`${r.nm_id}::${wb}`);
            const mergedRows = mergeDraftRows([...rows, ...palletizedRows]);
            const nextPrebook = [...strippedPrebook, ...norm.dropped.filter(r => pbNm.has(r.nm_id))];
            const targetNames = Array.from(new Set(mergedRows.flatMap(r => Object.keys(r.tgt))));
            const sourceIds = Array.from(new Set(mergedRows.flatMap(r => Object.keys(r.src).map(Number).filter(n => Number.isFinite(n) && n > 0))));
            const updated = await api.updateAssemblyDraft(draftId, { distribution: { ...buildDistribution(), rows: mergedRows, prebook: nextPrebook, source_warehouse_ids: sourceIds, target_warehouse_names: targetNames }, event: { event_type: 'PREBOOK_TOPUP', summary: 'Дозабор из предброни' } });
            healScopeRef.current = { ts: updated.updated_at, only: new Set([directionKey(pkg, wb)]) };
            applyDraft(updated);
            showToast(`Дособрано на «${wb}»: +${formatNumber(keptUnits, 0)} шт целыми паллетами`, 'success');
        } catch (e) { showToast(e instanceof Error ? e.message : 'Ошибка дозабивки', 'error'); }
        finally { setToppingUp(null); }
    }, [draftId, geomState, prebook, rows, effArticles, inScope, classOf, nmPpb, nmBoxSize, palletOverrides, prebookAcceptance, buildNormalizeCtx, buildDistribution, applyDraft, showToast]);

    // Отгрузить неполную паллету направления КАК ЕСТЬ: переносим предбронь этой отгрузки
    // (упаковка × склад × ФФ) в черновик частичной паллетой (без дозабора до целой) и
    // сохраняем НАПРЯМУЮ, без normalizeDraft (иначе он срезал бы неполную обратно).
    // Коммит паллеты не режет → частичная доедет в заявку.
    // ПРОВЕРКА ПРИЁМКИ WB (как в дозаборе): грузим только SKU с открытым приёмом И
    // лимитом (дни>0); прочие остаются в предброни; всё направление не готово → блок.
    // ⌛ (приём без лимита) ТОЖЕ блокирует: черновик = готовое к сдаче; моно — предзаявкой.
    const [shippingAsIs, setShippingAsIs] = useState<string | null>(null);
    const handleShipPrebookAsIs = useCallback(async (pkg: PackageType, wb: string, ffId: number) => {
        if (!draftId || shippingAsIs) return;
        const chosenKey = String(ffId);
        const key = `${pkg}::${wb}::${ffId}`;
        const affected = prebook.filter(r => (r.package_type || 'BOX') === pkg && (r.tgt[wb] || 0) > 0 && (r.src[chosenKey] || 0) > 0);
        if (affected.length === 0) return;
        setShippingAsIs(key);
        try {
            // Живая проверка приёмки по SKU направления: нужен приём И ЛИМИТ (дни>0).
            // «Черновик = готовое к сдаче»: ⌛ (приём без лимита) в черновик НЕ переносим —
            // моно сдаётся предзаявкой, короб ждёт лимита. При сбое force-проверки (WB
            // лимитит частоту) — фолбэк на фоновый кэш приёмки страницы.
            const okNm = new Set<number>();
            let openNoLimit = false;
            {
                // Приёмка из фонового кэша страницы (пер-баркод, ≤10 мин) — НЕ force.
                // force сидит на суб-лимите 6/мин: серия «Оставить так»/«Дозабить» его
                // выжигала → 429 и на кнопках, и на фоновой проверке (та переставала
                // грузиться → «черновик = готовое к сдаче» не досинхронивался, недобор
                // застревал в rows). Секундная свежесть коэффициентов для локального
                // переноса rows↔prebook не нужна — данные обновляются фоново каждые 10 мин.
                let perNm: Map<number, AcceptanceFlags | undefined> = new Map(
                    affected.map(r => [r.nm_id, prebookAcceptance.get(r.nm_id)?.availability?.[wb]]),
                );
                if (![...perNm.values()].some(Boolean)) {
                    // Кэш ещё не прогрет (приёмка не догрузилась) — ОДИН обычный запрос
                    // (не force): пер-баркод кэш бэка сам его закэширует.
                    try {
                        const items = affected.map(r => ({ nm_id: r.nm_id, barcode: r.barcode, distribution: { [wb]: allocatePairs(r.src, r.tgt).get(`${ffId}::${wb}`) || 0 } }));
                        const resp = await api.checkWbAcceptance({ items });
                        perNm = new Map((resp.items || []).map(it => [it.nm_id, it.availability?.[wb]]));
                    } catch {
                        showToast('WB ограничивает частоту проверок приёмки — подождите минуту и нажмите ещё раз', 'error');
                        return;
                    }
                }
                for (const [nm, av] of perNm) {
                    const open = !!av && (pkg === 'BOX' ? !!av.can_box : pkg === 'MONOPALLET' ? !!av.can_monopallet : !!av.can_supersafe);
                    const meta = pkg === 'MONOPALLET' ? av?.mono_meta : pkg === 'SUPERSAFE' ? av?.super_meta : av?.box_meta;
                    const days = (meta?.free_days_14 ?? 0) + (meta?.paid_days_14 ?? 0);
                    if (open && days > 0) okNm.add(nm);
                    else if (open) openNoLimit = true;
                }
            }
            if (okNm.size === 0) {
                showToast(openNoLimit
                    ? `«${wb}»: приём открыт, но ЛИМИТА нет (⌛) — в черновик не переносим (там только готовое к сдаче). Сборку можно запустить СЕЙЧАС кнопкой «📋 Создать предзаявку» (целые паллеты уйдут заявкой 🅿️, слот WB — при появлении лимита); хвост можно «Дозабить» — он останется в предброни.`
                    : `WB не принимает на «${wb}» — направление закрыто, отгрузка невозможна`, 'error');
                return;
            }

            const shipRows: AssemblyDraftRow[] = [];
            let skipped = 0;
            let looseLeft = 0;   // остаток россыпью, оставшийся в предброни (не кратен коробу)
            const nextPrebook = prebook
                .map(r => {
                    if ((r.package_type || 'BOX') !== pkg || !(r.tgt[wb] > 0) || !(r.src[chosenKey] > 0)) return r;
                    if (!okNm.has(r.nm_id)) { skipped += 1; return r; }   // закрытый SKU — остаётся в предброни
                    // Порция ИМЕННО этого ФФ на этот склад (строка может сорсить wb с
                    // нескольких ФФ) — берём allocatePairs, а не весь tgt[wb], иначе
                    // перелили бы с одного ФФ и выкинули порцию другого.
                    const portion = allocatePairs(r.src, r.tgt).get(`${ffId}::${wb}`) || 0;
                    if (portion <= 0) return r;
                    // Отгружаем только ЦЕЛЫЕ коробы (кратность этого ФФ, фолбэк — глобальная);
                    // остаток < короба остаётся в предброни. РОССЫПЬ ЗАПРЕЩЕНА ВСЕМ (канон
                    // 2026-07-08): SKU без кратности не отгружается и через «Оставить так».
                    const ppb = nmPpbByWh.get(r.nm_id)?.[ffId] ?? (nmPpb.get(r.nm_id) || 0);
                    const moved = ppb > 0 ? Math.floor(portion / ppb) * ppb : 0;
                    if (moved <= 0) { looseLeft += portion; return r; }  // целого короба нет — остаётся остатком
                    looseLeft += portion - moved;
                    // as_is: сознательно отгруженная частичная — self-heal её не откатывает.
                    shipRows.push({ nm_id: r.nm_id, barcode: r.barcode, vendor_code: r.vendor_code, src: { [chosenKey]: moved }, tgt: { [wb]: moved }, package_type: pkg, as_is: true });
                    const tgt = { ...r.tgt }; tgt[wb] = Math.max(0, (tgt[wb] || 0) - moved); if (tgt[wb] <= 0) delete tgt[wb];
                    const src = { ...r.src }; src[chosenKey] = Math.max(0, (src[chosenKey] || 0) - moved); if (src[chosenKey] <= 0) delete src[chosenKey];
                    return { ...r, tgt, src };
                })
                .filter(r => Object.keys(r.tgt).length > 0);
            if (shipRows.length === 0) {
                // Смешанный случай честно перечисляет обе причины (часть SKU закрыта WB,
                // у остальных нет целого короба) — направление целиком остаётся в предброни.
                const reasons = [
                    skipped ? `⛔ ${formatNumber(skipped, 0)} SKU закрыты WB` : '',
                    looseLeft ? 'нет целых коробов (россыпь запрещена — задайте кратность)' : '',
                ].filter(Boolean).join(' · ');
                showToast(`Отгружать нечего: ${reasons || 'нет порций этого ФФ'} — направление остаётся в предброни`, 'error');
                return;
            }
            const movedUnits = shipRows.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
            // Провенанс: эти строки уехали в черновик ИЗ предброни («Оставить так»).
            for (const r of shipRows) prebookOriginRef.current.add(`${r.nm_id}::${wb}`);
            const mergedRows = mergeDraftRows([...rows, ...shipRows]);
            const targetNames = Array.from(new Set(mergedRows.flatMap(r => Object.keys(r.tgt))));
            const sourceIds = Array.from(new Set(mergedRows.flatMap(r => Object.keys(r.src).map(Number).filter(n => Number.isFinite(n) && n > 0))));
            const updated = await api.updateAssemblyDraft(draftId, { distribution: { ...buildDistribution(), rows: mergedRows, prebook: nextPrebook, source_warehouse_ids: sourceIds, target_warehouse_names: targetNames }, event: { event_type: 'MATRIX_EDIT', summary: `«Оставить так»: неполная паллета «${wb}» → в черновик (as_is)` } });
            healScopeRef.current = { ts: updated.updated_at, only: new Set([directionKey(pkg, wb)]) };
            applyDraft(updated);
            showToast(
                `Отгружено как есть на «${wb}» (неполная паллета): +${formatNumber(movedUnits, 0)} шт целыми коробами`
                + (looseLeft ? ` · остаток ${formatNumber(looseLeft, 0)} шт остался в предброни (неполный короб / без кратности)` : '')
                + (skipped ? ` · ⛔ ${formatNumber(skipped, 0)} SKU закрыты WB — оставлены в предброни` : ''),
                'success',
            );
        } catch (e) { showToast(e instanceof Error ? e.message : 'Ошибка отгрузки', 'error'); }
        finally { setShippingAsIs(null); }
    }, [draftId, shippingAsIs, prebook, rows, nmPpb, nmPpbByWh, prebookAcceptance, buildDistribution, applyDraft, showToast]);

    // Удалить ВСЁ направление (упаковка × склад × ФФ) из предброни одним действием.
    // Снимаем порцию (ffId→wb) со ВСЕХ строк направления (скоуп по ffId через allocatePairs,
    // не весь tgt[wb] — иначе при мульти-ФФ строке снесли бы порцию чужого ФФ). Мутируем
    // ТОЛЬКО prebook (rows не трогаем — иначе снесли бы собранные паллеты черновика);
    // освобождённые коробы автоматически станут свободны на ФФ (inUse считается по prebook).
    const [deletingDir, setDeletingDir] = useState<string | null>(null);
    const handleDeleteDirection = useCallback(async (pkg: PackageType, wb: string, ffId: number) => {
        if (!draftId || deletingDir) return;
        const key = `${pkg}::${wb}::${ffId}`;
        const grp = prebookGroups.find(g => g.pkg === pkg && g.wb === wb && g.ffId === ffId);
        const ffLabel = grp?.ff || (ffId >= 0 ? `ФФ ${ffId}` : '—');
        if (!window.confirm(
            `Удалить всё направление «${wb}» (ФФ «${ffLabel}») из предброни?`
            + (grp ? ` ${formatNumber(grp.boxes, 0)} кор · ${formatNumber(grp.qty, 0)} шт.` : '')
            + ' Коробы останутся свободными на ФФ.',
        )) return;
        const chosenKey = String(ffId);
        setDeletingDir(key);
        try {
            const next = prebook
                .map(r => {
                    if ((r.package_type || 'BOX') !== pkg || !(r.tgt[wb] > 0) || !(r.src[chosenKey] > 0)) return r;
                    const removed = allocatePairs(r.src, r.tgt).get(`${ffId}::${wb}`) || 0;
                    if (removed <= 0) return r;
                    const tgt = { ...r.tgt }; tgt[wb] = Math.max(0, (tgt[wb] || 0) - removed); if (tgt[wb] <= 0) delete tgt[wb];
                    const src = { ...r.src }; src[chosenKey] = Math.max(0, (src[chosenKey] || 0) - removed); if (src[chosenKey] <= 0) delete src[chosenKey];
                    return { ...r, tgt, src };
                })
                .filter(r => Object.keys(r.tgt).length > 0);
            const updated = await api.updateAssemblyDraft(draftId, { distribution: { ...buildDistribution(), prebook: next }, event: { event_type: 'MATRIX_EDIT', summary: `Направление «${wb}» удалено из предброни` } });
            healScopeRef.current = { ts: updated.updated_at, only: new Set() }; // вычитающая → self-heal no-op
            applyDraft(updated);
            showToast(`Направление «${wb}» удалено из предброни — коробы свободны на ФФ`, 'success');
        } catch (e) { showToast(e instanceof Error ? e.message : 'Ошибка удаления', 'error'); }
        finally { setDeletingDir(null); }
    }, [draftId, deletingDir, prebook, prebookGroups, buildDistribution, applyDraft, showToast]);

    // Создать ПРЕДЗАЯВКУ: готовые целые паллеты (моно ИЛИ короб) на WB-склад без лимита
    // приёмки (⌛) сдаются предзаявкой (бронью). Создаём заявки на сборку сразу с флагом
    // is_prebooking (бэк, обычный сток) и убираем их из предброни. Скоуп по (ffId→wb).
    // Короб — тот же поток: ⌛-направление иначе тупик («Оставить так»/«Дозабить»
    // гейтует «черновик = готовое к сдаче»), а ФФ должен начать сборку сейчас, не
    // дожидаясь лимита. В БРОНЬ УХОДЯТ ТОЛЬКО ЦЕЛЫЕ ПАЛЛЕТЫ (авторитетный трим-сплит,
    // моно ≤3 арт, короб per-класс) — неполный хвост остаётся в предброни.
    const [creatingPrebooking, setCreatingPrebooking] = useState<string | null>(null);
    const handleCreatePrebooking = useCallback(async (pkg: PackageType, wb: string, ffId: number) => {
        if (!draftId || creatingPrebooking || (pkg !== 'MONOPALLET' && pkg !== 'BOX')) return;
        const chosenKey = String(ffId);
        const pkgWord = pkg === 'MONOPALLET' ? 'моно' : 'короба';
        const affected = prebook.filter(r => (r.package_type || 'BOX') === pkg && (r.tgt[wb] || 0) > 0 && (r.src[chosenKey] || 0) > 0);
        if (affected.length === 0) return;
        // Порции ИМЕННО этого направления (ffId→wb) — строка может сорсить wb с неск. ФФ.
        const dirPortions: AssemblyDraftRow[] = affected
            .map((r): AssemblyDraftRow | null => { const q = allocatePairs(r.src, r.tgt).get(`${ffId}::${wb}`) || 0; return q > 0 ? { nm_id: r.nm_id, barcode: r.barcode, vendor_code: r.vendor_code, src: { [chosenKey]: q }, tgt: { [wb]: q }, package_type: pkg } : null; })
            .filter((x): x is AssemblyDraftRow => x != null);
        if (dirPortions.length === 0) return;
        // Целые паллеты (в бронь) vs хвост (остаётся) — тем же сплитом, что консолидация/коммит.
        const split = consolidatePrebookWholePallets(dirPortions, buildNormalizeCtx([...rows, ...prebook]));
        const wholeUnits = split.toDraft.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
        if (wholeUnits <= 0) { showToast('Нет целой паллеты для предзаявки — сначала дозаберите хвост до целой или уберите неполную', 'error'); return; }
        const tailUnits = split.prebook.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
        if (!window.confirm(
            `Создать предзаявку на ${pkgWord} «${wb}»? В бронь уйдут ТОЛЬКО целые паллеты (${formatNumber(wholeUnits, 0)} шт) — ФФ начнёт сборку сразу, слот WB бронируется при появлении лимита.`
            + (tailUnits ? ` Неполный хвост (${formatNumber(tailUnits, 0)} шт) останется в предброни.` : ''),
        )) return;
        const key = `${pkg}::${wb}::${ffId}`;
        setCreatingPrebooking(key);
        try {
            const bookRows = split.toDraft
                .map(r => ({ warehouse_id: ffId, barcode: r.barcode, wb_warehouse_name: wb, qty: r.tgt[wb] || 0, package_type: pkg }))
                .filter(r => r.qty > 0);
            if (bookRows.length === 0) return;
            const res = await api.createPrebooking({ rows: bookRows });
            // Снимаем ВСЮ порцию направления из предброни, возвращаем ТОЛЬКО хвост (не целые).
            const stripped = prebook
                .map(r => {
                    if ((r.package_type || 'BOX') !== pkg || !(r.tgt[wb] > 0) || !(r.src[chosenKey] > 0)) return r;
                    const moved = allocatePairs(r.src, r.tgt).get(`${ffId}::${wb}`) || 0;
                    if (moved <= 0) return r;
                    const tgt = { ...r.tgt }; tgt[wb] = Math.max(0, (tgt[wb] || 0) - moved); if (tgt[wb] <= 0) delete tgt[wb];
                    const src = { ...r.src }; src[chosenKey] = Math.max(0, (src[chosenKey] || 0) - moved); if (src[chosenKey] <= 0) delete src[chosenKey];
                    return { ...r, tgt, src };
                })
                .filter(r => Object.keys(r.tgt).length > 0);
            const nextPrebook = mergeDraftRows([...stripped, ...split.prebook]);
            // Полу-успех НЕ атомарен: бронь уже создана. Падение PUT очистки предброни
            // без явного сигнала провоцировало повторный клик → ДУБЛЬ предзаявки.
            try {
                const updated = await api.updateAssemblyDraft(draftId, { distribution: { ...buildDistribution(), prebook: nextPrebook }, event: { event_type: 'MATRIX_EDIT', summary: `Предзаявка (${pkgWord}) «${wb}»: целые паллеты убраны из предброни` } });
                healScopeRef.current = { ts: updated.updated_at, only: new Set() }; // бронь убирает из предброни, rows не трогает
                applyDraft(updated);
            } catch {
                showToast(`⚠ Предзаявка на «${wb}» СОЗДАНА, но предбронь не очистилась — НЕ жмите кнопку повторно (будет дубль брони). Удалите направление из предброни вручную`, 'error');
                return;
            }
            const numbers = (res.requests || []).map(r => r.number).filter(Boolean);
            showToast(
                `Предзаявка на ${pkgWord} «${wb}» создана: ${formatNumber(res.created, 0)} заявок ${numbers.length ? numbers.join(', ') : ''} (${formatNumber(wholeUnits, 0)} шт целыми паллетами)`
                + (tailUnits ? ` · хвост ${formatNumber(tailUnits, 0)} шт остался в предброни` : '')
                + ' — ищите их в списке «Заявки на сборку» с бейджем 🅿️',
                'success',
            );
        } catch (e) { showToast(e instanceof Error ? e.message : 'Ошибка создания предзаявки', 'error'); }
        finally { setCreatingPrebooking(null); }
    }, [draftId, creatingPrebooking, prebook, rows, buildNormalizeCtx, buildDistribution, applyDraft, showToast]);

    // «Дозабить до целой» (под-вкладка «Предзаявка»): добрать МОНО-хвост целыми коробами
    // из свободного ФФ ЭТОГО направления до ещё одной целой паллеты. Результат ОСТАЁТСЯ в
    // предброни (⌛-моно под предзаявку, НЕ в черновик). Кандидаты = SKU направления (уже
    // приняты WB как моно → повторную приёмку не дёргаем; лимит ⌛ снимается предзаявкой).
    const [toppingUpPrebook, setToppingUpPrebook] = useState<string | null>(null);
    const handleTopUpPrebookMono = useCallback(async (wb: string, ffId: number) => {
        if (!draftId || toppingUpPrebook || ffId < 0) return;
        const g = prebookGroups.find(x => x.pkg === 'MONOPALLET' && x.wb === wb && x.ffId === ffId);
        if (!g || !g.tailTopUp) return;
        const chosenKey = String(ffId);
        const key = `MONOPALLET::${wb}::${ffId}`;
        setToppingUpPrebook(key);
        try {
            // Прицел дозабора = ТОП-3 крупнейших хвоста недобора (правило «≤3 арт на
            // моно-паллете»): объёмная цель «1 − дробь» по ВСЕМ артикулам направления
            // размазывала добор по >3 SKU — целая паллета не собиралась никогда.
            const km: Record<string, number> = {};
            for (const i of g.items) km[String(i.nm_id)] = (km[String(i.nm_id)] || 0) + i.qty;
            const ppbAtFf = (nm: number): number => nmPpbByWh.get(nm)?.[ffId] ?? (nmPpb.get(nm) || 0);
            const uppAt = (nm: number): number => {
                const b = effectiveBoxesPerPallet(nmBoxSize.get(nm) ?? null, maxPalletHeightCm(wb), palletOverrides);
                const ppb = ppbAtFf(nm);
                return b && ppb ? b * ppb : 0;
            };
            const pm = packMonoPallets(km, (k) => uppAt(Number(k)) || null, MONO_MAX_PALLET_ARTICLES, (k) => ppbAtFf(Number(k)));
            const tails = Object.entries(pm.dropped)
                .map(([k, v]) => { const u = uppAt(Number(k)); return { nm: Number(k), fp: u > 0 ? v / u : 0 }; })
                .filter(t => t.fp > 0)
                .sort((a, b) => b.fp - a.fp)
                .slice(0, MONO_MAX_PALLET_ARTICLES);
            const topFp = tails.reduce((s, t) => s + t.fp, 0);
            const dirNm = new Set(tails.map(t => t.nm));
            const inUse: Record<number, Record<number, number>> = {};
            for (const r of [...rows, ...prebook]) { const m = (inUse[r.nm_id] ??= {}); for (const [ff, q] of Object.entries(r.src)) m[Number(ff)] = (m[Number(ff)] || 0) + (q || 0); }
            // effArticles: резерв других черновиков вычтен, ФФ-ограничение скоупа
            // применено (ревью HIGH: сырой stockNeed тянул чужие коробы).
            const pool = effArticles
                .filter(a => dirNm.has(a.nm_id) && inScope(a.nm_id))
                .map(a => {
                    const ppb = nmPpb.get(a.nm_id) || 0;
                    if (ppb <= 0 || !nmBoxSize.get(a.nm_id)) return null;
                    const freeBoxes = Math.floor(((a.rf_stocks?.[ffId]?.available || 0) - (inUse[a.nm_id]?.[ffId] || 0)) / ppb);
                    if (freeBoxes <= 0) return null;
                    return { nmId: a.nm_id, ppb, freeBoxes, bpp: effectiveBoxesPerPallet(nmBoxSize.get(a.nm_id) ?? null, maxPalletHeightCm(wb), palletOverrides), barcode: a.barcode, vendor_code: a.vendor_code || '' };
                })
                .filter((x): x is TopUpCandidate & { barcode: string; vendor_code: string } => x != null)
                .sort((a, b) => b.freeBoxes - a.freeBoxes);
            const plan = planTopUpBoxes(topFp > 1e-9 ? 1 - topFp : 0, pool);
            if (!plan.feasible || plan.rows.length === 0) { showToast('Нечем дозабрать до целой — не хватает свободного ФФ на артикулы направления', 'error'); return; }
            const byNm = new Map(pool.map(c => [c.nmId, c]));
            const additions: AssemblyDraftRow[] = plan.rows.map(pr => {
                const c = byNm.get(pr.nmId)!;
                return { nm_id: pr.nmId, barcode: c.barcode, vendor_code: c.vendor_code, src: { [chosenKey]: pr.units }, tgt: { [wb]: pr.units }, package_type: 'MONOPALLET' as PackageType };
            });
            const addedUnits = additions.reduce((s, r) => s + (r.tgt[wb] || 0), 0);
            // Провенанс: дозабранные моно-коробы — ручное решение, авто-синк матрицы
            // не должен откатывать ячейку к хвосту расчёта.
            for (const r of additions) prebookOriginRef.current.add(`${r.nm_id}::${wb}`);
            const nextPrebook = mergeDraftRows([...prebook, ...additions]);
            const updated = await api.updateAssemblyDraft(draftId, { distribution: { ...buildDistribution(), prebook: nextPrebook }, event: { event_type: 'PREBOOK_TOPUP', summary: 'Дозабор из предброни' } });
            healScopeRef.current = { ts: updated.updated_at, only: new Set([directionKey('MONOPALLET', wb)]) };
            applyDraft(updated);
            showToast(`Дозаброшено в предбронь «${wb}»: +${formatNumber(addedUnits, 0)} шт целыми коробами до целой паллеты`, 'success');
        } catch (e) { showToast(e instanceof Error ? e.message : 'Ошибка дозабора', 'error'); }
        finally { setToppingUpPrebook(null); }
    }, [draftId, toppingUpPrebook, prebookGroups, rows, prebook, effArticles, inScope, nmPpb, nmPpbByWh, nmBoxSize, palletOverrides, buildDistribution, applyDraft, showToast]);

    // «Убрать неполную» (под-вкладка «Предзаявка»): оставить только ЦЕЛЫЕ моно-паллеты
    // направления, неполный хвост убрать (освободить на ФФ). Сплит — авторитетный (≤3 арт).
    const [trimmingTail, setTrimmingTail] = useState<string | null>(null);
    const handleTrimPrebookTail = useCallback(async (wb: string, ffId: number) => {
        if (!draftId || trimmingTail || ffId < 0) return;
        const chosenKey = String(ffId);
        const dirPortions: AssemblyDraftRow[] = prebook
            .filter(r => (r.package_type || 'BOX') === 'MONOPALLET' && (r.tgt[wb] || 0) > 0 && (r.src[chosenKey] || 0) > 0)
            .map((r): AssemblyDraftRow | null => { const q = allocatePairs(r.src, r.tgt).get(`${ffId}::${wb}`) || 0; return q > 0 ? { nm_id: r.nm_id, barcode: r.barcode, vendor_code: r.vendor_code, src: { [chosenKey]: q }, tgt: { [wb]: q }, package_type: 'MONOPALLET' as PackageType } : null; })
            .filter((x): x is AssemblyDraftRow => x != null);
        if (dirPortions.length === 0) return;
        const split = consolidatePrebookWholePallets(dirPortions, buildNormalizeCtx([...rows, ...prebook]));
        const tailUnits = split.prebook.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
        if (tailUnits <= 0) { showToast('Неполной паллеты нет — направление уже целыми паллетами', 'success'); return; }
        if (!window.confirm(`Убрать неполную паллету на «${wb}» (${formatNumber(tailUnits, 0)} шт)? Останутся только целые паллеты; хвост освободится на ФФ.`)) return;
        const key = `MONOPALLET::${wb}::${ffId}`;
        setTrimmingTail(key);
        try {
            const stripped = prebook
                .map(r => {
                    if ((r.package_type || 'BOX') !== 'MONOPALLET' || !(r.tgt[wb] > 0) || !(r.src[chosenKey] > 0)) return r;
                    const removed = allocatePairs(r.src, r.tgt).get(`${ffId}::${wb}`) || 0;
                    if (removed <= 0) return r;
                    const tgt = { ...r.tgt }; tgt[wb] = Math.max(0, (tgt[wb] || 0) - removed); if (tgt[wb] <= 0) delete tgt[wb];
                    const src = { ...r.src }; src[chosenKey] = Math.max(0, (src[chosenKey] || 0) - removed); if (src[chosenKey] <= 0) delete src[chosenKey];
                    return { ...r, tgt, src };
                })
                .filter(r => Object.keys(r.tgt).length > 0);
            // Возвращаем в предбронь ТОЛЬКО целые паллеты (split.toDraft) — ⌛-моно под
            // предзаявку. Хвост (split.prebook) НЕ возвращаем → освобождается на ФФ.
            const nextPrebook = mergeDraftRows([...stripped, ...split.toDraft]);
            const updated = await api.updateAssemblyDraft(draftId, { distribution: { ...buildDistribution(), prebook: nextPrebook }, event: { event_type: 'MATRIX_EDIT', summary: `Неполная моно-паллета «${wb}» убрана из предброни` } });
            healScopeRef.current = { ts: updated.updated_at, only: new Set() }; // вычитающая → self-heal no-op
            applyDraft(updated);
            const wholeKept = split.toDraft.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
            showToast(
                `Неполная паллета убрана с «${wb}»: ${formatNumber(tailUnits, 0)} шт освобождены на ФФ`
                + (wholeKept ? ` · целые паллеты (${formatNumber(wholeKept, 0)} шт) остались в предброни (если приём WB открыт — уедут в черновик автоматически)` : ''),
                'success',
            );
        } catch (e) { showToast(e instanceof Error ? e.message : 'Ошибка', 'error'); }
        finally { setTrimmingTail(null); }
    }, [draftId, trimmingTail, prebook, rows, buildNormalizeCtx, buildDistribution, applyDraft, showToast]);

    // ── ПОПАЛЛЕТНЫЕ действия (моно): предзаявка / освобождение выбранных паллет
    // раскладки (целых и частичных — юзер решает сам; можно НЕСКОЛЬКО за раз, тогда
    // предзаявка уходит ОДНОЙ заявкой и всё укладывается в один запрос + один PUT —
    // не съедает write-лимит). Снимаем из предброни РОВНО units паллет per nm
    // (порция ffId→wb через allocatePairs — мульти-src цел).
    const [palletOp, setPalletOp] = useState<string | null>(null);
    const stripPalletsFromPrebook = useCallback((pallets: PrebookMonoPallet[], wb: string, ffId: number): AssemblyDraftRow[] => {
        const chosenKey = String(ffId);
        const need = new Map<number, number>();
        for (const p of pallets) for (const it of p.items) need.set(it.nm_id, (need.get(it.nm_id) || 0) + it.units);
        return prebook
            .map(r => {
                if ((r.package_type || 'BOX') !== 'MONOPALLET' || !(r.tgt[wb] > 0) || !(r.src[chosenKey] > 0)) return r;
                const want = need.get(r.nm_id) || 0;
                if (want <= 0) return r;
                const portion = allocatePairs(r.src, r.tgt).get(`${ffId}::${wb}`) || 0;
                const take = Math.min(portion, want);
                if (take <= 0) return r;
                need.set(r.nm_id, want - take);
                const tgt = { ...r.tgt }; tgt[wb] = Math.max(0, (tgt[wb] || 0) - take); if (tgt[wb] <= 0) delete tgt[wb];
                const src = { ...r.src }; src[chosenKey] = Math.max(0, (src[chosenKey] || 0) - take); if (src[chosenKey] <= 0) delete src[chosenKey];
                return { ...r, tgt, src };
            })
            .filter(r => Object.keys(r.tgt).length > 0);
    }, [prebook]);

    const palletsLabel = (sel: { pallet: PrebookMonoPallet; palletNo: number }[]) =>
        sel.map(s => `№${s.palletNo}`).join(', ');

    // Предзаявка из выбранных паллет раскладки ОДНОЙ заявкой (частичные — по явному
    // решению юзера, confirm называет их количество).
    const handleBookPallets = useCallback(async (wb: string, ffId: number, sel: { pallet: PrebookMonoPallet; palletNo: number }[]) => {
        if (!draftId || palletOp || ffId < 0 || sel.length === 0) return;
        const units = sel.reduce((s, x) => s + x.pallet.items.reduce((a, it) => a + it.units, 0), 0);
        if (units <= 0) return;
        // Один SKU может лежать на нескольких выбранных паллетах — предзаявке отдаём
        // суммарные units per nm (backend группирует по ФФ×WB×упаковке → одна заявка).
        const unitsByNm = new Map<number, number>();
        for (const x of sel) for (const it of x.pallet.items) unitsByNm.set(it.nm_id, (unitsByNm.get(it.nm_id) || 0) + it.units);
        // Порции per (nm × БАРКОД) — зеркало stripPalletsFromPrebook (тот же порядок
        // обхода prebook и то же жадное min(portion, want)): у мульти-баркодного SKU
        // units уходят в бронь под ТЕМИ ЖЕ баркодами, что снимутся из предброни.
        // «Первый найденный баркод на все штуки nm» бронировал бы чужой размерный
        // вариант (ФФ собирает по баркоду — физически не тот товар).
        const remainingBook = new Map(unitsByNm);
        const bookByNmBc = new Map<string, { barcode: string; qty: number }>();
        for (const r of prebook) {
            if ((r.package_type || 'BOX') !== 'MONOPALLET' || !r.barcode) continue;
            if (!(r.tgt[wb] > 0) || !(r.src[String(ffId)] > 0)) continue;
            const want = remainingBook.get(r.nm_id) || 0;
            if (want <= 0) continue;
            const portion = allocatePairs(r.src, r.tgt).get(`${ffId}::${wb}`) || 0;
            const take = Math.min(portion, want);
            if (take <= 0) continue;
            remainingBook.set(r.nm_id, want - take);
            const k = `${r.nm_id}::${r.barcode}`;
            const e = bookByNmBc.get(k);
            if (e) e.qty += take; else bookByNmBc.set(k, { barcode: r.barcode, qty: take });
        }
        const bookRows = [...bookByNmBc.values()]
            .map(x => ({ warehouse_id: ffId, barcode: x.barcode, wb_warehouse_name: wb, qty: x.qty, package_type: 'MONOPALLET' as PackageType }))
            .filter(r => r.qty > 0 && r.barcode);
        if (bookRows.length === 0) { showToast('Не нашёл баркоды артикулов паллет в предброни', 'error'); return; }
        const partials = sel.filter(x => x.pallet.fillPct < 0.999).length;
        const artCount = unitsByNm.size;
        const confirmText = sel.length === 1
            ? `Создать предзаявку на «${wb}» из паллеты ${palletsLabel(sel)}${partials ? ` — ЧАСТИЧНАЯ (${Math.round(sel[0].pallet.fillPct * 100)}%)` : ' (целая)'}?`
              + ` ${formatNumber(units, 0)} шт · ${formatNumber(artCount, 0)} арт. Остальные паллеты направления останутся в предброни.`
            : `Создать ОДНУ предзаявку на «${wb}» из ${formatNumber(sel.length, 0)} паллет (${palletsLabel(sel)})${partials ? ` — в т.ч. ${formatNumber(partials, 0)} частичн.` : ''}?`
              + ` Всего ${formatNumber(units, 0)} шт · ${formatNumber(artCount, 0)} арт. Остальные паллеты направления останутся в предброни.`;
        if (!window.confirm(confirmText)) return;
        const key = `${wb}::${ffId}::#${sel.map(x => x.palletNo).join('+')}`;
        setPalletOp(key);
        try {
            const res = await api.createPrebooking({ rows: bookRows });
            const nextPrebook = stripPalletsFromPrebook(sel.map(x => x.pallet), wb, ffId);
            // Полу-успех НЕ атомарен: бронь уже создана — падение PUT очистки без
            // явного сигнала провоцировало повторный клик → дубль предзаявки.
            try {
                const updated = await api.updateAssemblyDraft(draftId, { distribution: { ...buildDistribution(), prebook: nextPrebook }, event: { event_type: 'MATRIX_EDIT', summary: `Предзаявка из паллет «${wb}»: убраны из предброни` } });
                healScopeRef.current = { ts: updated.updated_at, only: new Set() }; // бронь убирает из предброни, rows не трогает
                applyDraft(updated);
            } catch {
                showToast(`⚠ Предзаявка на «${wb}» СОЗДАНА, но паллеты не убрались из предброни — НЕ жмите кнопку повторно (будет дубль брони). Уберите их вручную («На ФФ»)`, 'error');
                return;
            }
            const numbers = (res.requests || []).map(r => r.number).filter(Boolean);
            showToast(
                `Предзаявка из ${sel.length === 1 ? `паллеты ${palletsLabel(sel)}` : `${formatNumber(sel.length, 0)} паллет (${palletsLabel(sel)})`} на «${wb}» создана: заявка ${numbers.join(', ')} (${formatNumber(units, 0)} шт${partials ? `, ${formatNumber(partials, 0)} частичн.` : ''})`
                + ' — ищите в списке «Заявки на сборку» с бейджем 🅿️',
                'success',
            );
        } catch (e) { showToast(e instanceof Error ? e.message : 'Ошибка создания предзаявки', 'error'); }
        finally { setPalletOp(null); }
    }, [draftId, palletOp, prebook, stripPalletsFromPrebook, buildDistribution, applyDraft, showToast]);

    // Освободить выбранные паллеты раскладки на ФФ (убрать их содержимое из предброни,
    // остальные паллеты направления не трогаем) — один PUT на всю пачку.
    const handleReleasePallets = useCallback(async (wb: string, ffId: number, sel: { pallet: PrebookMonoPallet; palletNo: number }[]) => {
        if (!draftId || palletOp || ffId < 0 || sel.length === 0) return;
        const units = sel.reduce((s, x) => s + x.pallet.items.reduce((a, it) => a + it.units, 0), 0);
        if (units <= 0) return;
        const arts = new Set(sel.flatMap(x => x.pallet.items.map(it => it.nm_id))).size;
        const what = sel.length === 1 ? `паллету ${palletsLabel(sel)}` : `${formatNumber(sel.length, 0)} паллет (${palletsLabel(sel)})`;
        if (!window.confirm(`Оставить ${what} на ФФ (${formatNumber(units, 0)} шт · ${formatNumber(arts, 0)} арт.)? ${sel.length === 1 ? 'Она уйдёт' : 'Они уйдут'} из предброни, коробы освободятся; остальные паллеты направления останутся.`)) return;
        const key = `${wb}::${ffId}::#${sel.map(x => x.palletNo).join('+')}`;
        setPalletOp(key);
        try {
            const nextPrebook = stripPalletsFromPrebook(sel.map(x => x.pallet), wb, ffId);
            const updated = await api.updateAssemblyDraft(draftId, { distribution: { ...buildDistribution(), prebook: nextPrebook }, event: { event_type: 'MATRIX_EDIT', summary: `Паллеты «${wb}» оставлены на ФФ (убраны из предброни)` } });
            healScopeRef.current = { ts: updated.updated_at, only: new Set() }; // «На ФФ» — вычитающая → self-heal no-op, соседей не трогаем
            applyDraft(updated);
            showToast(`${sel.length === 1 ? `Паллета ${palletsLabel(sel)}` : `Паллеты ${palletsLabel(sel)}`} (${formatNumber(units, 0)} шт) оставлены на ФФ — убраны из предброни`, 'success');
        } catch (e) { showToast(e instanceof Error ? e.message : 'Ошибка', 'error'); }
        finally { setPalletOp(null); }
    }, [draftId, palletOp, stripPalletsFromPrebook, buildDistribution, applyDraft, showToast]);

    // Перенести выбранные моно-паллеты предброни В ЧЕРНОВИК как есть (для направлений с
    // ОТКРЫТЫМ лимитом: моно-недобор, дозабрать нечем — «Оставить так» точечно по паллетам).
    // as_is=true: частичная моно-паллета едет в черновик, normalizeDraft её не режет.
    const handleDraftPallets = useCallback(async (wb: string, ffId: number, sel: { pallet: PrebookMonoPallet; palletNo: number }[]) => {
        if (!draftId || palletOp || ffId < 0 || sel.length === 0) return;
        const chosenKey = String(ffId);
        // Сколько штук per nm просят выбранные паллеты.
        const wantByNm = new Map<number, number>();
        for (const x of sel) for (const it of x.pallet.items) wantByNm.set(it.nm_id, (wantByNm.get(it.nm_id) || 0) + it.units);
        // Порции per (nm × БАРКОД) — зеркало stripPalletsFromPrebook (тот же обход и то же
        // жадное min(portion, want)): в черновик едет ровно то и под теми баркодами, что
        // снимется из предброни. Схлоп «первый баркод per nm» переатрибуцировал бы штуки
        // чужого размерного варианта (коммит бакетит по баркоду → не тот товар на ФФ).
        const remainingDraft = new Map(wantByNm);
        const shipByNmBc = new Map<string, AssemblyDraftRow>();
        for (const r of prebook) {
            if ((r.package_type || 'BOX') !== 'MONOPALLET' || !r.barcode || !(r.tgt[wb] > 0) || !(r.src[chosenKey] > 0)) continue;
            const want = remainingDraft.get(r.nm_id) || 0;
            if (want <= 0) continue;
            const portion = allocatePairs(r.src, r.tgt).get(`${ffId}::${wb}`) || 0;
            const take = Math.min(portion, want);
            if (take <= 0) continue;
            remainingDraft.set(r.nm_id, want - take);
            const k = `${r.nm_id}::${r.barcode}`;
            const e = shipByNmBc.get(k);
            if (e) { e.src[chosenKey] = (e.src[chosenKey] || 0) + take; e.tgt[wb] = (e.tgt[wb] || 0) + take; }
            else shipByNmBc.set(k, { nm_id: r.nm_id, barcode: r.barcode, vendor_code: r.vendor_code, src: { [chosenKey]: take }, tgt: { [wb]: take }, package_type: 'MONOPALLET' as PackageType, as_is: true });
        }
        const shipRows: AssemblyDraftRow[] = [...shipByNmBc.values()];
        if (shipRows.length === 0) { showToast('Не нашёл баркоды артикулов паллет в предброни', 'error'); return; }
        const units = shipRows.reduce((s, r) => s + (r.tgt[wb] || 0), 0);
        const partials = sel.filter(x => x.pallet.fillPct < 0.999).length;
        const what = sel.length === 1 ? `паллету ${palletsLabel(sel)}` : `${formatNumber(sel.length, 0)} паллет (${palletsLabel(sel)})`;
        if (!window.confirm(`Перенести ${what} в черновик как есть (${formatNumber(units, 0)} шт${partials ? `, в т.ч. ${formatNumber(partials, 0)} частичн.` : ''})? Моно поедет частичными паллетами; остальные паллеты направления останутся в предброни.`)) return;
        const key = `${wb}::${ffId}::#${sel.map(x => x.palletNo).join('+')}`;
        setPalletOp(key);
        try {
            for (const r of shipRows) prebookOriginRef.current.add(`${r.nm_id}::${wb}`);
            const nextPrebook = stripPalletsFromPrebook(sel.map(x => x.pallet), wb, ffId);
            const mergedRows = mergeDraftRows([...rows, ...shipRows]);
            const targetNames = Array.from(new Set(mergedRows.flatMap(r => Object.keys(r.tgt))));
            const sourceIds = Array.from(new Set(mergedRows.flatMap(r => Object.keys(r.src).map(Number).filter(n => Number.isFinite(n) && n > 0))));
            const updated = await api.updateAssemblyDraft(draftId, { distribution: { ...buildDistribution(), rows: mergedRows, prebook: nextPrebook, source_warehouse_ids: sourceIds, target_warehouse_names: targetNames }, event: { event_type: 'MATRIX_EDIT', summary: `Паллеты предброни «${wb}» перенесены в черновик как есть` } });
            healScopeRef.current = { ts: updated.updated_at, only: new Set([directionKey('MONOPALLET', wb)]) };
            applyDraft(updated);
            showToast(`${sel.length === 1 ? `Паллета ${palletsLabel(sel)}` : `Паллеты ${palletsLabel(sel)}`} перенесены в черновик (${formatNumber(units, 0)} шт как есть) — бейдж «из предброни» в раскладке`, 'success');
        } catch (e) { showToast(e instanceof Error ? e.message : 'Ошибка переноса в черновик', 'error'); }
        finally { setPalletOp(null); }
    }, [draftId, palletOp, prebook, rows, stripPalletsFromPrebook, buildDistribution, applyDraft, showToast]);

    // ── «Очистить черновик»: сброс наполнения на сервере; для основного черновика
    // бэкенд заодно удаляет категорийные черновики (кроме переданных на ФФ). ──
    const [clearing, setClearing] = useState(false);
    const handleClearDraft = useCallback(async () => {
        if (!draftId || clearing) return;
        const scopedNames = isScoped ? [] : allDrafts
            .filter(d => d.id !== draftId && (d.distribution.category_scope?.length ?? 0) > 0)
            .map(d => d.name);
        if (rows.length === 0 && prebook.length === 0 && scopedNames.length === 0) { showToast('Черновик уже пуст', 'success'); return; }
        const scopedNote = scopedNames.length > 0 ? ` Категорийные черновики (${scopedNames.join(', ')}) тоже будут удалены.` : '';
        if (!window.confirm(`Очистить черновик? Всё наполнение и предбронь будут удалены.${scopedNote}`)) return;
        setClearing(true);
        try {
            const res = await api.clearAssemblyDraft(draftId);
            applyDraft(res.draft);
            refreshReserved(); // список черновиков + резерв: удалённые категорийные исчезают из шапки
            const extra = [
                res.deleted_scoped.length ? `удалены категорийные: ${res.deleted_scoped.join(', ')}` : '',
                res.kept_scoped.length ? `оставлены (переданы на ФФ): ${res.kept_scoped.join(', ')}` : '',
            ].filter(Boolean).join(' · ');
            showToast(extra ? `Черновик очищен · ${extra}` : 'Черновик очищен', 'success');
        } catch (e: unknown) {
            showToast(e instanceof Error ? e.message : 'Ошибка очистки черновика', 'error');
        } finally {
            setClearing(false);
        }
    }, [draftId, clearing, isScoped, allDrafts, rows.length, prebook.length, applyDraft, refreshReserved, showToast]);

    // Удаление неликвида из вкладки «Прогноз»: убрать SKU из черновика на бэке, затем
    // АВТО-дозабивка — пере-собрать остатки округов в целые паллеты (как «Дозабить»).
    // Возвращает {removed, consolidated} для тоста. Бросает — вызывающий ловит.
    const handleRemoveSkus = useCallback(async (nmIds: number[]): Promise<{ removed: number; consolidated: boolean }> => {
        if (!draftId || nmIds.length === 0) return { removed: 0, consolidated: false };
        const after = await api.removeAssemblyDraftRows(draftId, nmIds); // возвращает обновлённый черновик
        let consolidated = false;
        try {
            const norm = await normalizeAndSave(draftId, after); // целые коробы + целые паллеты после удаления
            if (norm) { consolidated = norm.changed; applyDraft(norm.draft); }
            else applyDraft(after); // geom не готова
        } catch { applyDraft(after); }
        return { removed: nmIds.length, consolidated };
    }, [draftId, normalizeAndSave, applyDraft]);

    // ─── Autosave: debounce 5s after any change ──────────────────────────
    useEffect(() => {
        if (!initialLoadRef.current || !draftId) return;
        const timer = setTimeout(() => {
            autosaveTimerRef.current = null;
            const json = JSON.stringify(buildDistribution());
            if (json !== lastSavedJsonRef.current) saveDraft(true).catch(() => {});
        }, AUTOSAVE_DEBOUNCE_MS);
        autosaveTimerRef.current = timer;
        return () => {
            clearTimeout(timer);
            if (autosaveTimerRef.current === timer) autosaveTimerRef.current = null;
        };
    }, [rows, sourceWarehouseIds, targetWarehouseNames, palletsCount, palletWeightKg, estimatedReadyDate, name, comment, draftId, buildDistribution, saveDraft]);

    // Переименование черновика (как на предпросмотре): сохраняем только name.
    const skipSaveNameRef = useRef(false);
    const saveDraftName = useCallback(async () => {
        setEditingName(false);
        if (skipSaveNameRef.current) { skipSaveNameRef.current = false; setName(draft?.name ?? ''); return; }
        if (!draftId || !draft) return;
        const trimmed = name.trim();
        if (trimmed === '' || trimmed === draft.name) { setName(draft.name); return; }
        try {
            const updated = await api.updateAssemblyDraft(draftId, { name: trimmed });
            setDraft(updated);
            setName(updated.name);
            showToast('Название сохранено', 'success');
        } catch (e: unknown) {
            setName(draft.name);
            showToast(e instanceof Error ? e.message : 'Не удалось переименовать', 'error');
        }
    }, [draftId, draft, name, showToast]);

    // ?tab=need у скоупленного черновика — аномалия URL (чип кликнут с вкладки
    // «Потребность» основного / закладка): вкладка скрыта, контент не рендерится —
    // уводим на «Ручную раскладку», иначе экран без активного таба и без контента.
    useEffect(() => {
        if (!loading && isScoped && activeTab === 'need') setTab('matrix');
    }, [loading, isScoped, activeTab, setTab]);

    // nm_id строк черновика — скрыть из таблицы потребности во встроенной вкладке.
    const draftNmIds = useMemo(() => {
        const s = new Set<number>();
        for (const r of rows) s.add(r.nm_id);
        return s;
    }, [rows]);

    const geomReady = geomState === 'ready';
    const backToList = useCallback(() => router.push(`/p/${slug}/warehouse/assembly`), [router, slug]);

    // ─── Render ──────────────────────────────────────────────────────────
    if (loading) {
        return (
            <div className="animate-in">
                <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка черновика…</div>
            </div>
        );
    }
    if (error || !draft) {
        return (
            <div className="animate-in">
                <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>
                    {error || 'Не удалось загрузить черновик'}
                    <div style={{ marginTop: 12 }}>
                        <button className="btn btn-secondary btn-sm" onClick={backToList}>← К заявкам на сборку</button>
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="animate-in">
            {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}

            {/* Header */}
            <div className="page-header" style={{ marginBottom: 16 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, flex: 1, flexWrap: 'wrap' }}>
                    <button className="btn btn-secondary btn-sm" onClick={backToList}>← Назад</button>
                    <h1 className="page-title" style={{ margin: 0 }}>Сборка</h1>
                    {editingName ? (
                        <input
                            className="form-input"
                            autoFocus
                            value={name}
                            onChange={e => setName(e.target.value)}
                            onBlur={saveDraftName}
                            onKeyDown={e => {
                                if (e.key === 'Enter') e.currentTarget.blur();
                                if (e.key === 'Escape') { skipSaveNameRef.current = true; e.currentTarget.blur(); }
                            }}
                            style={{ maxWidth: 320, fontSize: 14 }}
                        />
                    ) : (
                        <button className="btn btn-secondary btn-sm" onClick={() => setEditingName(true)} title="Изменить название черновика">
                            ✏️ {name || 'Без названия'}
                        </button>
                    )}
                    {activeTab === 'draft' && (
                        <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{saving ? 'Сохранение…' : 'Автосохранение включено'}</span>
                    )}
                    {/* Индикатор страничного авто-синка — виден с любой вкладки. */}
                    {(pageSync.running || pageSync.note != null || pageSync.lastAt != null) && <span
                        style={{ fontSize: 12, color: pageSync.note ? 'var(--color-warning)' : 'var(--color-text-muted)' }}
                        title="Авто-синк всех черновиков с живой потребностью и лимитами приёмки: при заходе на страницу и раз в час, пока она открыта. Ручные решения (✋, дозаборы в строки) не трогает; предбронь пересобирается расчётом."
                    >
                        {pageSync.running
                            ? '⟳ авто-синк…'
                            : pageSync.note
                                ? pageSync.note
                                : pageSync.lastAt
                                    ? `⟳ синк ${new Date(pageSync.lastAt).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })} ✓`
                                    : ''}
                    </span>}
                </div>
            </div>

            {/* Переключатель черновиков: основной + категорийные (параллельные). */}
            {(draftChips.length > 1 || isScoped) && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
                    <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>Черновики:</span>
                    {draftChips.map(({ d, qty, scoped }) => {
                        const active = d.id === draftId;
                        return (
                            <button key={d.id} className={`btn btn-sm ${active ? 'btn-primary' : 'btn-secondary'}`}
                                title={scoped ? `Категорийный черновик: ${(d.distribution.category_scope || []).join(', ')}` : 'Основной черновик (без скоупа)'}
                                onClick={() => { if (!active) switchDraft(scoped ? d.id : null); }}>
                                {scoped ? '🗂 ' : '📝 '}{d.name || `Черновик ${d.id}`} · {formatNumber(qty, 0)} шт
                            </button>
                        );
                    })}
                    <button className="btn btn-secondary btn-sm" onClick={openCatModal}
                        title="Создать отдельный черновик-распределение только для выбранных категорий">
                        ➕ По категориям
                    </button>
                </div>
            )}
            {draftChips.length <= 1 && !isScoped && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
                    <button className="btn btn-secondary btn-sm" onClick={openCatModal}
                        title="Создать отдельный черновик-распределение только для выбранных категорий (например, только «Панели стеновые» с одного ФФ)">
                        ➕ Распределение по категориям
                    </button>
                </div>
            )}

            {/* Скоуп-бар категорийного черновика. */}
            {isScoped && (
                <div className="glass-card" style={{ padding: '10px 14px', marginBottom: 10, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', border: '1px solid var(--color-accent)' }}>
                    <span style={{ fontWeight: 700, color: 'var(--color-accent)' }}>🗂 Срез черновика: {(categoryScope || []).join(', ')}</span>
                    <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                        ФФ-источник: {scopeFfId != null ? (warehouses.find(w => w.id === scopeFfId)?.name || `ФФ ${scopeFfId}`) : 'все склады'} · все вкладки работают в этом срезе
                    </span>
                    <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 8 }}>
                        <button className="btn btn-danger btn-sm" onClick={handleDissolveScoped}
                            title="Удалить категорийный черновик; товар освободится на ФФ">✕ Распустить</button>
                    </span>
                </div>
            )}

            {/* Учёт параллельных черновиков в основном. */}
            {!isScoped && reservedUnits > 0 && (
                <div className="glass-card" style={{ padding: '8px 14px', marginBottom: 10, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <span style={{ fontSize: 13, color: 'var(--color-warning)', fontWeight: 600 }}>
                        🔒 В параллельных черновиках занято {formatNumber(reservedUnits, 0)} шт
                    </span>
                    <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>— вычтено из доступного при расчётах этого черновика</span>
                </div>
            )}

            <TabLayout
                tabs={TABS
                    // В категорийном черновике вкладка «Потребность» скрыта: её добавление
                    // идёт мимо скоупа — раскладка среза живёт в «Ручной раскладке».
                    .filter(t => !(isScoped && t.key === 'need'))
                    .map(t => t.key === 'prebook' && prebook.length > 0 ? { ...t, label: `🅿️ Предбронь (${prebook.length})` } : t)}
                active={activeTab}
                onChange={setTab}
            />

            {/* Баннер уровня СТРАНИЦЫ (не вкладки «Черновик»): без геометрии молча
                мертвы и дозабор предброни, и самоочистка — юзер должен видеть причину
                с любой вкладки (прод-кейс: «Дозабить» не реагировал без индикации). */}
            {geomState === 'error' && (
                <div className="glass-card" style={{ padding: 12, marginBottom: 12, borderLeft: '3px solid var(--color-danger)', display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                    <span style={{ color: 'var(--color-danger)', fontWeight: 700 }}>⛔ Кратности коробов не загрузились</span>
                    <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                        заполнение, дозабор предброни и самоочистка черновика заблокированы — без кратностей строки легли бы россыпью
                    </span>
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} onClick={() => loadGeometry()}>↻ Повторить загрузку</button>
                </div>
            )}

            {catModalOpen && (
                <CategoryDraftModal
                    articles={modalArticles}
                    categoryOf={categoryOf}
                    warehouses={warehouses}
                    existingScopes={allDrafts.flatMap(d => d.distribution.category_scope || [])}
                    onCreate={handleCreateScopedDraft}
                    onClose={() => setCatModalOpen(false)}
                />
            )}

            {/* Вкладка «Черновик сборки» — редактор строк + предпросмотр + commit */}
            {activeTab === 'draft' && (
                <>
                    <div className="glass-card" style={{ padding: 12, marginBottom: 12, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                        <button className="btn btn-primary btn-sm" onClick={handleFillFromNeed} disabled={filling}>
                            {filling ? 'Заполнение…' : '⚡ Заполнить черновик из потребности'}
                        </button>
                        {/* Показ и при rows=[]: черновик с одной предбронью или висящими
                            категорийными черновиками тоже должен очищаться (ревью LOW). */}
                        {(rows.length > 0 || prebook.length > 0
                            || allDrafts.some(d => d.id !== draftId && (d.distribution.category_scope?.length ?? 0) > 0)) && (
                            <button className="btn btn-danger btn-sm" onClick={handleClearDraft} disabled={clearing}>
                                {clearing ? 'Очистка…' : '🗑 Очистить черновик'}
                            </button>
                        )}
                        <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                            Соберёт черновик заново строго из «Потребность по складам» (с bump). Текущие строки будут заменены.
                        </span>
                    </div>
                    {prebook.length > 0 && (() => {
                        // Разбивка предброни по ФФ. Склады БЕЗ строк-заявок помечаем «⚠ только
                        // предбронь»: группы предпросмотра строятся из rows, и такой ФФ там
                        // «исчезает», хотя план в черновике есть (кейс «апл» 2026-07-11 —
                        // матрица показывает план единой суммой, а в заявках склада нет).
                        const pbByFf = new Map<number, number>();
                        for (const r of prebook) for (const [ff, q] of Object.entries(r.src || {})) {
                            if ((q || 0) > 0) pbByFf.set(Number(ff), (pbByFf.get(Number(ff)) || 0) + (q || 0));
                        }
                        const rowsFf = new Set<number>();
                        for (const r of rows) for (const [ff, q] of Object.entries(r.src || {})) if ((q || 0) > 0) rowsFf.add(Number(ff));
                        const pbTotal = [...pbByFf.values()].reduce((s, v) => s + v, 0);
                        const ffName = (id: number) => warehouses.find(w => w.id === id)?.name || `ФФ ${id}`;
                        return (
                            <div className="glass-card" style={{ padding: 12, marginBottom: 12, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                                <span style={{ fontWeight: 700 }}>🅿️ Предбронь: {formatNumber(pbTotal, 0)} шт · {formatNumber(prebook.length, 0)} поз.</span>
                                {[...pbByFf.entries()].sort((a, b) => b[1] - a[1]).map(([ff, q]) => {
                                    const noRows = !rowsFf.has(ff);
                                    return (
                                        <span key={ff} className="badge"
                                            title={noRows
                                                ? 'ВЕСЬ план этого ФФ — в предброни: заявок нет, в предпросмотре заявок склад не виден. Дозабор/отправка — на вкладке Предбронь.'
                                                : 'Часть плана этого ФФ в предброни (плюс есть заявки в предпросмотре).'}
                                            style={{ fontSize: 11, padding: '2px 8px', background: noRows ? 'rgba(255,159,10,0.14)' : 'rgba(59,130,246,0.10)', color: noRows ? 'var(--color-warning)' : 'var(--color-text)' }}>
                                            {noRows ? '⚠ ' : ''}{ffName(ff)} · {formatNumber(q, 0)} шт{noRows ? ' — только предбронь, заявок нет' : ''}
                                        </span>
                                    );
                                })}
                                <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>хвосты меньше паллеты — дозабор/отправка на вкладке «Предбронь»</span>
                                <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} onClick={() => setTab('prebook')}>Открыть предбронь →</button>
                            </div>
                        );
                    })()}
                    {noPpbArticles.length > 0 && (
                        <div className="glass-card" style={{ padding: 12, marginBottom: 12 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
                                <span style={{ fontWeight: 700, color: 'var(--color-warning)' }}>⚠️ Без кратности короба — {formatNumber(noPpbArticles.length, 0)} арт.</span>
                                <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                    нет данных «шт/короб» → НЕ участвуют в расчёте (россыпью не едут), пока не указана кратность
                                </span>
                                <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} onClick={() => setTab('box')}>
                                    📦 Указать кратность →
                                </button>
                            </div>
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                                {noPpbArticles.slice(0, 40).map(a => (
                                    <span key={a.nm_id} title={`nm ${a.nm_id} · свободный сток ФФ: ${formatNumber(a.rf, 0)} шт`}
                                        style={{ fontSize: 11, padding: '1px 7px', borderRadius: 5, background: 'rgba(245,158,11,0.12)', color: 'var(--color-warning)' }}>
                                        {a.vendor} · {formatNumber(a.rf, 0)} шт
                                    </span>
                                ))}
                                {noPpbArticles.length > 40 && (
                                    <span style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>…ещё {formatNumber(noPpbArticles.length - 40, 0)}</span>
                                )}
                            </div>
                        </div>
                    )}
                    {partialPpbArticles.length > 0 && (
                        <div className="glass-card" style={{ padding: 12, marginBottom: 12 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
                                <span style={{ fontWeight: 700, color: 'var(--color-warning)' }}>🧩 Частичная кратность — {formatNumber(partialPpbArticles.length, 0)} арт.</span>
                                <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                    кратность есть, но НЕ на складе с остатком → эта порция уедет россыпью; задайте кратность на складе-источнике
                                </span>
                                <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} onClick={() => setTab('box')}>
                                    📦 Указать кратность →
                                </button>
                            </div>
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                                {partialPpbArticles.slice(0, 40).map(a => (
                                    <span key={a.nm_id} title={`nm ${a.nm_id} · остаток на складах без кратности: ${formatNumber(a.rf, 0)} шт`}
                                        style={{ fontSize: 11, padding: '1px 7px', borderRadius: 5, background: 'rgba(245,158,11,0.12)', color: 'var(--color-warning)' }}>
                                        {a.vendor} · {formatNumber(a.rf, 0)} шт
                                    </span>
                                ))}
                                {partialPpbArticles.length > 40 && (
                                    <span style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>…ещё {formatNumber(partialPpbArticles.length - 40, 0)}</span>
                                )}
                            </div>
                        </div>
                    )}
                    {noBoxSizeArticles.length > 0 && (
                        <div className="glass-card" style={{ padding: 12, marginBottom: 12 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
                                <span style={{ fontWeight: 700, color: 'var(--color-warning)' }}>📐 Без размера коробки — {formatNumber(noBoxSizeArticles.length, 0)} арт.</span>
                                <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                    кратность есть, а габаритов нет → короба соберутся, но паллета не сформируется (моно/крупногабарит); задайте размер
                                </span>
                                <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} onClick={() => setTab('box')}>
                                    📐 Указать размер →
                                </button>
                            </div>
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                                {noBoxSizeArticles.slice(0, 40).map(a => (
                                    <span key={a.nm_id} title={`nm ${a.nm_id} · свободный сток ФФ: ${formatNumber(a.rf, 0)} шт`}
                                        style={{ fontSize: 11, padding: '1px 7px', borderRadius: 5, background: 'rgba(245,158,11,0.12)', color: 'var(--color-warning)' }}>
                                        {a.vendor} · {formatNumber(a.rf, 0)} шт
                                    </span>
                                ))}
                                {noBoxSizeArticles.length > 40 && (
                                    <span style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>…ещё {formatNumber(noBoxSizeArticles.length - 40, 0)}</span>
                                )}
                            </div>
                        </div>
                    )}
                    <UrgentShipPanel slug={slug} rows={rows} prebook={prebook} stockNeed={stockNeed}
                        reservedByNm={urgentReservedByNm}
                        ppbOf={urgentPpbOf}
                        inScope={isScoped ? inScope : undefined}
                        scopeLabel={isScoped ? (categoryScope || []).join(', ') : undefined}
                    />
                    <DraftPreview
                        slug={slug}
                        draftId={draft.id}
                        rows={rows}
                        newcomerNmIds={newcomerNmIds}
                        warehouses={warehouses}
                        nmPpb={nmPpb}
                        nmPpbByWh={nmPpbByWh}
                        nmMeta={nmMeta}
                        nmBoxSize={nmBoxSize}
                        palletOverrides={palletOverrides}
                        geomReady={geomReady}
                        prebookOrigin={prebookOriginRef.current}
                        onReturnPalletToPrebook={handleReturnPalletToPrebook}
                        ensureSaved={ensureSaved}
                        onToast={showToast}
                        onReloadDraft={reloadDraft}
                        classOf={classOf}
                        classLabel={classLabel}
                    />
                </>
            )}

            {/* Вкладка «Потребность по складам» — встроенный WarehouseNeedView.
                !isScoped обязателен (не только фильтр кнопки таба): ?tab=need переживает
                клик по чипу категорийного черновика (switchDraft хранит query), и смонти-
                рованная «Потребность» лила бы SKU ЧУЖИХ категорий в скоупленный черновик
                (add_rows_to_draft скоуп не валидирует, авто-синк чужие nm не вычищает). */}
            {activeTab === 'need' && !isScoped && (
                <WarehouseNeedView
                    embeddedDraftId={draft.id}
                    hiddenNmIds={draftNmIds}
                    onRowsAddedToDraft={handleRowsAdded}
                    autoCheckAcceptance
                    fillAllSignal={fillSignal}
                    onFillAllRows={handleFillAllRows}
                    subtractReserve={draftsReserved}
                />
            )}

            {/* Вкладка «Кратность» */}
            {activeTab === 'box' && <BoxMultiplicityView onSaved={() => loadGeometry()} />}

            {/* Вкладка «Паллеты» */}
            {activeTab === 'pallets' && (
                <>
                    <div style={{ maxWidth: 1100, margin: '16px auto 0' }}>
                        <BoxWeightSetting />
                    </div>
                    <PalletSizesView />
                </>
            )}

            {/* Вкладка «Ручная раскладка» — матрица-редактор черновика, источник = весь ФФ-сток */}
            {activeTab === 'matrix' && (
                draftId ? (
                    <DraftMatrixView
                        draftId={draftId}
                        ffNameById={new Map(warehouses.map(w => [w.id, w.name]))}
                        reserved={draftsReserved}
                        scopeCategories={categoryScope}
                        // null = список черновиков ещё не загружен: авто-синк
                        // матрицы ждёт (иначе разово пустые чужие скоупы).
                        foreignScopeCategories={draftsLoaded ? foreignScopeCategories : null}
                        scopeFfId={scopeFfId}
                        categoryOf={categoryOf}
                        classOf={classOf}
                        onDraftChanged={(d, opts) => {
                            // Тихая синхронизация из редактора-матрицы (автосейв степпера / ✕):
                            // ручная правка = ТОЧНЫЙ план юзера — пустой heal-scope на эту
                            // версию, иначе полный self-heal переупаковал бы её (rows→prebook,
                            // некратные released) вторым PUT. Вкладку НЕ переключаем.
                            // fromSync (авто-синк матрицы) не гасит консолидацию по приёмке.
                            healScopeRef.current = { ts: d.updated_at, only: new Set(), fromSync: opts?.fromSync };
                            applyDraft(d);
                        }}
                        readyPkgWbs={readyPkgWbs}
                    />
                ) : (
                    <div className="glass-card" style={{ padding: 48, textAlign: 'center', color: 'var(--color-muted)' }}>
                        Сначала выберите или создайте черновик (вкладка «📝 Черновик сборки») — ручная раскладка пишет результат в него.
                    </div>
                )
            )}

            {/* Вкладка «История» — журнал изменений черновика + откат событий */}
            {activeTab === 'history' && (
                draftId ? (
                    <DraftHistoryView draftId={draftId} onReverted={() => { reloadDraft(); }} />
                ) : (
                    <div className="glass-card" style={{ padding: 48, textAlign: 'center', color: 'var(--color-muted)' }}>
                        Сначала выберите или создайте черновик (вкладка «📝 Черновик сборки») — история ведётся по нему.
                    </div>
                )
            )}

            {/* Вкладка «Предраспределение машины в пути» — раздача груза по WB-складам до приёмки */}
            {activeTab === 'pre-dist' && <PreDistributionView />}

            {/* Вкладка «Предбронь» — коробы, не собравшие паллету, + минимальный дозабор */}
            {activeTab === 'prebook' && (
                <PrebookView
                    groups={prebookGroups}
                    toppingUpKey={toppingUp}
                    shipAsIsKey={shippingAsIs}
                    deletingKey={deletingDir}
                    prebookingKey={creatingPrebooking}
                    tailTopUpKey={toppingUpPrebook}
                    trimTailKey={trimmingTail}
                    acceptanceMarks={prebookAcceptanceMarks}
                    acceptanceLoading={prebookAccLoading}
                    preorderWbs={preorderWbs}
                    onTopUp={handleTopUpDirection}
                    onShipAsIs={handleShipPrebookAsIs}
                    onDelete={handleDeletePrebookItem}
                    onDeleteDirection={handleDeleteDirection}
                    onCreatePrebooking={handleCreatePrebooking}
                    onTopUpPrebook={handleTopUpPrebookMono}
                    onTrimTail={handleTrimPrebookTail}
                    onBookPallets={handleBookPallets}
                    onReleasePallets={handleReleasePallets}
                    onDraftPallets={handleDraftPallets}
                    palletOpKey={palletOp}
                />
            )}

            {/* Вкладка «Прогноз / Локализация» — загрузка WB-складов с учётом черновика */}
            {activeTab === 'forecast' && (
                <ForecastView draftId={draft.id} onRemoveSkus={handleRemoveSkus} onToast={showToast} />
            )}

            {/* Вкладка «Настройки складов» — исключение/закрытие складов + время РФ→WB */}
            {activeTab === 'settings' && <WarehouseExclusionSettings />}

            {/* «Заполнить черновик из потребности»: скрытый инстанс WarehouseNeedView считает
                раскладку ВСЕЙ потребности (data + свободные лимиты приёмки) и через onFillAllRows
                ЗАМЕНЯЕТ черновик. Блокирующий оверлей — чтобы не ушли во время расчёта. */}
            {filling && (
                <>
                    <div style={{
                        position: 'fixed', inset: 0, zIndex: 200,
                        background: 'rgba(0,0,0,0.18)', backdropFilter: 'blur(6px)',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                    }}>
                        <div className="glass-card" style={{ padding: 28, textAlign: 'center', maxWidth: 360 }}>
                            <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 12 }}>⚡ Собираю черновик из всей потребности…</div>
                            <div className="skeleton" style={{ width: '80%', height: 12, margin: '0 auto 8px' }} />
                            <div className="skeleton" style={{ width: '60%', height: 12, margin: '0 auto 14px' }} />
                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>Проверяю свободные лимиты приёмки WB — несколько секунд, не закрывайте страницу</div>
                        </div>
                    </div>
                    <div style={{ display: 'none' }} aria-hidden>
                        <WarehouseNeedView
                            embeddedDraftId={draft.id}
                            autoCheckAcceptance
                            fillAllSignal={fillSignal}
                            onFillAllRows={handleFillAllRows}
                            subtractReserve={draftsReserved}
                        />
                    </div>
                </>
            )}
        </div>
    );
}
