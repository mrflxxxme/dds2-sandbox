# Frontend Context

## Before ANY frontend change
Read `DOMAIN_FRONTEND.md` in this directory.

## Quick Reference
- Types: ALWAYS in `src/types/api.ts` (NEVER inline)
- API calls: ALWAYS via `src/lib/api.ts` methods (NEVER raw fetch)
- Numbers: `formatNumber()`, Dates: `formatDate()`
- Tables: ALWAYS add Excel export button
- States: MUST have loading, error, empty states
- Styles: CSS classes from `src/app/globals.css` (NEVER inline)
- Callbacks: `useCallback` for data loading functions

## New endpoint checklist
1. Add TypeScript type in `src/types/api.ts`
2. Add API method in `src/lib/api.ts`
3. Use in component
