import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import PageHeader from '@/components/PageHeader';

describe('PageHeader', () => {
  it('renders title', () => {
    render(<PageHeader title="Dashboard" />);

    expect(screen.getByText('Dashboard')).toBeInTheDocument();
  });

  it('renders title in an h1 element', () => {
    render(<PageHeader title="Dashboard" />);

    const heading = screen.getByRole('heading', { level: 1 });
    expect(heading).toHaveTextContent('Dashboard');
  });

  it('renders subtitle when provided', () => {
    render(<PageHeader title="Dashboard" subtitle="Overview of metrics" />);

    expect(screen.getByText('Overview of metrics')).toBeInTheDocument();
  });

  it('does not render subtitle when not provided', () => {
    const { container } = render(<PageHeader title="Dashboard" />);

    expect(container.querySelector('.page-subtitle')).toBeNull();
  });

  it('renders icon before title', () => {
    render(<PageHeader title="Settings" icon="gear-icon" />);

    const heading = screen.getByRole('heading', { level: 1 });
    expect(heading).toHaveTextContent('gear-icon Settings');
  });

  it('renders action buttons', () => {
    render(
      <PageHeader
        title="Items"
        actions={<button>Add Item</button>}
      />
    );

    expect(screen.getByText('Add Item')).toBeInTheDocument();
  });

  it('does not render actions container when no actions provided', () => {
    const { container } = render(<PageHeader title="Dashboard" />);

    // The actions div should not exist
    const pageHeader = container.querySelector('.page-header');
    expect(pageHeader).toBeInTheDocument();
    // Only one child div (the title section), no actions div
    const children = pageHeader?.children;
    expect(children?.length).toBe(1);
  });

  it('renders multiple action buttons', () => {
    render(
      <PageHeader
        title="Items"
        actions={
          <>
            <button>Export</button>
            <button>Add</button>
          </>
        }
      />
    );

    expect(screen.getByText('Export')).toBeInTheDocument();
    expect(screen.getByText('Add')).toBeInTheDocument();
  });

  it('renders all props together', () => {
    render(
      <PageHeader
        title="Reports"
        subtitle="Financial summaries"
        icon="chart-icon"
        actions={<button>Download</button>}
      />
    );

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('chart-icon Reports');
    expect(screen.getByText('Financial summaries')).toBeInTheDocument();
    expect(screen.getByText('Download')).toBeInTheDocument();
  });
});
