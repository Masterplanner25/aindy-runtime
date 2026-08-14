---
title: "Memory Address Space (MAS)"
last_verified: "2026-08-13"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Memory Address Space (MAS)

The Memory Address Space transforms `MemoryNode` from a flat, tag/semantic-only store into a filesystem-like, path-addressable namespace. Every node can be located by a deterministic hierarchical path in addition to its UUID.

> **Verified against source 2026-08-13** (DOCS-STALE-1).
>
> **The API inventory is sound.** Every constant, all 16 path functions and all 6 DAO methods
> exist with the documented names and defaults — §§1–6 need no correction, and the four database
> columns in §5 are present exactly as described.
>
> **Everything describing *behaviour* had drifted.** §7 documents a helper with no callers; §8's
> write-path rules get the fallback namespace wrong (`general`, not `_legacy`); §9's endpoints
> moved file, and two of the three return **different response keys** than documented, with two
> wrong default limits and a wrong depth cap; §10 mis-states three of five syscall handler
> mappings and omits two syscalls entirely.
>
> All corrected in place and marked. **If you integrated against §8, §9 or §10 as written,
> re-read them** — those are the sections a caller would have coded against.

---

## 1. Path Structure

```
/memory/{tenant_id}/{namespace}/{addr_type}/{node_id}
```

| Segment | Description | Example |
|---------|-------------|---------|
| `/memory` | MAS root — always present | `/memory` |
| `{tenant_id}` | Authenticated user or agent identifier | `user-abc123` |
| `{namespace}` | Logical grouping within the tenant | `auth`, `decisions`, `_legacy` |
| `{addr_type}` | Classification within the namespace | `insight`, `outcome`, `decision` |
| `{node_id}` | UUID of the node | `4f9a...` |

Examples:
```
/memory/user-abc/auth/decision/4f9a...
/memory/user-abc/decisions/outcome/7c3b...
/memory/user-abc/_legacy/insight/1a2b...   ← auto-derived for old nodes
```

Constants (in `memory/memory_address_space.py`):
- `MAS_ROOT = "/memory"`
- `LEGACY_NAMESPACE = "_legacy"`
- `MAX_PATH_DEPTH = 6`

---

## 2. Wildcard Patterns

Two wildcard forms are supported for bulk access:

| Pattern | Meaning | Example |
|---------|---------|---------|
| `path/*` | One level (direct children only) | `/memory/user-abc/auth/*` |
| `path/**` | Recursive (all descendants) | `/memory/user-abc/**` |

Classifier helpers:
```python
is_exact(path)      # True if no wildcards
is_wildcard(path)   # True if ends with /*
is_recursive(path)  # True if ends with /**
wildcard_prefix(path)  # returns prefix without /* or /**
```

---

## 3. Path Functions

All defined in `memory/memory_address_space.py`.

### Building Paths

```python
build_path(tenant_id, namespace=None, addr_type=None, node_id=None) -> str
# /memory/user-abc
# /memory/user-abc/auth
# /memory/user-abc/auth/decision
# /memory/user-abc/auth/decision/4f9a...

generate_node_path(tenant_id, namespace, addr_type) -> (full_path, node_id)
# Generates a new UUID and returns the full path + node_id
```

### Parsing Paths

```python
parse_path("/memory/user-abc/auth/decision/4f9a...") -> {
    "tenant_id": "user-abc",
    "namespace": "auth",
    "addr_type": "decision",
    "node_id": "4f9a..."
}
```

### Normalization and Validation

```python
normalize_path(path)  # collapse //, enforce /memory/ prefix, strip trailing /
validate_tenant_path(path, tenant_id)  # raises PermissionError on cross-tenant access
parent_path_of(path)  # /memory/user-abc/auth/decision/4f9a... → /memory/user-abc/auth/decision
```

---

## 4. Legacy Compatibility

Nodes created before MAS (no `path` column) are never backfilled. Instead, a stable derived path is computed on-the-fly:

```python
derive_legacy_path(node_dict) -> "/memory/{user_id}/_legacy/{memory_type}/{node_id}"
```

`enrich_node_with_path(node_dict)` adds the derived path to any node dict that is missing one. This means all nodes, old and new, present a consistent path interface to callers.

---

## 5. Database Columns

Added to `MemoryNodeModel` in `AINDY/memory/memory_persistence.py`. All four are present
in the model as documented (verified 2026-08-13). There is **no migration** — the table is
create_all-managed through the schema contract:

| Column | Type | Description |
|--------|------|-------------|
| `path` | `String(512)`, nullable, indexed | Full MAS path |
| `namespace` | `String(128)`, nullable, indexed | Namespace segment |
| `addr_type` | `String(128)`, nullable, indexed | Type segment (Python-safe name for `type`) |
| `parent_path` | `String(512)`, nullable, indexed | Parent path for tree queries |

Note: `addr_type` is used instead of `type` to avoid Python keyword collision.

---

## 6. DAO Path Methods

All added to `MemoryNodeDAO` in `db/dao/memory_node_dao.py`.

```python
# Write to an explicit path
dao.save_at_path(path, content, user_id, tags=None, node_type=None, ...) -> dict

# Exact lookup
dao.get_by_path(path, user_id=None) -> Optional[dict]

# One level (direct children of parent_path)
dao.list_path(parent_path, user_id=None, limit=100) -> List[dict]

# Recursive (LIKE prefix/%)
dao.walk_path(prefix, user_id=None, limit=200) -> List[dict]

# Hybrid dispatcher — exact / one-level / recursive based on path expression
dao.query_path(path_expr=None, query=None, tags=None, user_id=None, limit=20) -> List[dict]

# Causal chain — follows source_event_id links up to `depth` hops
dao.causal_trace(path, depth=5, user_id=None) -> List[dict]
```

`query_path` dispatches based on the path pattern:
- Exact path → `get_by_path`
- Ends with `/*` → `list_path`
- Ends with `/**` → `walk_path`
- No path → falls back to tag/text query

---

## 7. Tree Operations

```python
from memory.memory_address_space import build_tree, flatten_tree

tree = build_tree(nodes)   # {path → {"node": {...}, "children": [...]}}
flat = flatten_tree(tree)  # depth-first ordered list
```

`build_tree` assembles a nested tree from a flat list of node dicts. Each node's `parent_path` determines its position.

> **`flatten_tree` has zero callers** — verified 2026-08-13, nothing in `AINDY/` invokes it. It is
> defined and exported but unused. The `GET /platform/memory/tree` endpoint does *not* call it,
> which is why §9's promised `flat` response key does not exist.

---

## 8. Write Path Integration

When writing via `sys.v1.memory.write`, the path is extracted from the payload or auto-generated:

```python
path_from_write_payload(payload, tenant_id) -> (full_path, namespace, addr_type)
```

Rules (in priority order), **corrected 2026-08-13** against
`memory_address_space.py:262`:

1. If `payload["path"]` is set → `normalize_path` + `validate_tenant_path` (raises on a
   cross-tenant path), then `parse_path`.
   - If the parsed path **already carries a `node_id`** → used as-is.
   - If it does **not** → a new node_id is generated under the parsed namespace/addr_type.
     *The old rule 1 said "use it directly", which is only half true — a path without a node_id
     is a prefix, and the write lands at a freshly generated child of it.*
2. Otherwise `namespace = payload["namespace"] or "general"` and
   `addr_type = payload["addr_type"] or node_type`, then `generate_node_path`.

`node_type` itself defaults to **`"general"`**, not `"node"`.

> **The old rule 4 was wrong in a way worth calling out.** It said the fallback namespace is
> **`_legacy`**. It is **`general`**. `LEGACY_NAMESPACE` is used only by `derive_legacy_path`
> (§4) to synthesise a read-side path for pre-MAS rows — nothing ever *writes* into `_legacy`.
> Anyone who read this section and went looking for un-namespaced writes under
> `/memory/{tenant}/_legacy/**` would find nothing; they are under `/memory/{tenant}/general/**`.

There is no separate rule for "only namespace set" — rule 2 covers it, because each field falls
back independently.

---

## 9. API Endpoints

> **Corrected 2026-08-13.** This section said *"All in `routes/platform_router.py`"*. They are
> in **`AINDY/routes/platform/platform_ops_router.py`** (`:117`, `:137`, `:157`), registered under
> the `/platform` prefix via `PLATFORM_ROUTERS`. Response shapes, defaults and the depth cap were
> also wrong — every correction below is against that file.

All three are in `AINDY/routes/platform/platform_ops_router.py`, served under prefix `/platform`.

**Shared by all three**, and previously undocumented:
- rate limited **60/minute**
- require the `Scopes.MEMORY_READ` API-key scope (`enforce_api_key_scope`)
- the supplied `path` is put through `normalize_path` then `validate_tenant_path`, so a
  cross-tenant path raises rather than returning another tenant's nodes

### `GET /platform/memory` — `list_memory_path`

Hybrid list — supports path expressions, tag filtering, and text search. Delegates to
`MemoryNodeDAO.query_path`.

Query params:
- `path` — **required** (was documented as optional; it has no default)
- `query` — free-text search
- `tags` — comma-separated tag filter
- `limit` — max results, default **50** *(was documented as 20)*

Response: `{ "nodes": [...], "count": int, "path": str }` ✔ as documented

### `GET /platform/memory/tree` — `memory_tree`

Hierarchical tree from a path prefix. Exact paths are walked directly; wildcards go through
`wildcard_prefix`.

Query params:
- `path` — required; prefix to walk
- `limit` — max nodes, default **200** *(was documented as 100)*

Response: `{ "tree": {...}, "node_count": int, "path": str }`

> *Three of the four documented keys were wrong.* The old text promised
> `{ "tree", "flat", "count", "root" }`. There is **no `flat`** — `flatten_tree` exists in the
> module (§7) but this endpoint does not call it; `count` is **`node_count`**; `root` is **`path`**.

### `GET /platform/memory/trace` — `memory_trace`

Causal chain — follows `source_event_id` links backward from an exact node path.

Query params:
- `path` — required; exact path to a single node
- `depth` — hops to follow, default 5, capped at **20** via `min(depth, 20)` *(was documented as
  max 10)*

Response: `{ "chain": [...], "depth": int, "path": str }`

> *Two of three keys were wrong:* `count` is **`depth`** (the realised chain length, not the
> requested one) and `root_path` is **`path`**. Undocumented: an empty chain returns **404**
> `{"error": "No node found at path"}`, not an empty list.

---

## 10. Syscall Integration

MAS path methods are exposed as syscalls. All carry capability **`memory.read`** except
`write` (`memory.write`) and `delete` (a dedicated **`memory.delete`** capability that
`memory.write` does **not** grant — see MEM-DELETE-1).

| Syscall | Actually calls | Capability | `stable=` | Notes |
|---|---|---|---|---|
| `sys.v1.memory.read` | `query_path` **only when `path` is supplied**, else `dao.recall` | `memory.read` | ✔ | *Corrected:* the fallback to tag/semantic recall was undocumented |
| `sys.v1.memory.write` | `path_from_write_payload` → **`dao.save`** | `memory.write` | ✔ | *Corrected:* **not** `save_at_path`. That DAO method exists (§6) but no syscall uses it |
| `sys.v1.memory.list` | **`query_path`** | `memory.read` | ✘ | *Corrected:* **not** `list_path`, despite the name |
| `sys.v1.memory.tree` | `build_tree(walk_path(...))` | `memory.read` | ✘ | ✔ as documented |
| `sys.v1.memory.trace` | `causal_trace` | `memory.read` | ✘ | ✔ as documented |
| `sys.v1.memory.search` | semantic search | `memory.read` | — | **Missing from this table** |
| `sys.v1.memory.delete` | hard delete, cascade | **`memory.delete`** | — | **Missing** — shipped 2026-07-11, irreversible, tenant-scoped |

### Stability: the two MAS syscalls disagree with themselves

`sys.v1.memory.tree` and `sys.v1.memory.trace` are registered **`stable=False`**
(`syscall_registry.py:1487`, `:1501`), so `GET /platform/syscalls` advertises them as
experimental. Both are also in **`_STABLE_SYSCALLS`** in
`tests/unit/test_cross_repo_compatibility.py`, the CI-enforced cross-repo contract where
renaming or removing an entry is a **MAJOR** version bump.

Two sources of truth, opposite answers, on exactly the two syscalls this document exists to
describe. `sys.v1.memory.list` is consistent — `stable=False` and absent from the contract.

Recorded, not resolved: reconciling them is a code change, and which direction is correct is a
product decision about what has actually been promised to consumers. Part of the wider
stable-flag conflict noted in `PUBLIC_RUNTIME_SURFACES.md` and `SYSCALL_REFERENCE.md`.

---

## 11. Key Files

| File | Role |
|------|------|
| `AINDY/memory/memory_address_space.py` | All path utilities: normalize, parse, build, generate, derive_legacy, wildcard helpers, tree ops. **16 functions + 3 constants, all verified present 2026-08-13** |
| `AINDY/memory/memory_persistence.py` | `MemoryNodeModel` with 4 path columns — verified present |
| `AINDY/db/dao/memory_node_dao.py` | 6 path DAO methods (`:1591`–`:1724`) — all verified present with the documented signatures |
| *(no migration)* | The four path columns are **create_all-managed via the schema contract**, not Alembic-tracked — `memory_nodes` is deliberately absent from `env.py`'s `_RUNTIME_TABLES` allowlist. *Corrected 2026-08-13: this row cited `alembic/versions/g5h6i7j8k9l0_...py`, which never existed.* |
| `AINDY/routes/platform/platform_ops_router.py` | 3 MAS API endpoints. *Corrected 2026-08-13: this row said `routes/platform_router.py`.* |
| `AINDY/kernel/syscall_registry.py` | 5 MAS-aware syscall handlers + registrations |
| *(none)* | No MAS test suite exists. *Corrected 2026-08-13 — the row previously claimed `tests/unit/test_memory_address_space.py`, 61 tests.* |
