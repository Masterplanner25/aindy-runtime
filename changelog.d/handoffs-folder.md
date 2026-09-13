### Added — `docs/handoffs/`: outbound handoffs to sibling repos, with per-ask status (docs/handoffs-folder)

The three `NODUS_HANDOFF_*.md` files move out of `docs/runtime/` into their own folder with an
index that records what each asks of Nodus and where it stands: the 5.0.1 blocking ask is
resolved (`nodus-mcp 0.1.3`) but two of its §4 asks are verifiably still open; the `nodus-a2a`
name collision is unresolved as far as PyPI shows; the `migrate-store` truncation is open. They
are the opposite direction from `docs/upgrades/` and the index says so. `Runtime Docs Validation`
checks the new folder.
