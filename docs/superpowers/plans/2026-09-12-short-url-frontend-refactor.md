# Short URL Admin Frontend Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current card-heavy, fragmented console with a compact domain-scoped operations interface backed by the new aggregate APIs.

**Architecture:** Keep the existing React application, route each business area to one page, and split only reusable page-shell and feature-drawer components. TanStack Query owns server state, Zustand owns authentication and selected-domain state, React Hook Form plus Zod validates writes, and Ant Design provides all ordinary UI controls.

**Tech Stack:** React 19, TypeScript 6, Vite 8, React Router 7, Ant Design 6, TanStack Query 5, Zustand 5, React Hook Form 7, Zod 4, Axios, Day.js, Recharts 3, Oxlint.

**Spec:** `docs/superpowers/specs/2026-09-12-short-url-admin-refactor-design.md`

## Global Constraints

- Implement against the completed backend contract from `2026-09-12-short-url-backend-refactor.md`; do not preserve obsolete frontend API calls.
- Primary navigation is exactly Overview, Short Links, Access Logs, User Management for admins, and Security for admins.
- Domain administration belongs in the Short Links page drawer, not the primary navigation.
- The document body and page shell never scroll; only table bodies and intentional drawer content scroll.
- Use Ant Design components and icons instead of custom tables, dialogs, selectors, pagination, or SVG icons.
- Use one chart library: Recharts. Remove `@ant-design/charts`.
- Use React Hook Form and Zod for short-link and administrative write forms.
- Keep filters and form values intact after request errors and display the backend error message.
- Do not add a generic design system, schema-generated client, state machine, or global event bus.
- Do not modify or deploy the production server.

---

### Task 1: Replace the frontend API contract and domain state

**Files:**
- Create: `../short_url_frontend/src/types/api.ts`
- Modify: `../short_url_frontend/src/types/index.ts`
- Modify: `../short_url_frontend/src/utils/axios.ts`
- Modify: `../short_url_frontend/src/api/auth.ts`
- Modify: `../short_url_frontend/src/api/domains.ts`
- Modify: `../short_url_frontend/src/api/shortLinks.ts`
- Create: `../short_url_frontend/src/api/accessLogs.ts`
- Modify: `../short_url_frontend/src/api/users.ts`
- Create: `../short_url_frontend/src/api/security.ts`
- Create: `../short_url_frontend/src/api/dashboard.ts`
- Create: `../short_url_frontend/src/stores/domain.ts`

**Interfaces:**
- Produces shared `Page<T>`, `CursorPage<T>`, and `ApiError` contracts.
- Produces `User.role` values `admin | subaccount` and domain grants `read | manage`.
- Produces aggregate `ShortLinkWrite`, parsed-UA `AccessLog`, and dashboard response types.
- Produces a persisted selected-domain store that falls back safely when access changes.

- [ ] **Step 1: Run the current type check as the baseline**

Run: `pnpm type-check`

Expected: PASS before contract replacement.

- [ ] **Step 2: Define the new response and write types**

Use discriminated literal unions and one paginated shape:

```ts
export type UserRole = 'admin' | 'subaccount';
export type DomainAccessLevel = 'read' | 'manage';
export type AccessResult = 'allowed' | 'blocked' | 'error';
export type BlockReason = 'ip' | 'proxy' | 'country' | 'bot' | 'platform' | 'referer' | 'other';

export interface Page<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
}

export interface CursorPage<T> {
  items: T[];
  next_cursor: string | null;
}

export interface ApiError {
  code: string;
  message: string;
  details: Record<string, string[]> | null;
}
```

`ShortLinkWrite` must contain `domain_id`, `custom_alias`, `note`, `is_active`, complete `destinations`, and one `policy`. `AccessLog` must expose request URL, snapshots, result/reason/detail, country, Referer, raw UA, parsed browser/OS/device, and bot name.

- [ ] **Step 3: Replace every API wrapper with the new route groups**

Each wrapper returns `response.data` and accepts a typed params object. The short-link module performs one request per create or update:

```ts
export const createShortLink = async (payload: ShortLinkWrite) =>
  (await api.post<ShortLink>('/short-links', payload)).data;

export const updateShortLink = async (id: string, payload: ShortLinkWrite) =>
  (await api.put<ShortLink>(`/short-links/${id}`, payload)).data;

export const listAccessLogs = async (params: AccessLogQuery) =>
  (await api.get<CursorPage<AccessLog>>('/access-logs', { params })).data;
```

Add the new methods without changing old page imports yet. The legacy methods remain reachable only until their consuming pages are replaced in Tasks 3–6, then Task 7 removes methods for `/rules`, `/permissions`, `/api/admin`, `/api/logs`, and `/api/ip-blacklist`.

- [ ] **Step 4: Normalize backend errors once in Axios**

Export a helper that preserves the stable backend message:

```ts
export const getApiErrorMessage = (error: unknown): string => {
  if (axios.isAxiosError<ApiError>(error)) {
    return error.response?.data?.message ?? '请求失败，请稍后重试';
  }
  return '请求失败，请稍后重试';
};
```

Keep cookie credentials and the existing 401 logout behavior. Do not show global success messages in the interceptor.

- [ ] **Step 5: Implement selected-domain state**

```ts
interface DomainState {
  selectedDomainId: string | null;
  selectDomain: (domainId: string | null) => void;
  reconcileDomains: (domains: Domain[]) => void;
}
```

Persist only the ID. `reconcileDomains` keeps it when still authorized, otherwise selects the first active domain or null.

- [ ] **Step 6: Run type check and commit**

Run: `pnpm type-check`

Expected: PASS. New contracts coexist temporarily with old page-only exports until the consuming pages are replaced.

```bash
git add src/types src/utils/axios.ts src/api src/stores/domain.ts
git commit -m "refactor: align frontend API contracts"
```

### Task 2: Build the fixed viewport application shell

**Files:**
- Modify: `../short_url_frontend/src/main.tsx`
- Modify: `../short_url_frontend/src/App.tsx`
- Replace: `../short_url_frontend/src/layouts/AppLayout.tsx`
- Replace: `../short_url_frontend/src/router/index.tsx`
- Modify: `../short_url_frontend/src/components/AuthGuard.tsx`
- Create: `../short_url_frontend/src/components/PageHeader.tsx`
- Create: `../short_url_frontend/src/components/TablePage.tsx`
- Create: `../short_url_frontend/src/hooks/useTableScrollY.ts`
- Create: `../short_url_frontend/src/styles/app.css`

**Interfaces:**
- Produces a fixed `100dvh` shell with a collapsible sidebar and compact header.
- Produces `PageHeader` and `TablePage` primitives used by all business pages.
- Produces `useTableScrollY(containerRef, reservedHeight) -> number` for Ant Design table bodies.

- [ ] **Step 1: Add the root overflow and flex layout rules**

```css
html,
body,
#root {
  width: 100%;
  height: 100%;
  margin: 0;
  overflow: hidden;
}

.app-shell {
  height: 100dvh;
  min-height: 0;
}

.app-main,
.table-page,
.table-region {
  min-height: 0;
  min-width: 0;
}

.app-main,
.table-page {
  display: flex;
  flex: 1;
  flex-direction: column;
  overflow: hidden;
}
```

Import `styles/app.css` from `main.tsx`. Configure Ant Design theme tokens with a blue primary color, neutral background, 6px control radius, and no decorative gradients.

- [ ] **Step 2: Implement reusable header and table-page structure**

`TablePage` receives header, filters, table, and footer nodes. It renders header and filters at intrinsic height, then a `.table-region` with `flex: 1; overflow: hidden`, and finally the footer. Do not wrap the table in another Card.

- [ ] **Step 3: Implement measured Ant Design table height**

Use `ResizeObserver` on the table region and calculate from its current content box:

```ts
export function useTableScrollY(
  ref: RefObject<HTMLElement | null>,
  reservedHeight = 47,
) {
  const [scrollY, setScrollY] = useState(320);
  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;
    const update = () => setScrollY(Math.max(160, node.clientHeight - reservedHeight));
    update();
    const observer = new ResizeObserver(update);
    observer.observe(node);
    return () => observer.disconnect();
  }, [ref, reservedHeight]);
  return scrollY;
}
```

- [ ] **Step 4: Replace the shell and route table**

Use exact routes:

```text
/                 Overview
/short-links      Short Links
/access-logs      Access Logs
/users            User Management, admin only
/security         Security, admin only
```

Remove `/domains`, `/blacklist`, `/logs`, and `/short-links/:id`. `AuthGuard` must include an admin-only guard that redirects a subaccount to `/` without flashing restricted content.

- [ ] **Step 5: Verify the shell before page refactors**

Run: `pnpm type-check`

Expected: remaining failures identify old page contracts, not shell or router types.

Run: `pnpm dev --host 127.0.0.1 --port 18080`

Expected: login renders, authenticated shell occupies exactly one viewport, and browser body `scrollHeight === clientHeight`.

- [ ] **Step 6: Commit**

```bash
git add src/main.tsx src/App.tsx src/layouts src/router src/components/AuthGuard.tsx src/components/PageHeader.tsx src/components/TablePage.tsx src/hooks src/styles
git commit -m "refactor: establish fixed admin shell"
```

### Task 3: Rebuild Overview around useful operations metrics

**Files:**
- Replace: `../short_url_frontend/src/pages/Dashboard.tsx`
- Create: `../short_url_frontend/src/features/dashboard/MetricStrip.tsx`
- Create: `../short_url_frontend/src/features/dashboard/VisitTrend.tsx`
- Create: `../short_url_frontend/src/features/dashboard/RankingList.tsx`

**Interfaces:**
- Consumes `GET /api/dashboard?domain_id&days`.
- Shows visits, allowed, blocked, unique IP, daily trend, top short links, and top Referers.

- [ ] **Step 1: Build one query keyed by selected domain and time range**

```ts
const dashboardQuery = useQuery({
  queryKey: ['dashboard', selectedDomainId, days],
  queryFn: () => getDashboard({ domain_id: selectedDomainId!, days }),
  enabled: Boolean(selectedDomainId),
});
```

The page reconciles authorized domains before enabling the query. An empty authorized-domain list shows one concise Empty state.

- [ ] **Step 2: Implement the dense dashboard hierarchy**

Render a page header with domain selector and 7/30-day segmented control, one four-column metric strip, then a two-column area with trend at roughly two-thirds width and ranked lists at one-third. Use plain white surfaces with one-pixel borders; do not place icons inside metric tiles.

- [ ] **Step 3: Format chart and ranking values**

Use Recharts `ResponsiveContainer`, `LineChart`, `XAxis`, `YAxis`, `Tooltip`, and two lines for allowed/blocked. Display the short-code plus note in top links and collapse empty Referers under `Direct / Unknown` only when the backend returns that label.

- [ ] **Step 4: Verify and commit**

Run: `pnpm type-check`

Expected: Dashboard and its feature components have no TypeScript errors.

```bash
git add src/pages/Dashboard.tsx src/features/dashboard
git commit -m "feat: focus overview on traffic health"
```

### Task 4: Rebuild Short Links as one table and aggregate drawer

**Files:**
- Replace: `../short_url_frontend/src/pages/ShortLinks.tsx`
- Create: `../short_url_frontend/src/features/short-links/schema.ts`
- Create: `../short_url_frontend/src/features/short-links/ShortLinkDrawer.tsx`
- Create: `../short_url_frontend/src/features/short-links/DestinationsEditor.tsx`
- Create: `../short_url_frontend/src/features/short-links/PolicyEditor.tsx`
- Create: `../short_url_frontend/src/features/domains/DomainDrawer.tsx`

**Interfaces:**
- Uses the aggregate create/update payload without follow-up destination or rule calls.
- Gives read users view access and manage users create/edit/activation controls.
- Gives admins domain-management access beside the domain selector.

- [ ] **Step 1: Define the exact Zod form schema**

```ts
const destinationSchema = z.object({
  id: z.string().uuid().optional(),
  url: z.url('请输入有效 URL'),
  type: z.enum(['allowed', 'blocked']),
  weight: z.number().int().min(0),
  is_active: z.boolean(),
});

export const shortLinkSchema = z.object({
  domain_id: z.string().uuid(),
  custom_alias: z.string().trim().max(64).optional(),
  note: z.string().trim().max(255).optional(),
  is_active: z.boolean(),
  destinations: z.array(destinationSchema).min(1),
  policy: linkPolicySchema,
}).superRefine((value, context) => {
  if (!value.destinations.some((item) => item.type === 'allowed' && item.is_active)) {
    context.addIssue({ code: 'custom', path: ['destinations'], message: '至少需要一个启用的正常目标地址' });
  }
});
```

`linkPolicySchema` rejects an `allow` mode with an empty list and normalizes countries to uppercase.

- [ ] **Step 2: Build destination and policy editors from Ant Design controls**

Use `useFieldArray` for destinations. Each destination row contains type, URL, weight, active state, and remove action. Policy uses mode Selects followed by tag-mode Selects for values, plus proxy and bot Switches. Keep policy copy direct and operational.

- [ ] **Step 3: Implement one create/edit drawer**

The 720px drawer loads details only for edit, calls `reset(toFormValues(detail))`, and submits exactly one `POST` or `PUT`. On failure, keep the drawer and current values open and use `getApiErrorMessage`. On success, close, notify, and invalidate short-link and dashboard queries.

- [ ] **Step 4: Implement the table page**

Columns are full short URL with copy action, note, destination summary, policy summary, state, visit count, updated time, and actions. Filters are domain, keyword, and active state. Use backend page pagination and `scroll={{ x: 1240, y: scrollY }}`.

For read-only users, hide write actions rather than rendering disabled clutter. Clicking the full URL copies it; clicking Edit opens the drawer.

- [ ] **Step 5: Move domain administration into an admin drawer**

`DomainDrawer` lists domains with inline state and edit actions and opens a compact nested form for create/update. Delete requires `Modal.confirm` and handles the backend 409 when links still reference the domain. Refresh the selector and reconcile selected-domain state after changes.

- [ ] **Step 6: Verify and commit**

Run: `pnpm type-check`

Expected: Short Links and all related feature components compile without old `AccessRule`, `Permission`, `description`, or `denied` references.

```bash
git add src/pages/ShortLinks.tsx src/features/short-links src/features/domains
git commit -m "feat: make short-link editing atomic"
```

### Task 5: Rebuild Access Logs for investigation

**Files:**
- Replace: `../short_url_frontend/src/pages/Logs.tsx`
- Create: `../short_url_frontend/src/features/logs/LogFilters.tsx`
- Create: `../short_url_frontend/src/features/logs/AccessLogDrawer.tsx`
- Create: `../short_url_frontend/src/features/logs/ua.tsx`

**Interfaces:**
- Shows full short URL, note, country, result/reason, parsed UA, and Referer directly in the table.
- Uses backend cursor pagination with a frontend cursor stack.
- Opens raw UA, decision detail, and all immutable snapshots in a row drawer.

- [ ] **Step 1: Implement primary and advanced filters**

Primary row: domain, keyword, time range, result, Search, and Reset. Collapsible advanced row: short link, country, and block reason. Build a submitted query snapshot so typing does not request on every keystroke.

Convert the local date range to ISO instants before sending it. Reset also clears cursor history.

- [ ] **Step 2: Implement the cursor stack**

```ts
const [cursorStack, setCursorStack] = useState<Array<string | null>>([null]);
const currentCursor = cursorStack.at(-1) ?? null;

const goNext = () => {
  const nextCursor = logsQuery.data?.next_cursor;
  if (nextCursor) {
    setCursorStack((stack) => [...stack, nextCursor]);
  }
};

const goPrevious = () => {
  setCursorStack((stack) => (stack.length > 1 ? stack.slice(0, -1) : stack));
};
```

The query key includes the submitted filters and current cursor. Show Previous when stack length is greater than one and Next only when `next_cursor` exists. Do not display fake page numbers or totals.

- [ ] **Step 3: Build investigation-oriented columns**

Use these columns in this order: time, full short URL, note, IP, country, result, reason, browser/OS/device, Referer. Use one compact UA summary such as `Chrome 120 · Android 13 · Samsung Galaxy`; render `Bot · Googlebot` for bots and `Unparsed` when backfill has not run.

Use ellipsis plus native/Ant tooltip for long URL, note, and Referer values. Keep raw UA out of the table.

- [ ] **Step 4: Build the row detail drawer**

Display request URL, domain/code/note snapshots, IP/country, result, reason/detail, target URL, Referer, parsed browser/OS/device/bot fields, raw UA, and accessed time with `Descriptions`. The drawer reads the selected row and does not make another request.

- [ ] **Step 5: Verify table containment and commit**

Run: `pnpm type-check`

Expected: Access Logs compiles without the old array-based log response.

In the browser, load at least 30 rows and verify `document.documentElement.scrollHeight === document.documentElement.clientHeight` while `.ant-table-body.scrollHeight > .ant-table-body.clientHeight`.

```bash
git add src/pages/Logs.tsx src/features/logs
git commit -m "feat: turn access logs into an investigation table"
```

### Task 6: Rebuild User Management and Security

**Files:**
- Replace: `../short_url_frontend/src/pages/Users.tsx`
- Rename: `../short_url_frontend/src/pages/Blacklist.tsx` to `../short_url_frontend/src/pages/Security.tsx`
- Create: `../short_url_frontend/src/features/users/schema.ts`
- Create: `../short_url_frontend/src/features/users/UserDrawer.tsx`
- Create: `../short_url_frontend/src/features/security/BlacklistDrawer.tsx`

**Interfaces:**
- User drawer assigns none/read/manage per domain.
- Security page manages exact global IPv4/IPv6 blacklist entries.
- Both pages are inaccessible to subaccounts in routes and menus.

- [ ] **Step 1: Build the user form schema and domain grant editor**

```ts
export const userSchema = z.object({
  username: z.string().trim().min(3).max(64),
  password: z.string().min(8).optional(),
  role: z.enum(['admin', 'subaccount']),
  is_active: z.boolean(),
  domain_access: z.array(z.object({
    domain_id: z.string().uuid(),
    access_level: z.enum(['read', 'manage']),
  })),
});
```

Require password on create. For subaccounts, render each domain with a three-state Segmented control: none, read, manage. Administrators submit an empty grant list.

- [ ] **Step 2: Implement the user table and drawer**

Columns: username, role, status, domain-access summary, created time, actions. Use backend pagination. The deactivate action is confirmed; disable it for the current user in the UI and show the backend `LAST_ADMIN_REQUIRED` message when the last-admin guard wins.

- [ ] **Step 3: Implement the Security page**

Use one IP-blacklist table with IP, reason, creator, created time, and actions. Add/edit uses a narrow drawer with an IP input and optional reason. Remove uses `Modal.confirm`. Do not add MaxMind settings or unrelated security cards.

- [ ] **Step 4: Verify role protection and commit**

Run: `pnpm type-check`

Expected: User Management and Security compile against the new admin APIs.

Sign in as a subaccount, enter `/users` and `/security` directly, and verify both redirect to `/` without issuing their data requests.

```bash
git add src/pages/Users.tsx src/pages/Security.tsx src/features/users src/features/security
git commit -m "feat: simplify users and global security"
```

### Task 7: Remove superseded frontend code and dependencies

**Files:**
- Delete: `../short_url_frontend/src/pages/Domains.tsx`
- Delete: `../short_url_frontend/src/pages/ShortLinkDetail.tsx`
- Delete: `../short_url_frontend/src/components/short-link/AccessRuleTab.tsx`
- Delete: `../short_url_frontend/src/components/short-link/PermissionTab.tsx`
- Delete: `../short_url_frontend/src/components/short-link/TargetUrlTab.tsx`
- Delete: `../short_url_frontend/src/api/blacklist.ts`
- Delete: `../short_url_frontend/src/assets/hero.png`
- Delete: `../short_url_frontend/src/assets/react.svg`
- Delete: `../short_url_frontend/src/assets/vite.svg`
- Modify: `../short_url_frontend/package.json`
- Modify: `../short_url_frontend/pnpm-lock.yaml`
- Modify: `../short_url_frontend/src/types/index.ts`
- Modify: `../short_url_frontend/src/api/domains.ts`
- Modify: `../short_url_frontend/src/api/shortLinks.ts`
- Delete: `../short_url_frontend/src/api/logs.ts`
- Modify: `../short_url_frontend/src/api/users.ts`

- [ ] **Step 1: Search for legacy concepts and unused imports**

Run:

```bash
rg -n "operator|client|description|default_action|denied|AccessRule|PermissionTab|/api/logs|/api/admin|/blacklist|/domains|@ant-design/charts" src package.json
```

Expected: only intentional compatibility-free user-facing wording remains; no old route, role, model, or API reference remains.

- [ ] **Step 2: Delete superseded pages, tabs, and starter assets**

Remove files only after `rg` confirms they have no live imports. Remove legacy types from `src/types/index.ts` and re-export the final contracts from `src/types/api.ts`. Remove legacy methods from the retained API modules and delete the obsolete log and blacklist modules. Keep ordinary Ant Design components rather than replacing deleted tabs with custom equivalents.

- [ ] **Step 3: Remove the duplicate chart dependency**

Run: `pnpm remove @ant-design/charts`

Expected: `package.json` and `pnpm-lock.yaml` retain Recharts and no longer contain `@ant-design/charts`.

- [ ] **Step 4: Run static verification**

Run: `pnpm type-check`

Expected: PASS.

Run: `pnpm lint`

Expected: PASS.

Run: `pnpm build`

Expected: PASS and `dist/` is produced.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: remove obsolete frontend flows"
```

### Task 8: Exercise the integrated application in the browser

**Files:**
- Modify only files implicated by verified integration defects.

- [ ] **Step 1: Start the tested backend and frontend**

Backend, from `../short_url`:

```bash
docker compose up -d db redis app
curl --fail http://127.0.0.1:18000/health
```

Frontend, from `../short_url_frontend`:

```bash
pnpm dev --host 127.0.0.1 --port 18080
```

Expected: backend health succeeds and the login page loads at `http://127.0.0.1:18080/login`.

- [ ] **Step 2: Exercise administrator flows against the real API**

Sign in, then create/update a domain, subaccount, short link with normal and blocked destinations, policy, and blacklist entry. Trigger one allowed redirect and one blocked redirect. Verify both log rows show full URL, country, result, reason, parsed UA, and Referer. Remove the temporary data through the UI.

- [ ] **Step 3: Exercise subaccount authorization**

Use one read grant and one manage grant. Verify the read-only domain hides mutation controls, the manage domain permits short-link changes, ungranted domains are absent, and direct administrative URLs redirect without restricted API requests.

- [ ] **Step 4: Verify layout at target widths**

At 1440px and 1280px widths, verify the browser body has no scrollbars; Short Links, Access Logs, Users, and Security keep headers/filters/pagination visible; only table bodies scroll vertically; wide tables scroll horizontally inside their table regions. At a narrower width, verify sidebar collapse and low-priority column hiding.

- [ ] **Step 5: Inspect console and network failures**

Expected: no React key/hook warnings, unhandled promise rejections, failed chunk requests, obsolete API calls, unexpected 401/403/404 responses, or requests repeated by render loops.

- [ ] **Step 6: Re-run all verification after integration fixes**

Backend, from `../short_url`:

```bash
uv run pytest -q
```

Frontend, from `../short_url_frontend`:

```bash
pnpm type-check
pnpm lint
pnpm build
```

Expected: every command passes.

- [ ] **Step 7: Commit verified integration fixes**

If files changed during QA:

```bash
git add -A
git commit -m "fix: resolve integrated admin workflow defects"
```

If no files changed, do not create an empty commit. Leave `http://127.0.0.1:18080` open in the in-app browser for user review.
