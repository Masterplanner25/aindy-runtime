"""`COST-GOVERNOR-1`, the meter half — token usage is observed instead of discarded.

The runtime enforced a 300-second wall-clock ceiling and a 256 MiB memory ceiling on execution
units whose dominant cost is **tokens**, which it did not measure at all. Four quota dimensions
exist and none of them is the one that matters for an LLM runtime.

★★ And the quantity was not merely uncapped — it was **thrown away at the boundary**. Every
provider client did `return str(response.choices[0].message.content or "")`, so the usage object
lived for one stack frame. Nothing downstream could have metered spend; there was nothing left.

This is the meter, not the governor. Nothing here refuses a call.

★ WHAT THESE TESTS ARE ACTUALLY FOR
------------------------------------
A meter is only useful if a reader can trust what its silence means. So the tests are shaped
around the three states an operator has to be able to tell apart:

1. **Tokens were used** → `aindy_llm_tokens_total` moves.
2. **No call was made** → nothing moves.
3. **A call was made and its usage could not be read** → `aindy_llm_usage_unreadable_total`
   moves while the token count stays flat.

Collapsing (2) and (3) is the failure that matters. A flat token count would then mean either
"nothing happened" or "everything happened and we saw none of it", and a meter that cannot
distinguish those is not evidence of anything — `CLAUDE.md`'s soak-harness rule, applied to
accounting instead of to a test.

The other property under test is that **metering can never fail a call that already succeeded.**
The tokens are spent either way; turning an accounting problem into a user-visible error would be
strictly worse than a gap in a graph.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.runtime_only


class _Usage:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class _Response:
    def __init__(self, usage=None):
        if usage is not None:
            self.usage = usage


def _read(name: str, labels: dict) -> float:
    from AINDY.platform_layer.metrics import REGISTRY

    value = REGISTRY.get_sample_value(name, labels)
    return float(value or 0.0)


# ── extraction: the two provider shapes, and the honest None ─────────────────


def test_openai_shaped_usage_is_read():
    from AINDY.platform_layer.token_meter import extract_token_usage

    r = _Response(_Usage(prompt_tokens=120, completion_tokens=45, total_tokens=165))
    assert extract_token_usage(r) == (120, 45)


def test_anthropic_shaped_usage_is_read():
    """Different field names for the same quantity — `input_tokens` / `output_tokens`."""
    from AINDY.platform_layer.token_meter import extract_token_usage

    r = _Response(_Usage(input_tokens=300, output_tokens=90))
    assert extract_token_usage(r) == (300, 90)


def test_a_response_with_no_usage_returns_none():
    """★ `None` is an answer, not an error.

    A stubbed client in a test, or a provider omitting usage on a streamed response, is not a
    malfunction — and it must be distinguishable from a zero-token call, which is why this
    returns `None` rather than `(0, 0)`.
    """
    from AINDY.platform_layer.token_meter import extract_token_usage

    assert extract_token_usage(_Response()) is None
    assert extract_token_usage(_Response(_Usage(something_else=1))) is None
    assert extract_token_usage(object()) is None


def test_a_partial_usage_object_still_reports_what_it_has():
    """Half an answer beats none. A provider reporting only prompt tokens is metered for those."""
    from AINDY.platform_layer.token_meter import extract_token_usage

    assert extract_token_usage(_Response(_Usage(prompt_tokens=10))) == (10, 0)


# ── the three states an operator must be able to tell apart ──────────────────


def test_usage_is_recorded_against_provider_and_model():
    """State 1: tokens were used, and the counters move by the right amounts."""
    from AINDY.platform_layer.token_meter import observe_llm_usage

    labels_p = {"provider": "openai", "model": "gpt-probe", "kind": "prompt"}
    labels_c = {"provider": "openai", "model": "gpt-probe", "kind": "completion"}
    before_p, before_c = _read("aindy_llm_tokens_total", labels_p), _read("aindy_llm_tokens_total", labels_c)

    observe_llm_usage(
        provider="openai",
        model="gpt-probe",
        response=_Response(_Usage(prompt_tokens=7, completion_tokens=3)),
    )

    assert _read("aindy_llm_tokens_total", labels_p) == before_p + 7
    assert _read("aindy_llm_tokens_total", labels_c) == before_c + 3


def test_an_unreadable_response_is_counted_not_swallowed():
    """★★ STATE 3, AND THE POINT OF THE WHOLE FILE.

    Without this counter, a flat `aindy_llm_tokens_total` means either "no calls happened" or
    "every call was made and none of its usage could be read". Those demand opposite responses
    from an operator, and a meter that cannot separate them is not evidence of anything.
    """
    from AINDY.platform_layer.token_meter import observe_llm_usage

    labels = {"provider": "openai", "model": "unreadable-probe"}
    tokens = {"provider": "openai", "model": "unreadable-probe", "kind": "prompt"}
    before_unreadable = _read("aindy_llm_usage_unreadable_total", labels)
    before_tokens = _read("aindy_llm_tokens_total", tokens)

    observe_llm_usage(provider="openai", model="unreadable-probe", response=_Response())

    assert _read("aindy_llm_usage_unreadable_total", labels) == before_unreadable + 1, (
        "an unreadable response was silently ignored — the operator cannot tell it apart from "
        "no call having been made"
    )
    assert _read("aindy_llm_tokens_total", tokens) == before_tokens, (
        "an unreadable response must not be recorded as zero tokens; that is a fabricated "
        "measurement, and it is the one thing worse than a gap"
    )


def test_metering_never_raises_on_a_hostile_response():
    """★ A call that already succeeded must not be failed by accounting.

    The tokens are spent either way. Turning a metering problem into a user-visible error would
    be strictly worse than a gap in a graph — so a response that explodes on attribute access is
    absorbed, and counted as unreadable.
    """
    from AINDY.platform_layer.token_meter import observe_llm_usage

    class _Hostile:
        @property
        def usage(self):
            raise RuntimeError("provider SDK exploded")

    labels = {"provider": "anthropic", "model": "hostile-probe"}
    before = _read("aindy_llm_usage_unreadable_total", labels)

    observe_llm_usage(provider="anthropic", model="hostile-probe", response=_Hostile())

    assert _read("aindy_llm_usage_unreadable_total", labels) == before + 1


# ── the wiring: every provider client must actually call it ──────────────────


def test_every_provider_client_meters_its_response():
    """★ The seam is the point — the usage object exists there and was discarded one line later.

    Checked over the AST rather than by string match: a comment mentioning the call must not
    satisfy this, which is a standing rule in `CLAUDE.md` after four source-text assertions in
    one fortnight proved weaker than they looked.
    """
    import ast
    from pathlib import Path

    clients = {
        "AINDY/platform_layer/openai_client.py",
        "AINDY/platform_layer/azure_openai_client.py",
        "AINDY/platform_layer/anthropic_client.py",
    }
    for rel in clients:
        tree = ast.parse(Path(rel).read_text(encoding="utf-8"))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "observe_llm_usage" in called, (
            f"{rel} does not call observe_llm_usage. Its response carries the usage object and "
            f"the next line discards it — an unmetered provider is invisible spend."
        )


def test_a_chat_call_is_metered_exactly_once(monkeypatch):
    """★★ DOUBLE-COUNTING IS WORSE THAN NOT COUNTING, and I walked into it writing this.

    `chat()` delegates to the raw response method — `chat_completion_response` /
    `messages_create`. Metering the raw path (needed, because a caller wanting tool blocks can
    only use that one) while *also* metering inside `chat()` counts every chat call twice.

    A gap in a graph is a known unknown. A number that is silently 2× is a **fabricated
    measurement**, and it is the failure the meter's own design rejects outright — the same
    reason an unreadable response is counted separately instead of recorded as zero tokens. A
    governor reserving against a doubled meter would refuse calls that were within budget.
    """
    from AINDY.platform_layer.openai_client import OpenAILLMClient

    labels = {"provider": "openai", "model": "once-probe", "kind": "prompt"}
    before = _read("aindy_llm_tokens_total", labels)

    client = OpenAILLMClient.__new__(OpenAILLMClient)  # no API key needed; the SDK call is stubbed

    class _Msg:
        content = "ok"

    class _Choice:
        message = _Msg()

    class _ChatResponse(_Response):
        choices = [_Choice()]

    class _Completions:
        def create(self, **kw):
            # A response shaped enough for BOTH halves: usage for the meter, choices for the
            # text extraction chat() performs afterwards. A stub carrying only usage would fail
            # in extraction and never reach the assertion.
            return _ChatResponse(_Usage(prompt_tokens=5, completion_tokens=1))

    class _Chat:
        completions = _Completions()

    class _SDK:
        chat = _Chat()

    object.__setattr__(client, "_client", _SDK())
    object.__setattr__(client, "_chat_timeout", 30.0)

    client.chat(model="once-probe", messages=[{"role": "user", "content": "hi"}])

    assert _read("aindy_llm_tokens_total", labels) == before + 5, (
        "a single chat() call did not record exactly its prompt tokens. +10 means both chat() "
        "and the raw path it delegates to are metering — every chat call counted twice."
    )


def test_only_the_raw_path_carries_the_meter():
    """★ Structural companion to the test above, so the reason survives a refactor.

    Each client must call `observe_llm_usage` exactly once. Two call sites in one client is the
    double-count; zero is an unmetered provider.
    """
    import ast
    from pathlib import Path

    for rel in (
        "AINDY/platform_layer/openai_client.py",
        "AINDY/platform_layer/azure_openai_client.py",
        "AINDY/platform_layer/anthropic_client.py",
    ):
        tree = ast.parse(Path(rel).read_text(encoding="utf-8"))
        calls = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "observe_llm_usage"
        ]
        assert len(calls) == 1, (
            f"{rel} has {len(calls)} observe_llm_usage call sites, expected exactly 1. "
            f"Two means chat() and the raw path it delegates to both meter, doubling every "
            f"chat call; zero means the provider is unmetered."
        )


def test_the_labels_deliberately_exclude_tenant():
    """★ Recorded so the omission is not 'fixed' by someone reading it as an oversight.

    Tenant is the more useful partition for a governor and is left out on purpose: a Prometheus
    label is a time series per distinct value, so a tenant label grows cardinality with the
    customer list. Per-tenant accounting belongs in the counter the governor checks — a cache,
    keyed and expiring — not in the observability surface.
    """
    from AINDY.platform_layer.metrics import llm_tokens_total

    assert tuple(llm_tokens_total._labelnames) == ("provider", "model", "kind"), (
        "the token metric's labels changed. Adding tenant here makes cardinality grow with the "
        "customer list; that partition belongs in the governor's counter, not in Prometheus."
    )
