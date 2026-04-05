import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import DataTable from '@/components/DataTable';
import type { Column } from '@/components/DataTable';

const columns: Column[] = [
  { key: 'name', label: 'Name' },
  { key: 'amount', label: 'Amount', align: 'right', format: 'number' },
  { key: 'date', label: 'Date', format: 'date' },
];

const sampleData = [
  { id: 1, name: 'Item A', amount: 1500.5, date: '2025-01-15' },
  { id: 2, name: 'Item B', amount: 2300, date: '2025-02-20' },
  { id: 3, name: 'Item C', amount: 800.75, date: '2025-03-10' },
];

describe('DataTable', () => {
  it('renders column headers', () => {
    render(<DataTable columns={columns} data={sampleData} />);

    expect(screen.getByText('Name')).toBeInTheDocument();
    expect(screen.getByText('Amount')).toBeInTheDocument();
    expect(screen.getByText('Date')).toBeInTheDocument();
  });

  it('renders data rows', () => {
    render(<DataTable columns={columns} data={sampleData} />);

    expect(screen.getByText('Item A')).toBeInTheDocument();
    expect(screen.getByText('Item B')).toBeInTheDocument();
    expect(screen.getByText('Item C')).toBeInTheDocument();
  });

  it('shows loading state', () => {
    render(<DataTable columns={columns} data={[]} loading={true} />);

    expect(screen.getByText(/Загрузка/)).toBeInTheDocument();
  });

  it('shows empty state when no data', () => {
    render(<DataTable columns={columns} data={[]} />);

    expect(screen.getByText('Нет данных')).toBeInTheDocument();
  });

  it('shows custom empty text', () => {
    render(<DataTable columns={columns} data={[]} emptyText="Пусто" />);

    expect(screen.getByText('Пусто')).toBeInTheDocument();
  });

  it('renders title with row count', () => {
    render(<DataTable columns={columns} data={sampleData} title="Items" />);

    expect(screen.getByText('Items (3)')).toBeInTheDocument();
  });

  it('renders title without count when data is empty', () => {
    render(<DataTable columns={columns} data={[]} title="Items" loading={false} />);

    expect(screen.getByText('Items')).toBeInTheDocument();
  });

  it('shows Excel export button when exportName is set and data exists', () => {
    render(<DataTable columns={columns} data={sampleData} exportName="test" />);

    expect(screen.getByText(/Excel/)).toBeInTheDocument();
  });

  it('hides Excel export button when data is empty', () => {
    render(<DataTable columns={columns} data={[]} exportName="test" />);

    expect(screen.queryByText(/Excel/)).not.toBeInTheDocument();
  });

  it('calls onRowClick when a row is clicked', async () => {
    const user = userEvent.setup();
    let clickedRow: any = null;
    let clickedIndex: number = -1;

    render(
      <DataTable
        columns={columns}
        data={sampleData}
        onRowClick={(row, index) => {
          clickedRow = row;
          clickedIndex = index;
        }}
      />
    );

    await user.click(screen.getByText('Item B'));

    expect(clickedRow).toEqual(sampleData[1]);
    expect(clickedIndex).toBe(1);
  });

  it('highlights selected row', () => {
    const { container } = render(
      <DataTable columns={columns} data={sampleData} selectedIndex={1} />
    );

    const rows = container.querySelectorAll('tbody tr');
    expect(rows[1]).toHaveStyle({ background: 'rgba(139,92,246,0.1)' });
  });

  it('truncates data beyond maxRows and shows message', () => {
    render(<DataTable columns={columns} data={sampleData} maxRows={2} />);

    expect(screen.getByText('Item A')).toBeInTheDocument();
    expect(screen.getByText('Item B')).toBeInTheDocument();
    expect(screen.queryByText('Item C')).not.toBeInTheDocument();
    expect(screen.getByText(/Показано 2 из 3/)).toBeInTheDocument();
  });

  it('renders actions in toolbar', () => {
    render(
      <DataTable
        columns={columns}
        data={sampleData}
        actions={<button>Custom Action</button>}
      />
    );

    expect(screen.getByText('Custom Action')).toBeInTheDocument();
  });

  it('auto-generates columns from data keys', () => {
    const data = [{ foo: 'bar', baz: 42 }];

    render(<DataTable columns={[]} data={data} autoColumns={true} />);

    expect(screen.getByText('foo')).toBeInTheDocument();
    expect(screen.getByText('baz')).toBeInTheDocument();
    expect(screen.getByText('bar')).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
  });

  it('formats money cells with ruble sign', () => {
    const moneyColumns: Column[] = [
      { key: 'price', label: 'Price', format: 'money' },
    ];
    const data = [{ id: 1, price: 1000 }];

    render(<DataTable columns={moneyColumns} data={data} />);

    // Should contain the ruble sign
    const cell = screen.getByText(/\u20BD/);
    expect(cell).toBeInTheDocument();
  });

  it('shows em-dash for null values', () => {
    const data = [{ id: 1, name: null, amount: null, date: null }];

    render(<DataTable columns={columns} data={data} />);

    // \u2014 is em-dash, DataTable renders it for null values
    const dashes = screen.getAllByText('\u2014');
    expect(dashes.length).toBeGreaterThanOrEqual(1);
  });
});
