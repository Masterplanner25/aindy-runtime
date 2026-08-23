### Added — the runtime publishes its HTTP route inventory, and CI keeps it current (FR-22)

`AINDY/route_inventory.json` lists every `(method, path)` the runtime serves in the
`runtime-only` boot profile, with OpenAPI tags. It ships inside the wheel, so a consumer reads
the surface for the version they installed without booting anything:

```python
import json
from importlib.resources import files

inventory = json.loads(files("AINDY").joinpath("route_inventory.json").read_text())
```

**Why this exists.** The app team's API reference documents ~51 runtime-owned routes, and their
guard is scoped to `/apps/*` — so the runtime half of their file was a curated inventory nothing
checked, accurate when written and free to drift afterwards. They deliberately did not extend
their guard over our routes: an app-side mechanism policing a runtime-owned surface makes the
app responsible for something it does not own. So the runtime guards its own.

`scripts/check_route_inventory.py` regenerates the file; `--check` fails when it is stale, and
`tests/unit/test_route_inventory.py` runs that comparison in `Runtime Contracts` — in **both**
directions, because a route silently leaving the published surface matters more to a pinned
consumer than one appearing.

**★ Correction worth acting on if you consume our routes: `/apps/*` is not an app-ownership
boundary.** 35 routes under that prefix — coordination, memory, agent — are served by the
runtime with no plugins loaded. A guard treating `/apps/*` as "the app's surface" is wrong about
a third of it. Subtracting this inventory from a booted app's surface gives the genuinely
app-owned set without curating one by hand.

Two things absence means precisely: the legacy alias surface
(`AINDY_ENABLE_LEGACY_SURFACE=true`) is excluded — the inventory publishes supported routes, not
compatibility shims; and there is no version field, because the file is committed and a stamped
version would make every release bump edit it. **A removal from this file is a breaking change
for anyone pinned to it.**
