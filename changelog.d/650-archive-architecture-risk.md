### Changed — `ARCHITECTURE_RISK.md` archived (#650)

The 2026-06-03 complexity/blast-radius risk map was measurements, and measurements decay:
re-measured today, `startup.py` had grown 25% and `config.py`'s importer count 37% since it was
written, while `CLAUDE.md` still cited it as the current reference. The two coupling findings
with consequences are tracked as `CLI-1` and the `runtime_only.py` import-hazard section; the
rest are the deferred `LAYER-*` class. The archive entry says how to get a risk map that cannot
go stale: generate it.
