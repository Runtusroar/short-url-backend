# Architecture

The application is organized by stable shared infrastructure, centralized ORM
models, business features, and external-system adapters.

```text
app/
├── core/
│   ├── client_ip.py
│   ├── config.py
│   ├── database.py
│   ├── exceptions.py
│   ├── rate_limit.py
│   └── security.py
├── features/
│   ├── access_logs/{router.py, schemas.py, service.py}
│   ├── auth/{router.py, schemas.py, service.py}
│   ├── blacklist/{router.py, schemas.py, service.py}
│   ├── domains/{dependencies.py, router.py, schemas.py, service.py}
│   ├── redirect/{router.py, service.py, ua.py}
│   ├── short_links/{router.py, schemas.py, service.py, short_code.py}
│   └── users/{router.py, schemas.py, service.py}
├── integrations/
│   ├── maxmind/country.py
│   └── redis.py
├── models/
│   ├── access_log.py
│   ├── access_rule.py
│   ├── blacklist.py
│   ├── domain.py
│   ├── short_link.py
│   └── user.py
└── main.py

tests/
├── architecture/
├── core/
├── deployment/
├── features/
│   ├── access_logs/
│   ├── blacklist/
│   ├── domains/
│   ├── redirect/
│   └── short_links/
├── integrations/
└── conftest.py
```

## Boundaries

- `app.main` composes the application and registers feature routers.
- Routers translate HTTP requests and responses only; they do not own business
  workflows or database transactions.
- Feature services own workflows and transaction boundaries.
- ORM declarations are centralized in `app.models`, which re-exports the
  models used elsewhere in the application.
- Request and response schemas live with the feature that owns them.
- Repeated complex database reads may gain a feature-local `queries.py` in the
  future; simple reads stay in the owning service.
- Integrations own communication with external systems, including Redis and
  the local MaxMind database.
- Production imports use only `app.core`, `app.models`, `app.features`, and
  `app.integrations`; legacy `app.routers`, `app.services`, and `app.schemas`
  modules do not exist.
