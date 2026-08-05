<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

## Lessons from real sessions (append here, don't touch the block above)

- **`eslint-config-next` here enforces `react-hooks/set-state-in-effect`** — a rule that
  doesn't exist in most training data. `useEffect(() => setState(someProp), [someProp])`
  (copying a prop into local state just to react to its changes) fails lint with "Calling
  setState synchronously within an effect can trigger cascading renders." Found while
  building Ticket 3's decision-overlay state in `report-view.tsx`. Fix: don't copy the prop
  into state at all — keep only the actual local delta (e.g. an `id -> override` map) in
  `useState`, and merge it with the prop via `useMemo` at render time instead of syncing a
  full copy via an effect.
- **No test framework is configured** (`package.json` has no jest/vitest/testing-library as
  of Ticket 3). Verification for frontend changes today is `npx tsc --noEmit`,
  `npm run lint`, `npm run build`, plus manual testing in a real browser against a real
  running API — there is no way to run an automated frontend test suite yet. Don't assume
  one exists; don't add one without asking first (new dependency).
- **No FastAPI `TestClient`/mock-auth harness exists on the backend either** (see
  `notes/tickets/ticket1.md`/`ticket2.md`) — so a frontend session can't lean on backend
  route tests to validate contract shape either. Cross-check new frontend API calls against
  the actual router source (`apps/api/routers/*.py`) and/or a real `curl`/manual request,
  not just the TypeScript types in `lib/api.ts` (which are hand-written mirrors, not
  generated from the backend, and can drift).
