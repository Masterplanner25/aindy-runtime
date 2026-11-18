---
title: "App Handoff — Runtime v2.38.0"
api_version: "1.0"
last_verified: "2026-11-18"
status: current
owner: "platform-team"
---

# App handoff: runtime v2.38.0

**One dependency move with one ask: SQLAlchemy 2.0.52 → 2.1.8 (DEC-134, #981).** **No schema step**: Alembic stays
at `0022` and the schema contract at `2026-11-09`. No ui-kit or aindy-sdk release. No runtime route, scope or
default changed.

> ## ★ Read §1 before you bump the pin
>
> On SQLAlchemy 2.1 a bare `postgresql://` URL means **psycopg 3**, which neither of us installs. The runtime's own
> engines now name psycopg2 themselves. **Yours don't**, and SQLAlchemy reaches you through our exact pin. With the
> pin moved and nothing else, your container stops at its first step: `docker/entrypoint.sh` runs
> `scripts/ensure_pgvector.py`, which builds an engine from the bare `DATABASE_URL` and fails with
> `ModuleNotFoundError: No module named 'psycopg'`.
>
> **Make the §1 change in the same PR as the pin bump.** `normalize_database_url` is new in 2.38.0, so it can't land
> first; writing `postgresql+psycopg2://` works on both versions if you'd rather split it.

Bump the `constraints.txt` pin to `==2.38.0` (your contract test moves the floor with it), make the §1 change,
**rebuild the image** (`mcp` shares it, so recreate both).

> ## ★ After the rebuild: confirm what you are running
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v, sqlalchemy; from AINDY.config import normalize_database_url as n; from sqlalchemy.engine import make_url; print(v.__version__, sqlalchemy.__version__, make_url(n('postgresql://u@h/d')).get_dialect().driver, list(AINDY.__path__))"
> # expect: 2.38.0 2.1.8 psycopg2 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```

---

## 1. ★ Name the driver at every engine you create

```python
from AINDY.config import normalize_database_url

engine = create_engine(normalize_database_url(os.environ["DATABASE_URL"]))
```

It rewrites `postgresql://` (any case) to `postgresql+psycopg2://` and leaves everything else alone, byte for byte:
`sqlite://…`, a URL that already names a driver, the empty string. It is idempotent. The other way is to put
`postgresql+psycopg2://` into the URL itself; the runtime accepts that as is.

**Your sites, from a census of your `main` on 2026-11-18** (`create_engine`, `engine_from_config` and
`sqlalchemy.url` over `*.py` and workflow YAML; SQLite-only engines left out):

| Site | What breaks unfixed |
|---|---|
| `scripts/ensure_pgvector.py:23` | **the entrypoint's first step**: the container never boots |
| `alembic/alembic/env.py:63` (`sqlalchemy.url`) and `:191` (`create_engine`), both from `get_database_url()` | `alembic upgrade head`, which `scripts/deploy_bootstrap.py` runs on an existing database |
| `.github/workflows/deploy-bootstrap-guard.yml:139` and `:160` (inline Python) | that guard, on your adoption PR (it will tell you before a deploy does) |
| `scripts/replay_app_migrations.py:169` | the migration replay |
| `scripts/check_schema_default_parity.py:91`, `scripts/audit_leadgen_fixtures.py:59`, `scripts/inventory.py:333` | each script, when someone runs it |

In `env.py`, wrapping `database_url` where it is read (line ~57) covers both sites at once.

**Not affected:** `tests/fixtures/db.py` uses the runtime's `AINDY.db.database.engine` for Postgres, which already
names its driver; `scripts/deploy_bootstrap.py` uses that same engine. No `psycopg2.connect(...)` turned up in your
tree, but if one appears, leave it on the bare URL: that is libpq, not SQLAlchemy, and a `+psycopg2` scheme breaks it.

**A guard, if you want one.** Ours is `tests/unit/test_database_url_driver.py`: it walks every `create_engine` call in
the source with the AST and requires the URL argument to be `normalize_database_url(...)`, plus a line scan of
`.github/workflows/*.yml` for inline ones. Our own first CI run missed an inline workflow engine until the YAML
scan existed.

## 2. What else 2.1 changes

- **Autoflush before raw SQL.** A session now flushes pending ORM writes before a `session.execute(text(...))` too,
  not just before ORM queries. A raw query that used to read the database *without* your unflushed changes now sees
  them. Nothing in the runtime depended on the old behaviour; check any of yours that read through `text()` in the
  middle of a unit of work.
- `str(engine.url)` escapes differently. Don't build connection strings by formatting a `URL` object; the runtime
  doesn't.
- The rest of SQLAlchemy's 2.1 migration notes touch nothing in the runtime's models.

## 3. Report

On your adoption report: the confirm line above, from the container, `alembic current` on the app line after the deploy, and
whether `deploy-bootstrap-guard` passed on the adoption PR.

## 4. Your register

- **FR-70: closed** on your 2.37.0 report (runtime #982). **FR-14: closed** too (#983): the README's deploy section
  was the last place recommending a bare `bootstrap-schema`; it now branches on the exit code, with 4 winning over 3.
  That was the half your entry still had open.
- **FR-55 is filed on our side** (#983, it had only been mentioned before). Ask (b) runs against DEC-012, so it waits
  on the owner's decision; nothing to do on yours.
- Still open from earlier handoffs, no new ask: the trace question in your 2.34.0 report (§5 there).
