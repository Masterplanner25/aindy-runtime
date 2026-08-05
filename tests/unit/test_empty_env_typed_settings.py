"""FR-10 — an empty environment variable must mean "unset", not "crash the process".

The idiomatic Compose default for an optional variable renders as an EMPTY STRING rather
than an absent one::

    AINDY_REQUIRE_VERIFIED_LOGIN: "${AINDY_REQUIRE_VERIFIED_LOGIN:-}"   # -> ""

To an operator that reads as "leave it off". To pydantic it is an unparseable bool, and
because ``settings = Settings()`` runs at module import, the process dies before it serves
anything — a crash loop rather than a config warning. This bit the live deployment twice
(``AINDY_NEXT_ACTION_ACTING``, then ``AINDY_REQUIRE_VERIFIED_LOGIN``, 27 restarts).

``env_ignore_empty=True`` on ``model_config`` is the guard. These tests pin both halves of
it: the bools it fixes, and the non-bool fields it must not disturb.
"""
import pytest

from AINDY.config import Settings


#: Bool settings the *test harness itself* needs in order to construct Settings at all —
#: they gate the sqlite URL check and the Mongo reachability check. Emptying them would be
#: testing the harness, not the guard, so the sweep below skips them by name.
_HARNESS_BOOLS = {"AINDY_ALLOW_SQLITE", "SKIP_MONGO_PING"}


def _build(monkeypatch, **env):
    """Construct a fresh Settings under the given environment.

    Baseline first, caller overrides second — the reverse order silently stomps any field
    a test is trying to empty.
    """
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("AINDY_ALLOW_SQLITE", "true")
    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    monkeypatch.setenv("SKIP_MONGO_PING", "1")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings()


# ---------------------------------------------------------------------------
# The failure that crash-looped the container
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "field",
    ["AINDY_REQUIRE_VERIFIED_LOGIN", "AINDY_NEXT_ACTION_ACTING"],
)
def test_empty_string_on_a_typed_bool_falls_back_to_the_default(monkeypatch, field):
    """`FOO=""` must resolve to the field default, not raise bool_parsing."""
    settings = _build(monkeypatch, **{field: ""})
    assert getattr(settings, field) is False


def test_every_typed_bool_survives_an_empty_value(monkeypatch):
    """Not just the two that bit us — the whole class.

    Sets *every* bool field on Settings to "" at once. Without env_ignore_empty this raises
    a ValidationError naming all of them; the point of the guard is that none of them can
    take the process down.
    """
    bool_fields = [
        name
        for name, info in Settings.model_fields.items()
        if info.annotation is bool and name not in _HARNESS_BOOLS
    ]
    assert len(bool_fields) > 20, f"expected the full bool surface, found {len(bool_fields)}"

    settings = _build(monkeypatch, **{name: "" for name in bool_fields})

    for name in bool_fields:
        value = getattr(settings, name)
        assert isinstance(value, bool), f"{name} resolved to {value!r}, not a bool"
        assert value is Settings.model_fields[name].default, (
            f"{name} resolved to {value!r} rather than its declared default"
        )


# ---------------------------------------------------------------------------
# What the guard must NOT disturb
# ---------------------------------------------------------------------------

def test_explicit_values_still_win(monkeypatch):
    """The guard only applies to empty values — a real setting is still honoured."""
    settings = _build(monkeypatch, AINDY_REQUIRE_VERIFIED_LOGIN="true")
    assert settings.AINDY_REQUIRE_VERIFIED_LOGIN is True


def test_empty_mongo_url_still_resolves_falsy(monkeypatch):
    """MONGO_URL is the one field where "" is deliberately set as a value.

    `runtime-ci.yml` sets `MONGO_URL: ""` to disable Mongo, and its default is None rather
    than "". env_ignore_empty flips which of those two the field lands on — both are falsy,
    every consumer tests `if not settings.MONGO_URL`, and `ensure_mongo_url` normalises via
    `(v or "").strip()`. This pins the observable contract so a future refactor to an
    `is None` check has to fail here first.
    """
    settings = _build(monkeypatch, MONGO_URL="")
    assert not settings.MONGO_URL


def test_empty_string_defaults_are_unaffected(monkeypatch):
    """Fields whose default is already "" resolve identically either way."""
    settings = _build(monkeypatch, LLM_FALLBACK_PROVIDERS="", AINDY_SMTP_USER="")
    assert settings.LLM_FALLBACK_PROVIDERS == ""
    assert settings.AINDY_SMTP_USER == ""
