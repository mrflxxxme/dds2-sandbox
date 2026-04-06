import { describe, it, expect } from 'vitest';
import { formatNumber, formatDate, formatDateTime } from '@/lib/utils';

describe('formatNumber', () => {
  it('formats positive numbers with 2 decimals by default', () => {
    const result = formatNumber(1234.5);
    // ru-RU uses non-breaking space as thousands separator and comma for decimals
    expect(result).toContain('1');
    expect(result).toContain('234');
    expect(result).toContain('50');
  });

  it('formats with custom decimal places', () => {
    const result = formatNumber(1234.5678, 0);
    expect(result).toContain('1');
    expect(result).toContain('235'); // rounded
  });

  it('returns em-dash for null', () => {
    expect(formatNumber(null)).toBe('\u2014');
  });

  it('returns em-dash for undefined', () => {
    expect(formatNumber(undefined)).toBe('\u2014');
  });

  it('formats zero correctly', () => {
    const result = formatNumber(0);
    expect(result).toContain('0');
    expect(result).toContain('00');
  });

  it('formats negative numbers', () => {
    const result = formatNumber(-500.99);
    expect(result).toContain('500');
    expect(result).toContain('99');
  });
});

describe('formatDate', () => {
  it('formats ISO date string to ru-RU locale', () => {
    const result = formatDate('2025-03-15');
    // ru-RU date format: DD.MM.YYYY
    expect(result).toContain('15');
    expect(result).toContain('03');
    expect(result).toContain('2025');
  });

  it('returns em-dash for null', () => {
    expect(formatDate(null)).toBe('\u2014');
  });

  it('returns em-dash for undefined', () => {
    expect(formatDate(undefined)).toBe('\u2014');
  });

  it('returns em-dash for empty string', () => {
    expect(formatDate('')).toBe('\u2014');
  });
});

describe('formatDateTime', () => {
  it('formats ISO datetime string', () => {
    const result = formatDateTime('2025-03-15T14:30:00');
    expect(result).toContain('15');
    expect(result).toContain('2025');
  });

  it('returns em-dash for null', () => {
    expect(formatDateTime(null)).toBe('\u2014');
  });

  it('returns em-dash for undefined', () => {
    expect(formatDateTime(undefined)).toBe('\u2014');
  });

  it('returns em-dash for empty string', () => {
    expect(formatDateTime('')).toBe('\u2014');
  });
});
