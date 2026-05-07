import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import LocalizationInsights, {
    potentialSave,
    suggestDistrict,
    distributionFor,
    rollupByRegion,
} from '@/components/LocalizationInsights';
import type { LocalizationSkuRow, DistrictBreakdown } from '@/types/api';

function district(
    key: string,
    local: number,
    nonLocal: number,
): DistrictBreakdown {
    const total = local + nonLocal;
    return {
        district: key,
        label: key,
        local,
        non_local: nonLocal,
        total,
        local_pct: total > 0 ? (local / total) * 100 : 0,
    };
}

function makeRow(over: Partial<LocalizationSkuRow> = {}): LocalizationSkuRow {
    return {
        nm_id: 1,
        vendor_code: 'A1',
        title: 'A1',
        subject: 'sub',
        brand: 'BrandA',
        total: 100,
        local: 60,
        non_local: 40,
        loc_pct: 60,
        ktr: 1.0,
        krp: 0,
        contribution: 100,
        status: 'neutral',
        districts: [],
        ...over,
    };
}

describe('potentialSave', () => {
    it('возвращает 0 если КТР уже идеальный', () => {
        expect(potentialSave({ total: 100, ktr: 0.5 })).toBe(0);
    });

    it('считает orders × (ktr - 0.5) для нормального SKU', () => {
        // 100 заказов × (1.40 - 0.50) = 90
        expect(potentialSave({ total: 100, ktr: 1.4 })).toBeCloseTo(90, 5);
    });

    it('не уходит в отрицательные (clamp к 0)', () => {
        expect(potentialSave({ total: 50, ktr: 0.4 })).toBe(0);
    });
});

describe('suggestDistrict / distributionFor', () => {
    it('distributionFor возвращает пустой массив если districts пусто', () => {
        expect(distributionFor(makeRow({ districts: [] }))).toEqual([]);
    });

    it('distributionFor исключает abroad/unknown и сортирует по non_local desc', () => {
        const row = makeRow({
            districts: [
                district('abroad', 0, 100),
                district('unknown', 0, 50),
                district('central', 1, 5),
                district('volga', 5, 20),
                district('ural', 50, 10),
            ],
        });
        const dist = distributionFor(row);
        expect(dist.map(d => d.key)).toEqual(['volga', 'ural', 'central']);
        expect(dist[0].nonLocal).toBe(20);
    });

    it('distributionFor режет до топ-3', () => {
        const row = makeRow({
            districts: [
                district('central', 0, 10),
                district('volga', 0, 20),
                district('ural', 0, 30),
                district('northwest', 0, 40),
                district('south_caucasus', 0, 50),
            ],
        });
        const dist = distributionFor(row);
        expect(dist).toHaveLength(3);
        expect(dist.map(d => d.nonLocal)).toEqual([50, 40, 30]);
    });

    it('suggestDistrict возвращает "—" если distribution пуст', () => {
        expect(suggestDistrict(makeRow({ districts: [] }))).toBe('—');
        expect(
            suggestDistrict(
                makeRow({
                    districts: [district('central', 100, 0)],
                }),
            ),
        ).toBe('—');
    });

    it('suggestDistrict берёт ФО с наибольшим non_local', () => {
        const row = makeRow({
            districts: [
                district('central', 10, 3),
                district('volga', 5, 20),
                district('ural', 50, 5),
            ],
        });
        expect(suggestDistrict(row)).toContain('20 нелок');
    });
});

describe('rollupByRegion', () => {
    type Row = Parameters<typeof rollupByRegion>[0][number];
    function sel(over: Partial<Row> = {}): Row {
        return {
            nm_id: 1,
            vendor_code: 'A',
            title: 'A',
            brand: '—',
            subject: '—',
            total: 100,
            non_local: 50,
            loc_pct: 0,
            ktr: 2,
            krp: 2.5,
            contribution: 200,
            potentialSave: 150,
            distribution: [],
            ...over,
        };
    }

    it('пустой → пустой массив', () => {
        expect(rollupByRegion([])).toEqual([]);
    });

    it('агрегирует non_local по ФО + считает SKU + share', () => {
        const out = rollupByRegion([
            sel({
                nm_id: 1,
                distribution: [
                    { key: 'central', label: 'Центральный', nonLocal: 30 },
                    { key: 'volga', label: 'Приволжский', nonLocal: 10 },
                ],
            }),
            sel({
                nm_id: 2,
                distribution: [
                    { key: 'central', label: 'Центральный', nonLocal: 20 },
                ],
            }),
        ]);
        // Σ central = 50 (2 SKU), volga = 10 (1 SKU). Total = 60.
        const central = out.find(r => r.key === 'central')!;
        const volga = out.find(r => r.key === 'volga')!;
        expect(central.nonLocal).toBe(50);
        expect(central.skus).toBe(2);
        expect(central.sharePct).toBeCloseTo((50 / 60) * 100, 5);
        expect(volga.nonLocal).toBe(10);
        expect(volga.skus).toBe(1);
        // Sorted by nonLocal desc → central первый.
        expect(out[0].key).toBe('central');
    });
});

describe('<LocalizationInsights />', () => {
    const rows: LocalizationSkuRow[] = [
        makeRow({
            nm_id: 1,
            vendor_code: 'AAA',
            brand: 'B1',
            subject: 'Куртки',
            total: 100,
            ktr: 2.0,
            krp: 2.5,
            loc_pct: 0,
            contribution: 200,
            districts: [district('central', 10, 90)],
        }),
        makeRow({
            nm_id: 2,
            vendor_code: 'BBB',
            brand: 'B2',
            subject: 'Платья',
            total: 50,
            ktr: 0.5,
            krp: 0,
            loc_pct: 100,
            contribution: 25,
            districts: [district('volga', 50, 0)],
        }),
        makeRow({
            nm_id: 3,
            vendor_code: 'CCC',
            brand: 'B1',
            subject: 'Куртки',
            total: 80,
            ktr: 1.4,
            krp: 2.1,
            loc_pct: 37,
            contribution: 112,
            districts: [district('ural', 30, 50)],
        }),
    ];

    it('по умолчанию открыта вкладка Pareto-симулятор и видна таблица выбранных', () => {
        render(<LocalizationInsights rows={rows} />);
        const table = screen.getByTestId('insights-selected');
        expect(table).toBeInTheDocument();
        // Идеальный SKU (BBB) НЕ должен быть в выбранных.
        expect(within(table).queryByText('BBB')).toBeNull();
    });

    it('Pareto-симулятор: пресет 50% берёт 1 кандидата (из 2 не-идеальных)', () => {
        render(<LocalizationInsights rows={rows} />);
        fireEvent.click(screen.getByRole('button', { name: '50%' }));
        const table = screen.getByTestId('insights-selected');
        const bodyRows = within(table).getAllByRole('row').slice(1);
        // Math.round(2 * 0.5) = 1
        expect(bodyRows.length).toBe(1);
        // Top-1 по potential_save = AAA (200)
        expect(bodyRows[0].textContent).toContain('AAA');
    });

    it('Pareto-симулятор: «Все» (100%) показывает всех кандидатов кроме идеальных', () => {
        render(<LocalizationInsights rows={rows} />);
        fireEvent.click(screen.getByRole('button', { name: 'Все' }));
        const table = screen.getByTestId('insights-selected');
        const bodyRows = within(table).getAllByRole('row').slice(1);
        expect(bodyRows.length).toBe(2);
        // BBB всё равно не должен быть.
        expect(within(table).queryByText('BBB')).toBeNull();
    });

    it('Pareto-симулятор показывает chip-распределение «куда довезти»', () => {
        render(<LocalizationInsights rows={rows} />);
        // Берём всех кандидатов чтобы видеть и AAA и CCC.
        fireEvent.click(screen.getByRole('button', { name: 'Все' }));
        const table = screen.getByTestId('insights-selected');
        // Для AAA distribution = central:90; для CCC = ural:50.
        // Компонент берёт label из DISTRICT_LABELS — рендерит ru-варианты.
        expect(within(table).getByText(/Центральный.*90/i)).toBeInTheDocument();
        expect(within(table).getByText(/Уральский.*50/i)).toBeInTheDocument();
    });

    it('переключение на «Сводка» агрегирует по brand по умолчанию', () => {
        render(<LocalizationInsights rows={rows} />);
        fireEvent.click(screen.getByRole('tab', { name: /Pareto-симулятор/i })); // simulator (уже активен)
        fireEvent.click(screen.getByRole('tab', { name: /^Сводка$/i }));
        const table = screen.getByTestId('insights-groups');
        expect(table.getAttribute('data-groupby')).toBe('brand');
        expect(within(table).getByText('B1')).toBeInTheDocument();
        expect(within(table).getByText('B2')).toBeInTheDocument();
    });

    it('Сводка: toggle переключает группировку на категории', () => {
        render(<LocalizationInsights rows={rows} />);
        fireEvent.click(screen.getByRole('tab', { name: /^Сводка$/i }));
        fireEvent.click(screen.getByRole('tab', { name: /По категориям/i }));
        const table = screen.getByTestId('insights-groups');
        expect(table.getAttribute('data-groupby')).toBe('subject');
        // 2 категории: «Куртки» (2 SKU) и «Платья» (1)
        expect(within(table).getByText('Куртки')).toBeInTheDocument();
        expect(within(table).getByText('Платья')).toBeInTheDocument();
    });

    it('сводка по регионам показывает Σ нелок и число артикулов на ФО', () => {
        render(<LocalizationInsights rows={rows} />);
        fireEvent.click(screen.getByRole('button', { name: 'Все' }));
        const table = screen.getByTestId('insights-region-rollup');
        // AAA → central:90, CCC → ural:50. ИТОГО строка = 140.
        expect(within(table).getByText(/Центральный/i)).toBeInTheDocument();
        expect(within(table).getByText(/Уральский/i)).toBeInTheDocument();
        const itogoCells = within(table).getByText('ИТОГО').closest('tr')!;
        expect(itogoCells.textContent).toContain('140');
    });

    it('клик по строке ФО раскрывает список артикулов в этот регион', () => {
        render(<LocalizationInsights rows={rows} />);
        fireEvent.click(screen.getByRole('button', { name: 'Все' }));
        // central — там 90 нелок у AAA
        const centralRow = screen.getByTestId('region-row-central');
        expect(centralRow.getAttribute('aria-expanded')).toBe('false');
        expect(screen.queryByTestId('region-detail-central')).toBeNull();
        fireEvent.click(centralRow);
        expect(centralRow.getAttribute('aria-expanded')).toBe('true');
        const detail = screen.getByTestId('region-detail-central');
        expect(within(detail).getByText('AAA')).toBeInTheDocument();
        // CCC относится к ural, не должен быть в central detail.
        expect(within(detail).queryByText('CCC')).toBeNull();
        // Повторный клик — закрывает
        fireEvent.click(centralRow);
        expect(screen.queryByTestId('region-detail-central')).toBeNull();
    });

    it('раскрытие одного ФО закрывает другой (single-open)', () => {
        render(<LocalizationInsights rows={rows} />);
        fireEvent.click(screen.getByRole('button', { name: 'Все' }));
        fireEvent.click(screen.getByTestId('region-row-central'));
        expect(screen.getByTestId('region-detail-central')).toBeInTheDocument();
        fireEvent.click(screen.getByTestId('region-row-ural'));
        expect(screen.queryByTestId('region-detail-central')).toBeNull();
        expect(screen.getByTestId('region-detail-ural')).toBeInTheDocument();
    });

    it('пустой rows → empty-state', () => {
        render(<LocalizationInsights rows={[]} />);
        expect(screen.getByText(/Нет артикулов в выборке/i)).toBeInTheDocument();
    });

    it('сортировка таблицы выбранных переключает порядок', () => {
        render(<LocalizationInsights rows={rows} />);
        fireEvent.click(screen.getByRole('button', { name: 'Все' })); // взять обоих кандидатов
        const table = screen.getByTestId('insights-selected');
        // Дефолт: sort by potentialSave desc → AAA первый (200) > CCC (72)
        let bodyRows = within(table).getAllByRole('row').slice(1);
        expect(bodyRows[0].textContent).toContain('AAA');
        // Кликаем "Заказов" → DESC: AAA (100) > CCC (80)
        fireEvent.click(within(table).getByText(/Заказов/));
        bodyRows = within(table).getAllByRole('row').slice(1);
        expect(bodyRows[0].textContent).toContain('AAA');
        // Второй клик — ASC: CCC (80) первый
        fireEvent.click(within(table).getByText(/Заказов/));
        bodyRows = within(table).getAllByRole('row').slice(1);
        expect(bodyRows[0].textContent).toContain('CCC');
    });
});
