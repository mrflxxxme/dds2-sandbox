import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ChangelogBell from '@/components/ChangelogBell';
import { CHANGELOG } from '@/lib/changelog';

const LS_KEY = 'dds_changelog_last_seen';

describe('ChangelogBell', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('первый визит — непрочитаны все новости', async () => {
    render(<ChangelogBell slug="test" />);

    expect(await screen.findByText(String(CHANGELOG.length))).toBeInTheDocument();
  });

  it('открытие панели гасит счётчик и запоминает последнюю новость', async () => {
    const user = userEvent.setup();
    render(<ChangelogBell slug="test" />);

    await user.click(await screen.findByLabelText('Обновления проекта'));

    expect(screen.getByRole('dialog', { name: 'Обновления проекта' })).toBeInTheDocument();
    expect(localStorage.getItem(LS_KEY)).toBe(CHANGELOG[0].id);
    expect(screen.queryByText(String(CHANGELOG.length))).not.toBeInTheDocument();
  });

  it('счётчик = число новостей поверх последней прочитанной', async () => {
    localStorage.setItem(LS_KEY, CHANGELOG[2].id);
    render(<ChangelogBell slug="test" />);

    expect(await screen.findByText('2')).toBeInTheDocument();
  });

  it('неизвестный id в localStorage — считаем всё непрочитанным', async () => {
    localStorage.setItem(LS_KEY, 'выпилена-в-прошлом-релизе');
    render(<ChangelogBell slug="test" />);

    expect(await screen.findByText(String(CHANGELOG.length))).toBeInTheDocument();
  });

  it('клик по новости раскрывает суть, влияние и ссылку на раздел', async () => {
    const user = userEvent.setup();
    const entry = CHANGELOG[0];
    render(<ChangelogBell slug="test" />);

    await user.click(await screen.findByLabelText('Обновления проекта'));
    expect(screen.queryByText(entry.summary)).not.toBeInTheDocument();

    await user.click(screen.getByText(entry.title));

    expect(screen.getByText(entry.summary)).toBeInTheDocument();
    expect(screen.getByText(entry.impact, { exact: false })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: `Открыть «${entry.section}»` }))
      .toHaveAttribute('href', `/p/test${entry.href}`);
  });

  it('Escape закрывает панель', async () => {
    const user = userEvent.setup();
    render(<ChangelogBell slug="test" />);

    await user.click(await screen.findByLabelText('Обновления проекта'));
    await user.keyboard('{Escape}');

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('id новостей уникальны — иначе «прочитано» ломается', () => {
    const ids = CHANGELOG.map(e => e.id);

    expect(new Set(ids).size).toBe(ids.length);
  });

  it('новости отсортированы от новых к старым', () => {
    const dates = CHANGELOG.map(e => e.date);

    expect([...dates].sort().reverse()).toEqual(dates);
  });
});
