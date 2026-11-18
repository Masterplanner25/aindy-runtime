### Changed — SQLAlchemy 2.1.8, staying on psycopg2 (DEC-134) (#981)

- **SQLAlchemy 2.0.52 → 2.1.8.** 2.1 changed the default PostgreSQL driver for a bare `postgresql://` URL from
  psycopg2 to psycopg 3, which this package does not install. The bump therefore failed on every PostgreSQL
  deployment until the URL named its driver.
- **Operators change nothing.** The runtime now rewrites a bare `postgresql://` in `DATABASE_URL` to
  `postgresql+psycopg2://` where it creates each engine (`AINDY.config.normalize_database_url`), including Alembic's.
  `settings.DATABASE_URL` still reads as you wrote it. A URL that already names a driver is left alone, so
  `postgresql+psycopg://` still means psycopg 3, which you would have to install yourself.
- **If your own code creates engines from the same `DATABASE_URL`** (an app sharing the runtime's settings), it
  meets the same 2.1 default. Pass the URL through `normalize_database_url` too, or install psycopg 3.
- Also in 2.1, the ORM session now autoflushes before raw `text()` queries as well, so pending ORM writes reach
  the database before such a query runs. See SQLAlchemy's 2.1 migration notes for the rest (none of the other
  listed changes touch the runtime's models).
