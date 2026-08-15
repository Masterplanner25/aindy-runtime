"""Behavioural suite for the Memory Address Space (MAS).

Closes the MAS half of DOCS-COVERAGE-CLAIM-1: `MEMORY_ADDRESS_SPACE.md` cited
`tests/unit/test_memory_address_space.py` — this path — which had never existed.

MAS is pure functions over path strings, so everything here runs without a DB.

Three behaviours below are asserted as they *currently* are, with the surprise
called out rather than smoothed over:

* `build_tree` does not nest for real MAS data (see TestBuildTree).
* `flatten_tree` used to drop a node that is the parent of another node
  (MAS-FLATTEN-1, fixed 2026-08-15; see TestFlattenTree).
* `MAX_PATH_DEPTH` and `_SAFE_SEGMENT` are declared but never applied.

Marked `runtime_only` deliberately — without it CI collects nothing here
(see CI-MARKER-1).
"""
from __future__ import annotations

import uuid

import pytest

from AINDY.memory import memory_address_space as mas
from AINDY.memory.memory_address_space import (
    LEGACY_NAMESPACE,
    MAS_ROOT,
    build_path,
    build_tree,
    derive_legacy_path,
    enrich_node_with_path,
    flatten_tree,
    generate_node_path,
    is_exact,
    is_recursive,
    is_wildcard,
    normalize_path,
    parent_path_of,
    parse_path,
    path_from_write_payload,
    validate_tenant_path,
    wildcard_prefix,
)

pytestmark = pytest.mark.runtime_only


# ── normalize_path ────────────────────────────────────────────────────────────


class TestNormalizePath:
    def test_empty_path_rejected(self):
        with pytest.raises(ValueError, match="must not be empty"):
            normalize_path("")

    @pytest.mark.parametrize("bad", ["/other/t1/ns", "memory/t1/ns", "/mem/t1", "  "])
    def test_path_outside_mas_root_rejected(self, bad):
        with pytest.raises(ValueError, match="must start with"):
            normalize_path(bad)

    def test_trailing_slash_stripped(self):
        assert normalize_path("/memory/t1/entities/") == "/memory/t1/entities"

    def test_root_itself_survives_normalization(self):
        assert normalize_path(MAS_ROOT) == MAS_ROOT
        # the rstrip guard must not eat the root's own slash
        assert normalize_path("/memory/") == MAS_ROOT

    def test_consecutive_slashes_collapsed(self):
        assert normalize_path("/memory//t1///entities") == "/memory/t1/entities"

    def test_surrounding_whitespace_stripped(self):
        assert normalize_path("  /memory/t1/entities  ") == "/memory/t1/entities"

    @pytest.mark.parametrize(
        "path",
        ["/memory/t1/entities/*", "/memory/t1/entities/**", "/memory/t1/*/updated"],
    )
    def test_wildcards_preserved(self, path):
        assert normalize_path(path) == path

    def test_normalization_is_idempotent(self):
        once = normalize_path("/memory//t1/entities/")
        assert normalize_path(once) == once


# ── validate_tenant_path ──────────────────────────────────────────────────────


class TestValidateTenantPath:
    def test_exact_tenant_root_allowed(self):
        validate_tenant_path("/memory/t1", "t1")  # must not raise

    def test_path_under_tenant_allowed(self):
        validate_tenant_path("/memory/t1/entities/updated/n1", "t1")

    def test_other_tenant_rejected(self):
        with pytest.raises(PermissionError, match="TENANT_VIOLATION"):
            validate_tenant_path("/memory/t2/entities", "t1")

    def test_sibling_tenant_sharing_a_prefix_is_rejected(self):
        """`t1` must not authorize `t12` — the guard's trailing slash is load-bearing."""
        with pytest.raises(PermissionError):
            validate_tenant_path("/memory/t12/entities", "t1")

    def test_wildcard_in_tenant_position_rejected(self):
        with pytest.raises(PermissionError):
            validate_tenant_path("/memory/*/entities", "t1")

    def test_bare_root_rejected_for_a_tenant(self):
        with pytest.raises(PermissionError):
            validate_tenant_path(MAS_ROOT, "t1")

    def test_dotdot_is_a_literal_segment_not_traversal(self):
        """MAS does not resolve `..`; it is an ordinary segment.

        The guard therefore accepts this path (it *is* under the tenant prefix as a
        string), and `parse_path` still reports the real tenant. Recorded so the
        absence of traversal resolution is a known property, not an assumption.
        """
        path = "/memory/t1/../t2/entities/n1"
        validate_tenant_path(path, "t1")  # does not raise
        assert parse_path(path)["tenant_id"] == "t1"
        assert parse_path(path)["namespace"] == ".."


# ── parse_path ────────────────────────────────────────────────────────────────


class TestParsePath:
    def test_full_path_decomposed(self):
        parsed = parse_path("/memory/t1/entities/updated/node-9")
        assert parsed == {
            "tenant_id": "t1",
            "namespace": "entities",
            "addr_type": "updated",
            "node_id": "node-9",
        }

    def test_partial_paths_leave_trailing_components_none(self):
        assert parse_path("/memory/t1")["namespace"] is None
        assert parse_path("/memory/t1/entities")["addr_type"] is None
        assert parse_path("/memory/t1/entities/updated")["node_id"] is None

    def test_root_yields_all_none(self):
        assert set(parse_path(MAS_ROOT).values()) == {None}

    def test_wildcard_segments_returned_verbatim(self):
        assert parse_path("/memory/t1/entities/*")["addr_type"] == "*"
        assert parse_path("/memory/t1/entities/**")["addr_type"] == "**"

    def test_segments_beyond_node_id_are_ignored(self):
        """Deeper-than-canonical paths do not error; the extra tail is dropped."""
        parsed = parse_path("/memory/t1/entities/updated/n1/extra/more")
        assert parsed["node_id"] == "n1"

    def test_parse_round_trips_with_build(self):
        path = build_path("t1", "entities", "updated", "n1")
        assert parse_path(path) == {
            "tenant_id": "t1",
            "namespace": "entities",
            "addr_type": "updated",
            "node_id": "n1",
        }


# ── build_path ────────────────────────────────────────────────────────────────


class TestBuildPath:
    def test_tenant_only(self):
        assert build_path("t1") == "/memory/t1"

    def test_full_path(self):
        assert build_path("t1", "entities", "updated", "n1") == "/memory/t1/entities/updated/n1"

    @pytest.mark.parametrize("empty", ["", None])
    def test_tenant_id_required(self, empty):
        with pytest.raises(ValueError, match="tenant_id is required"):
            build_path(empty)

    def test_addr_type_requires_namespace(self):
        with pytest.raises(ValueError, match="addr_type requires namespace"):
            build_path("t1", None, "updated")

    def test_node_id_requires_namespace_and_addr_type(self):
        with pytest.raises(ValueError, match="node_id requires namespace and addr_type"):
            build_path("t1", "entities", None, "n1")

    def test_node_id_is_stringified(self):
        node_id = uuid.uuid4()
        assert build_path("t1", "ns", "type", node_id).endswith(str(node_id))


# ── generate_node_path ────────────────────────────────────────────────────────


class TestGenerateNodePath:
    def test_returns_canonical_path_and_matching_id(self):
        path, node_id = generate_node_path("t1", "entities", "updated")
        assert path == f"/memory/t1/entities/updated/{node_id}"
        assert uuid.UUID(node_id)  # must be a real UUID

    def test_successive_calls_are_unique(self):
        first, _ = generate_node_path("t1", "entities", "updated")
        second, _ = generate_node_path("t1", "entities", "updated")
        assert first != second

    def test_generated_path_parses_back(self):
        path, node_id = generate_node_path("t1", "entities", "updated")
        assert parse_path(path)["node_id"] == node_id


# ── derive_legacy_path ────────────────────────────────────────────────────────


class TestDeriveLegacyPath:
    def test_uses_legacy_namespace_and_memory_type(self):
        path = derive_legacy_path({"user_id": "u1", "memory_type": "failure", "id": "n1"})
        assert path == f"/memory/u1/{LEGACY_NAMESPACE}/failure/n1"

    def test_falls_back_to_node_type_when_memory_type_absent(self):
        path = derive_legacy_path({"user_id": "u1", "node_type": "decision", "id": "n1"})
        assert path == f"/memory/u1/{LEGACY_NAMESPACE}/decision/n1"

    def test_defaults_for_a_wholly_empty_node(self):
        path = derive_legacy_path({})
        parsed = parse_path(path)
        assert parsed["tenant_id"] == "unknown"
        assert parsed["namespace"] == LEGACY_NAMESPACE
        assert parsed["addr_type"] == "insight"
        assert uuid.UUID(parsed["node_id"])  # a fresh id is minted

    def test_is_stable_for_the_same_node(self):
        node = {"user_id": "u1", "memory_type": "insight", "id": "n1"}
        assert derive_legacy_path(node) == derive_legacy_path(node)


# ── parent_path_of ────────────────────────────────────────────────────────────


class TestParentPathOf:
    @pytest.mark.parametrize(
        "path,expected",
        [
            ("/memory/t1/ns/type/id", "/memory/t1/ns/type"),
            ("/memory/t1/ns/type", "/memory/t1/ns"),
            ("/memory/t1/ns", "/memory/t1"),
            ("/memory/t1", MAS_ROOT),
        ],
    )
    def test_strips_one_segment_per_call(self, path, expected):
        assert parent_path_of(path) == expected

    def test_root_is_a_fixed_point(self):
        assert parent_path_of(MAS_ROOT) == MAS_ROOT

    def test_walking_up_terminates_at_root(self):
        path = "/memory/t1/ns/type/id"
        for _ in range(10):
            path = parent_path_of(path)
        assert path == MAS_ROOT


# ── pattern classification ────────────────────────────────────────────────────


class TestPatternClassification:
    @pytest.mark.parametrize(
        "path,exact,wild,recursive",
        [
            ("/memory/t1/ns/type/id", True, False, False),
            ("/memory/t1/ns/*", False, True, False),
            ("/memory/t1/ns/**", False, False, True),
        ],
    )
    def test_classification_is_mutually_exclusive(self, path, exact, wild, recursive):
        assert is_exact(path) is exact
        assert is_wildcard(path) is wild
        assert is_recursive(path) is recursive

    def test_recursive_is_not_also_wildcard(self):
        """`/**` must not satisfy `is_wildcard` or callers double-expand it."""
        assert is_wildcard("/memory/t1/ns/**") is False

    def test_is_exact_detects_a_wildcard_anywhere_not_just_the_tail(self):
        assert is_exact("/memory/t1/*/type/id") is False

    def test_is_exact_does_not_normalize_so_it_tolerates_junk(self):
        """`is_exact` is a plain substring check — unlike its two siblings it
        never calls `normalize_path`, so it does not raise on a non-MAS path."""
        assert is_exact("not-a-mas-path") is True

    @pytest.mark.parametrize("checker", [is_wildcard, is_recursive])
    def test_siblings_do_normalize_and_therefore_reject_junk(self, checker):
        with pytest.raises(ValueError):
            checker("not-a-mas-path")


class TestWildcardPrefix:
    def test_single_level_wildcard(self):
        assert wildcard_prefix("/memory/t1/entities/*") == "/memory/t1/entities"

    def test_recursive_wildcard(self):
        assert wildcard_prefix("/memory/t1/entities/**") == "/memory/t1/entities"

    def test_both_wildcard_forms_yield_the_same_prefix(self):
        assert wildcard_prefix("/memory/t1/ns/*") == wildcard_prefix("/memory/t1/ns/**")

    def test_exact_path_falls_back_to_parent(self):
        assert wildcard_prefix("/memory/t1/entities/updated/n1") == "/memory/t1/entities/updated"


# ── path_from_write_payload ───────────────────────────────────────────────────


class TestPathFromWritePayload:
    def test_defaults_when_nothing_supplied(self):
        path, ns, addr = path_from_write_payload({}, "t1")
        assert (ns, addr) == ("general", "general")
        assert path.startswith("/memory/t1/general/general/")

    def test_node_type_becomes_addr_type(self):
        _, ns, addr = path_from_write_payload({"node_type": "failure"}, "t1")
        assert (ns, addr) == ("general", "failure")

    def test_explicit_namespace_and_addr_type_win(self):
        path, ns, addr = path_from_write_payload(
            {"namespace": "entities", "addr_type": "updated"}, "t1"
        )
        assert (ns, addr) == ("entities", "updated")
        assert path.startswith("/memory/t1/entities/updated/")

    def test_prebuilt_path_without_node_id_gets_one_generated(self):
        path, ns, addr = path_from_write_payload({"path": "/memory/t1/entities/updated"}, "t1")
        assert (ns, addr) == ("entities", "updated")
        assert uuid.UUID(parse_path(path)["node_id"])

    def test_prebuilt_path_with_node_id_is_preserved_verbatim(self):
        supplied = "/memory/t1/entities/updated/keep-me"
        path, _, _ = path_from_write_payload({"path": supplied}, "t1")
        assert path == supplied

    def test_cross_tenant_path_is_refused(self):
        with pytest.raises(PermissionError, match="TENANT_VIOLATION"):
            path_from_write_payload({"path": "/memory/attacker/entities/updated"}, "t1")

    def test_tenant_check_precedes_generation(self):
        """A cross-tenant write must raise, not silently rewrite into the caller's space."""
        with pytest.raises(PermissionError):
            path_from_write_payload({"path": "/memory/t2/ns/type/n1"}, "t1")

    def test_generated_path_always_lands_under_the_caller_tenant(self):
        for payload in ({}, {"namespace": "x"}, {"node_type": "failure"}):
            path, _, _ = path_from_write_payload(payload, "t1")
            validate_tenant_path(path, "t1")  # must not raise


# ── build_tree ────────────────────────────────────────────────────────────────


class TestBuildTree:
    def test_every_node_is_keyed_by_its_path(self):
        nodes = [
            {"id": "n1", "path": "/memory/t1/entities/updated/n1"},
            {"id": "n2", "path": "/memory/t1/executions/completed/n2"},
        ]
        tree = build_tree(nodes)
        assert set(tree) == {n["path"] for n in nodes}
        assert tree["/memory/t1/entities/updated/n1"]["node"]["id"] == "n1"

    def test_pathless_nodes_are_placed_at_their_derived_legacy_path(self):
        tree = build_tree([{"id": "n1", "user_id": "u1", "memory_type": "insight"}])
        assert f"/memory/u1/{LEGACY_NAMESPACE}/insight/n1" in tree

    def test_real_mas_data_produces_no_nesting(self):
        """The finding: for canonical MAS data every entry has empty `children`.

        MAS nodes are always 5-segment leaves, and a node's parent path
        (`/memory/t/ns/type`) is never itself a node — so the `parent in by_path`
        condition never fires and the result is a flat map, not a tree. This backs
        `sys.v1.memory.tree`, so the endpoint returns a flat map by construction.
        Asserted so a future change to real nesting is a visible, deliberate break.
        """
        nodes = [
            {"id": "n1", "path": "/memory/t1/entities/updated/n1"},
            {"id": "n2", "path": "/memory/t1/entities/updated/n2"},
            {"id": "n3", "path": "/memory/t1/executions/completed/n3"},
        ]
        tree = build_tree(nodes)
        assert all(entry["children"] == [] for entry in tree.values())

    def test_nesting_appears_only_when_a_node_path_is_a_prefix_of_another(self):
        nodes = [
            {"id": "parent", "path": "/memory/t1/entities/updated"},
            {"id": "child", "path": "/memory/t1/entities/updated/n1"},
        ]
        tree = build_tree(nodes)
        assert tree["/memory/t1/entities/updated"]["children"] == [
            "/memory/t1/entities/updated/n1"
        ]

    def test_empty_input_yields_empty_tree(self):
        assert build_tree([]) == {}

    def test_duplicate_paths_collapse_to_the_last_writer(self):
        nodes = [
            {"id": "first", "path": "/memory/t1/ns/type/dup"},
            {"id": "second", "path": "/memory/t1/ns/type/dup"},
        ]
        tree = build_tree(nodes)
        assert len(tree) == 1
        assert tree["/memory/t1/ns/type/dup"]["node"]["id"] == "second"


# ── flatten_tree ──────────────────────────────────────────────────────────────


class TestFlattenTree:
    """MAS-FLATTEN-1 — FIXED 2026-08-15.

    The root set was "every path, minus every path that is some node's parent", which
    removes the *parents* — so an intermediate node was never walked and vanished. A
    root is a node whose parent is not itself a node, the inverse of what was written.
    """

    def test_empty_tree(self):
        assert flatten_tree({}) == []

    def test_flat_data_round_trips_every_node(self):
        nodes = [
            {"id": "n1", "path": "/memory/t1/entities/updated/n1"},
            {"id": "n2", "path": "/memory/t1/entities/updated/n2"},
        ]
        flat = flatten_tree(build_tree(nodes))
        assert {n["id"] for n in flat} == {"n1", "n2"}

    def test_output_is_deterministically_ordered(self):
        nodes = [
            {"id": "b", "path": "/memory/t1/ns/type/b"},
            {"id": "a", "path": "/memory/t1/ns/type/a"},
        ]
        tree = build_tree(nodes)
        assert [n["id"] for n in flatten_tree(tree)] == ["a", "b"]

    def test_a_parent_node_is_not_dropped(self):
        """The regression itself. Was `xfail(strict=True)`; output was `['child']`."""
        nodes = [
            {"id": "parent", "path": "/memory/t1/entities/updated"},
            {"id": "child", "path": "/memory/t1/entities/updated/n1"},
        ]
        flat = flatten_tree(build_tree(nodes))
        assert {n["id"] for n in flat} == {"parent", "child"}

    def test_parent_precedes_its_child(self):
        """Depth-first, as the docstring and MEMORY_ADDRESS_SPACE.md §7 promise."""
        nodes = [
            {"id": "parent", "path": "/memory/t1/entities/updated"},
            {"id": "child", "path": "/memory/t1/entities/updated/n1"},
        ]
        assert [n["id"] for n in flatten_tree(build_tree(nodes))] == ["parent", "child"]

    def test_depth_first_across_three_levels(self):
        nodes = [
            {"id": "a", "path": "/memory/t/ns"},
            {"id": "b", "path": "/memory/t/ns/ty"},
            {"id": "c", "path": "/memory/t/ns/ty/id"},
        ]
        assert [n["id"] for n in flatten_tree(build_tree(nodes))] == ["a", "b", "c"]

    def test_every_node_appears_exactly_once(self):
        """Totality *and* no duplication — the walk is now visited-guarded, so a node
        reachable by more than one route cannot be emitted twice."""
        nodes = [
            {"id": "root", "path": "/memory/t/ns"},
            {"id": "mid", "path": "/memory/t/ns/ty"},
            {"id": "leaf1", "path": "/memory/t/ns/ty/a"},
            {"id": "leaf2", "path": "/memory/t/ns/ty/b"},
        ]
        ids = [n["id"] for n in flatten_tree(build_tree(nodes))]
        assert sorted(ids) == ["leaf1", "leaf2", "mid", "root"]
        assert len(ids) == len(set(ids))

    def test_nodes_unreachable_from_any_root_are_still_returned(self):
        """Totality guard against a hand-built tree.

        `build_tree` only records a `children` entry when the parent path is itself a
        node, so a partially-populated tree can hold nodes reachable from no root.
        Silently dropping them is the very failure this entry is about, so they are
        appended rather than lost.
        """
        tree = {
            "/memory/t/ns": {"node": {"id": "root"}, "children": []},
            "/memory/t/ns/ty": {"node": {"id": "orphaned"}, "children": []},
        }
        ids = [n["id"] for n in flatten_tree(tree)]
        assert sorted(ids) == ["orphaned", "root"]

    def test_multiple_independent_roots_are_all_walked(self):
        nodes = [
            {"id": "n1", "path": "/memory/t1/entities/updated/n1"},
            {"id": "n2", "path": "/memory/t1/executions/completed/n2"},
            {"id": "n3", "path": "/memory/t2/entities/updated/n3"},
        ]
        assert {n["id"] for n in flatten_tree(build_tree(nodes))} == {"n1", "n2", "n3"}

    def test_flatten_returns_the_same_node_count_as_the_tree(self):
        """The invariant that would have caught MAS-FLATTEN-1 in one line."""
        for paths in (
            ["/memory/t/ns/ty/a"],
            ["/memory/t/ns", "/memory/t/ns/ty"],
            ["/memory/t/ns", "/memory/t/ns/ty", "/memory/t/ns/ty/a", "/memory/t/ns/ty/b"],
        ):
            tree = build_tree([{"id": p, "path": p} for p in paths])
            assert len(flatten_tree(tree)) == len(tree)


# ── enrich_node_with_path ─────────────────────────────────────────────────────


class TestEnrichNodeWithPath:
    def test_existing_path_is_left_alone(self):
        node = {"id": "n1", "path": "/memory/t1/ns/type/n1"}
        assert enrich_node_with_path(node)["path"] == "/memory/t1/ns/type/n1"

    def test_missing_path_is_derived(self):
        node = {"id": "n1", "user_id": "u1", "memory_type": "failure"}
        assert enrich_node_with_path(node)["path"] == f"/memory/u1/{LEGACY_NAMESPACE}/failure/n1"

    def test_mutates_in_place_and_returns_the_same_object(self):
        node = {"id": "n1", "user_id": "u1"}
        assert enrich_node_with_path(node) is node
        assert "path" in node

    def test_empty_string_path_is_treated_as_missing(self):
        node = {"id": "n1", "user_id": "u1", "path": ""}
        assert enrich_node_with_path(node)["path"].startswith("/memory/u1/")


# ── declared-but-unenforced constraints ───────────────────────────────────────


class TestUnenforcedConstraints:
    """`MAX_PATH_DEPTH` and `_SAFE_SEGMENT` are defined and never referenced again.

    Both look like validation but neither is wired to anything, so paths that
    violate them are accepted. Pinned here so the gap is explicit: if either is
    ever enforced, these tests fail and say exactly what changed.
    """

    def test_max_path_depth_is_not_enforced(self):
        deep = "/memory/t1/ns/type/id/" + "/".join(f"s{i}" for i in range(20))
        assert normalize_path(deep) == deep.rstrip("/")

    def test_segment_charset_is_not_enforced(self):
        # spaces, quotes and percent signs all survive normalization
        assert normalize_path("/memory/t 1/n's/%ns") == "/memory/t 1/n's/%ns"

    def test_build_path_does_not_validate_segment_contents(self):
        assert build_path("t 1", "n s", "a b") == "/memory/t 1/n s/a b"

    def test_constants_exist_but_are_referenced_nowhere_else(self):
        import inspect
        import re

        source = inspect.getsource(mas)
        for name in ("MAX_PATH_DEPTH", "_SAFE_SEGMENT"):
            assert len(re.findall(rf"\b{name}\b", source)) == 1, (
                f"{name} is now referenced more than once — if it became enforced, "
                "the two tests above should be updated to assert the new behaviour."
            )
