"""FR-63: a user's refused key reaches a ``chat_completion`` caller as ``ProviderKeyRejected``.

``UserKeyLLMClient`` raised it, but ``chat_completion`` and ``chat_completion_deepseek`` re-raise
through ``_legacy_raise``, which unwraps any ``LLMCallError`` that has a cause. The caller got the
inner ``LLMCallError("... failed")`` back, with the two errors' causes pointing at each other.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.runtime_only


class _AuthError(Exception):
    status_code = 401


class _RefusingRaw:
    """A provider client whose every call is refused with a 401, wrapped as the clients wrap it."""

    def __init__(self):
        self.calls = 0

    def chat_completion_response(self, **kwargs):
        return self.call_method("chat_completion_response", **kwargs)

    def call_method(self, name, *args, **kwargs):
        from AINDY.platform_layer.llm_client import LLMCallError

        self.calls += 1
        try:
            raise _AuthError("invalid api key")
        except _AuthError as auth:
            raise LLMCallError("provider call failed") from auth

    def chat(self, messages, model=None, temperature=0.7, max_tokens=None):
        return self.call_method("chat", messages)

    def is_available(self):
        return True


def _user_client(provider):
    from AINDY.platform_layer.llm_client import UserKeyLLMClient

    raw = _RefusingRaw()
    return UserKeyLLMClient(raw, provider=provider, fingerprint="fp-test", key_hint="zz9Q"), raw


@pytest.mark.parametrize(
    "provider, helper",
    [
        ("openai", "AINDY.platform_layer.openai_client:chat_completion"),
        ("deepseek", "AINDY.platform_layer.deepseek_client:chat_completion_deepseek"),
    ],
)
def test_a_refused_user_key_comes_out_of_the_helper_as_itself(provider, helper):
    import importlib

    from AINDY.platform_layer.llm_client import LLMCallError
    from AINDY.platform_layer.provider_keys import ProviderKeyRejected

    module, name = helper.split(":")
    call = getattr(importlib.import_module(module), name)
    client, raw = _user_client(provider)

    with pytest.raises(LLMCallError) as caught:
        call(client, model="m", messages=[{"role": "user", "content": "hi"}])

    assert isinstance(caught.value, ProviderKeyRejected), type(caught.value)
    assert caught.value.key_hint == "zz9Q"
    assert raw.calls == 1, "a refused key is never retried"
    # no cycle: walking the causes ends
    seen, current = 0, caught.value
    while current is not None and seen < 10:
        current, seen = current.__cause__, seen + 1
    assert current is None, "the cause chain loops"


def test_control_another_call_error_is_still_unwrapped_as_before():
    """The legacy unwrap stays for everything else: callers catch the provider's own exception."""
    from AINDY.platform_layer.llm_client import LLMCallError
    from AINDY.platform_layer.openai_client import _legacy_raise

    class _Timeout(Exception):
        pass

    try:
        raise _Timeout("slow")
    except _Timeout as inner:
        wrapped = LLMCallError("openai call failed")
        wrapped.__cause__ = inner
    with pytest.raises(_Timeout):
        _legacy_raise(wrapped)


@pytest.mark.parametrize("module", ["AINDY.platform_layer.openai_client", "AINDY.platform_layer.deepseek_client"])
def test_a_missing_user_key_passes_through_too(module):
    import importlib

    from AINDY.platform_layer.provider_keys import ProviderKeyRequired

    with pytest.raises(ProviderKeyRequired):
        importlib.import_module(module)._legacy_raise(ProviderKeyRequired("openai"))
