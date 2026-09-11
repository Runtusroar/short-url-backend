# Short URL Admin Refactor Design

Date: 2026-09-12  
Status: Approved in design review

## 1. Goal

Refactor the existing short URL backend and admin frontend into a smaller, clearer system without losing production short links or access history.

The completed system must:

- support administrators and domain-scoped subaccounts;
- explain every blocked visit with a stable reason and readable detail;
- retain existing short links, destinations, users, domains, and access logs through migration;
- provide a desktop-oriented admin UI with overview, short links, access logs, user management, and security;
- keep page scrolling inside data tables rather than on the browser viewport;
- use maintained libraries for common functions instead of custom implementations;
- exercise every API, including authorization failures, with automated tests.

## 2. Non-goals

The first version will not add:

- a generic RBAC or permission-expression engine;
- microservices, a repository layer, a message queue, or a background worker;
- CIDR blacklist entries, blacklist expiry, or IP allowlists;
- database partitioning, full-text search, or analytics warehousing;
- a dedicated mobile administration experience;
- CI/CD or production deployment automation.

## 3. Chosen Approach

Use a focused refactor rather than patching the current model indefinitely or rewriting the application from scratch.

Keep FastAPI, SQLAlchemy, Alembic, PostgreSQL, Redis, React, TanStack Query, Ant Design, React Hook Form, Zod, Axios, Day.js, and Recharts. Remove overlapping or obsolete dependencies and code as the replacement flows become complete.

The backend stays a single FastAPI application. Simple CRUD routes may use SQLAlchemy directly. Only access decisions, GeoIP, proxy lookup, user-agent parsing, authentication, and other independently testable business rules belong in services. Do not introduce repository, manager, or command layers without a concrete need.

## 4. Backend Structure

The target backend layout is:

```text
app/
├── api/
│   ├── auth.py
│   ├── dashboard.py
│   ├── domains.py
│   ├── logs.py
│   ├── security.py
│   ├── short_links.py
│   └── users.py
├── core/
│   ├── config.py
│   ├── errors.py
│   └── security.py
├── db/
│   ├── base.py
│   ├── session.py
│   └── models/
│       ├── access_log.py
│       ├── domain.py
│       ├── security.py
│       ├── short_link.py
│       └── user.py
├── schemas/
│   ├── auth.py
│   ├── dashboard.py
│   ├── domain.py
│   ├── log.py
│   ├── security.py
│   ├── short_link.py
│   └── user.py
├── services/
│   ├── access.py
│   ├── geoip.py
│   ├── proxy.py
│   ├── short_code.py
│   └── user_agent.py
├── dependencies.py
└── main.py
```

Files are divided by business responsibility, not by an abstract architecture template. Shared helpers must have more than one real caller or remain next to their caller.

## 5. Database Model

### 5.1 `users`

- Keep the existing UUID primary key, username, password hash, active flag, and timestamps.
- Restrict `role` to `admin` or `subaccount` with application validation and a database check constraint.
- Administrators have global access and do not need domain-access rows.
- Prefer deactivation over hard deletion so ownership and audit history remain meaningful.
- Prevent deactivating the last active administrator.

### 5.2 `domains`

- Keep existing UUIDs, domain names, active state, and timestamps.
- Normalize domain names to lowercase without scheme, path, or port and enforce uniqueness.
- Remove fallback routing through a default domain. An unknown `Host` returns 404.
- Existing domain rows are retained. The obsolete default marker is removed after migration.

### 5.3 `user_domain_access`

This table replaces `user_domains` and `short_link_permissions` as the only subaccount authorization mapping.

- Composite primary key: `(user_id, domain_id)`.
- `access_level`: `read` or `manage`, enforced by a check constraint.
- `read` permits overview, short-link, and access-log reads for the domain.
- `manage` includes `read` and permits short-link creation, editing, and activation changes.
- User and domain foreign keys use `ON DELETE CASCADE`.
- Administrators bypass this table.

### 5.4 `short_links`

- Keep existing UUID, domain, short code, custom-alias marker, owner, active state, and timestamps.
- Rename `description` to `note`.
- Keep uniqueness on `(domain_id, short_code)`.
- Short codes remain case-sensitive for existing data. Generated codes are lowercase; custom aliases preserve the submitted case.
- The owner foreign key uses `ON DELETE SET NULL`; ownership is informational, while domain access controls authorization.
- Remove `default_action` after access-rule conversion. New policies describe restrictions directly and an unrestricted active link is allowed.

### 5.5 `target_urls`

- Retain existing rows and UUIDs.
- Restrict `url_type` to `allowed` or `blocked`.
- Require a non-negative weight.
- Keep multiple weighted destinations for normal and blocked traffic.
- Deleting a destination must not erase its URL from historical logs.

### 5.6 `link_policies`

Each short link has at most one explicit policy row. This replaces the current ordered, multi-row access-rule model for new runtime decisions.

Fields:

- `short_link_id`, primary key and cascading foreign key;
- `country_mode`: `off`, `allow`, or `block`;
- `countries`: JSON array of uppercase ISO country codes;
- `platform_mode`: `off`, `allow`, or `block`;
- `platforms`: JSON array using DeviceDetector device categories normalized by the application;
- `referer_mode`: `off`, `allow`, or `block`;
- `referer_patterns`: JSON array of hostname or wildcard patterns;
- `block_proxy`: boolean;
- `block_bot`: boolean;
- `updated_at`.

An absent policy or a dimension with mode `off` means unrestricted. The database validates mode values; Pydantic validators normalize list values and reject contradictory empty allowlists.

### 5.7 `ip_blacklist`

- Remains global across every domain and short link.
- Store exact IPv4 or IPv6 addresses as PostgreSQL `INET` with a unique constraint.
- Keep reason, creator, and creation timestamp.
- The creator foreign key uses `ON DELETE SET NULL`.
- The first version does not accept networks or ranges.

### 5.8 `ip_reputation`

This table persists successful MaxMind lookups and also prevents unnecessary repeated calls.

Fields:

- `ip`, PostgreSQL `INET` primary key;
- `is_proxy`, boolean;
- `proxy_type`, nullable normalized string;
- `checked_at` and `expires_at` UTC timestamps.

Only the fields needed by access decisions are stored. A fresh row is reused. Expired or absent rows may trigger a new lookup. Provider failures and insufficient balance are not treated as proxy results.

### 5.9 `access_logs`

Access logs are historical records and must survive deletion of mutable business objects.

References:

- `short_link_id`, `domain_id`, and `target_url_id` become nullable and use `ON DELETE SET NULL`.

Snapshots:

- `request_url` stores the complete requested short URL;
- `domain_name`, `short_code`, and `short_link_note` store values at access time;
- `target_url` stores the selected destination value.

Decision fields:

- `result`: `allowed`, `blocked`, or `error`;
- `block_reason`: nullable `ip`, `proxy`, `country`, `bot`, `platform`, `referer`, or `other`;
- `block_detail`: nullable readable explanation.

Request fields:

- `ip`, nullable PostgreSQL `INET`;
- `country_code`, nullable two-character country code;
- `referer`, nullable text;
- `ua_raw`, nullable text.

Parsed user-agent fields:

- browser name and version;
- operating-system name and version;
- device type, brand, and model;
- bot name when detected.

Time:

- Store only `accessed_at` as a UTC timezone-aware timestamp.
- Remove the fixed `accessed_at_plus8` and `dedup_bucket` columns.
- Convert dates to the configured display timezone when querying.

Indexes are limited to demonstrated queries:

- `(accessed_at DESC, id DESC)`;
- `(domain_id, accessed_at DESC, id DESC)`;
- `(short_link_id, accessed_at DESC, id DESC)`;
- `(result, accessed_at DESC)`;
- `(country_code, accessed_at DESC)`;
- `(block_reason, accessed_at DESC)`.

No full-text or trigram index is introduced initially. Short-code and note searches may use ordinary filters at the current expected scale and can be measured later.

## 6. Migration and Data Preservation

Migration must be forward-only and tested against both an empty database and a fixture representing the current production schema.

1. Create new tables, nullable columns, constraints, and indexes without removing old fields.
2. Map user roles: `admin` stays `admin`; `operator` and `client` become `subaccount`.
3. Copy domain grants into `user_domain_access`: former operators receive `manage`; former clients receive `read`; admin grant rows are omitted. This intentionally replaces former per-link client grants with the approved domain-wide read scope.
4. Rename or copy `description` into `note` while retaining every short-link UUID.
5. Rename existing target type `denied` to `blocked` while retaining every destination UUID.
6. Run a policy-conversion preflight before destructive rule changes. It considers both ordered rules and each link's current `default_action`. Rules that can be represented by one explicit policy are converted automatically and the converted behavior is checked against representative inputs. If a rule set cannot be represented equivalently, the preflight lists affected short-link IDs and stops before dropping old structures. It must never silently broaden or block traffic.
7. Backfill log snapshots from existing domain, short-link, and target rows.
8. Map historical `allowed` to `allowed`; historical `denied` and `blocked` to `blocked`. Set their reason to `other` and detail to `Historical record: the previous version did not store a specific reason.`
9. Convert valid textual IP values to `INET`; invalid or `unknown` values become null.
10. Add `SET NULL` foreign keys only after snapshot backfill.
11. Backfill parsed UA fields with an idempotent batch command after the schema migration to avoid a long schema lock. Until a row is processed, the UI displays `Unparsed` and still exposes the raw UA in its detail drawer.
12. Drop obsolete per-link permission, ordered-rule, `default_action`, default-domain, timezone-date, and deduplication structures only after conversion checks pass.

The application and migration will be deployed together. No backward compatibility with the old frontend API is required, but database data compatibility is required.

## 7. Authentication and Authorization

Authentication remains token/cookie based using the existing password-hashing and JWT libraries.

Authorization is always applied in backend queries:

- an administrator can access all domains and administrative endpoints;
- a subaccount can read only domains with an access row;
- a subaccount can mutate short links only where the access row is `manage`;
- user-management, domain-management, and security endpoints require an administrator;
- frontend menu visibility is only a convenience and is never the security boundary.

Object lookups combine object ID and authorized domain scope. They do not fetch an object first and check scope later when the scope can be expressed in SQL.

## 8. Redirect Decision Flow

The redirect path is deterministic:

1. Normalize and resolve the request host; reject unknown or inactive domains.
2. Resolve an active short link using the host and exact short code.
3. Sanitize the client IP from trusted proxy headers, and capture Referer and raw UA.
4. Parse the UA once with the Python DeviceDetector package.
5. Check the global exact-IP blacklist.
6. If bots are blocked and the visitor is a bot, block with reason `bot`.
7. If proxy blocking is enabled:
   - a bot is treated as a proxy without calling MaxMind, preserving the agreed cost-saving behavior;
   - a non-bot uses a fresh persisted reputation result or calls MaxMind;
   - MaxMind timeout, provider failure, or insufficient balance fails open.
8. Apply country, platform, and Referer policy dimensions in that order.
9. Select a weighted allowed or blocked destination.
10. Produce one decision object containing result, primary reason, detail, and target.
11. Write the access-log snapshot and return a 302 response.

The first condition that actually blocks the request is the primary reason. If no matching target exists, return the existing structured application error and attempt to log `error` with reason `other`.

A failure to persist the access log is reported to server logging but must not change a valid redirect decision.

## 9. API Contract

Endpoint groups:

- `/api/auth/*`: login, logout, and current user;
- `/api/dashboard`: summary for authorized domains;
- `/api/short-links/*`: short links, destinations, and policy;
- `/api/access-logs`: log search and cursor pagination;
- `/api/users/*`: users and domain access, administrator only;
- `/api/domains/*`: domain administration, administrator only;
- `/api/security/ip-blacklist/*`: global blacklist, administrator only.

All errors use one JSON envelope containing a stable code, readable message, and optional field details.

List conventions:

- small administrative lists use page, page size, total, and items;
- access logs use a stable `(accessed_at, id)` cursor and return items plus `next_cursor`;
- access-log filters include domain, short link, short code/note keyword, time range, country, result, and block reason;
- access-log rows return full short URL, snapshots, parsed UA summary, Referer, and block explanation.

Dashboard and list endpoints apply domain authorization before aggregation or pagination.

## 10. Frontend Information Architecture

Continue using the existing React application and Ant Design components. The primary navigation contains:

1. Overview;
2. Short Links;
3. Access Logs;
4. User Management, administrator only;
5. Security, administrator only.

Domain management moves into an administrator-only drawer opened beside the domain selector on the Short Links page. It is not a primary navigation item.

Subaccounts do not see User Management or Security. Their selected-domain choices contain only authorized domains.

## 11. Frontend Layout and Visual Direction

Use a conventional, restrained operations-admin visual language:

- neutral gray page background and white work surfaces;
- dark readable text and one blue accent color;
- modest radii, light dividers, and minimal shadow;
- no gradients, decorative metric icons, nested cards, or card-per-row layouts;
- existing Ant Design icons rather than custom SVG artwork.

The application root is exactly `100dvh` and the document body does not scroll. The sidebar and compact header remain fixed. The main content and each page use flex layout with `min-height: 0`. Page title, filters, and pagination remain visible; only the Ant Design table body scrolls vertically. Wide tables scroll horizontally inside their table region.

Desktop widths of 1440 and 1280 pixels are first-class targets. At smaller widths, the sidebar collapses and low-priority columns hide. A separate mobile workflow is not included.

## 12. Frontend Pages

### 12.1 Overview

Show four primary metrics for the selected domain and time window: visits, allowed visits, blocked visits, and unique IPs. Below them, show one visit-trend chart and compact ranked lists for top short links and Referers. Avoid empty decorative panels.

### 12.2 Short Links

Use one table with full short URL, note, destination summary, policy summary, active state, visit count, update time, and actions.

Creation and editing use one right-side drawer. Destinations and policy are sections in the same form. Do not route ordinary editing through a multi-tab detail page. Use schema validation through React Hook Form and Zod.

### 12.3 Access Logs

Show common filters in one line and optional filters in a collapsible secondary area.

Columns: access time, full short URL, note, IP, country, result, specific block reason, parsed browser/OS/device, and Referer. Raw UA is not a main column. Selecting a row opens a detail drawer with full snapshots, raw UA, parsed values, decision detail, and destination.

The frontend keeps a cursor stack to support Previous and Next navigation without inventing page numbers the backend cannot guarantee.

### 12.4 User Management

Use a table with username, role, status, domain-access summary, and creation time. Creation and editing happen in a drawer. Each domain has exactly three choices: none, read, or manage. The UI prevents disabling the last active administrator and also handles the backend error if state changes concurrently.

### 12.5 Security

The first version contains only the global IP blacklist table with IP, reason, creator, and creation time. Administrators can add, edit, and remove entries through Ant Design forms and confirmation dialogs. MaxMind provider state is operational configuration and is not exposed here initially.

## 13. Dependency Policy

- Replace `user-agents` with `device_detector`; use one UA parser only.
- Retain raw UA values because parsing rules evolve and some UA strings omit details.
- Keep Recharts and remove unused `@ant-design/charts`.
- Use Python `ipaddress` for request validation and PostgreSQL `INET` for storage.
- Use SQLAlchemy and Alembic for schema work; do not add another migration or ORM library.
- Use Ant Design for tables, forms, drawers, menus, tags, dialogs, and pagination.
- Do not add utility libraries for behavior already covered clearly by the standard library or current dependencies.

## 14. Error Handling

- Invalid or unauthorized domain/object access returns a stable 403 or 404 without exposing whether inaccessible data exists.
- Invalid IP, domain, URL, policy, and cursor inputs return field-level 422 responses through the common error envelope.
- MaxMind failures fail open and are logged server-side without marking the visitor as a proxy.
- GeoIP database failure yields an unknown country and does not automatically block the request.
- Missing allowed or blocked destinations returns a specific application error and attempts an `error/other` log.
- Database conflicts such as duplicate username, domain, short code, or blacklist IP use stable 409 codes.
- UI queries show actionable errors and retain the current filter/form state for retry.

## 15. Verification Strategy

### Backend unit tests

- role and domain-access checks;
- host, IP, short-code, and policy normalization;
- every decision reason and the documented decision order;
- DeviceDetector mapping for representative desktop, mobile, tablet, bot, app/WebView, and unknown UA strings;
- MaxMind persisted lookup reuse, expiry, bot skip, and fail-open paths;
- weighted target selection and missing-target behavior;
- access-log cursor encoding, decoding, and stable ordering.

### Backend API tests

Exercise every route with at least one success case and its relevant authentication, authorization, validation, not-found, and conflict cases. Explicitly verify:

- admin versus subaccount behavior;
- read versus manage domain access;
- cross-domain object access denial;
- short-link CRUD, destinations, and policies;
- dashboard authorization and aggregation;
- all access-log filters and cursor navigation;
- user and domain administration;
- global IP-blacklist CRUD;
- GET and HEAD redirect behavior and resulting log snapshots.

### Migration tests

- upgrade a fresh database to head;
- upgrade a fixture at the current Alembic head;
- assert preservation of user, domain, short-link, destination, and access-log counts and UUIDs;
- assert policy preflight refuses an unrepresentable rule set;
- run downgrade only where it is data-safe; destructive downgrade support is not a release requirement.

### Frontend verification

- TypeScript type check;
- Oxlint;
- production build;
- authenticated route and role-menu checks;
- manual browser smoke test at 1440 and 1280 widths;
- verify that the body does not scroll and that short-link, log, user, and blacklist tables scroll internally;
- exercise create/edit/filter flows against the real local API;
- inspect browser console and failed network requests before completion.

## 16. Delivery Sequence

1. Add characterization tests for current behavior and migration fixtures.
2. Implement the database migration and new models while preserving data.
3. Implement authorization and API contracts.
4. Implement redirect decisions, DeviceDetector, MaxMind persistence, and enriched logs.
5. Run the complete backend API and migration suite.
6. Refactor the frontend shell and four primary business areas plus Security.
7. Remove replaced routes, pages, components, dependencies, and dead code.
8. Run backend and frontend automated verification.
9. Verify the integrated UI in the in-app browser and leave the local preview open for review.

Production deployment and production-data migration are separate, explicit follow-up actions. This implementation prepares and tests the migration but does not modify the production server or database.
