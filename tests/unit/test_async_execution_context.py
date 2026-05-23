import pytest

from AINDY.platform_layer.async_execution_context import (
    activate_async_execution_context,
    deactivate_async_execution_context,
    is_async_execution_active,
)

pytestmark = pytest.mark.runtime_only


def test_public_functions_import_cleanly():
    assert callable(activate_async_execution_context)
    assert callable(deactivate_async_execution_context)
    assert callable(is_async_execution_active)


def test_async_execution_context_defaults_inactive():
    assert is_async_execution_active() is False


def test_activate_marks_context_active_until_token_reset():
    token = activate_async_execution_context()

    try:
        assert is_async_execution_active() is True
    finally:
        deactivate_async_execution_context(token)

    assert is_async_execution_active() is False


def test_deactivate_restores_prior_context_value():
    outer_token = activate_async_execution_context()

    try:
        inner_token = activate_async_execution_context()
        assert is_async_execution_active() is True

        deactivate_async_execution_context(inner_token)
        assert is_async_execution_active() is True
    finally:
        deactivate_async_execution_context(outer_token)

    assert is_async_execution_active() is False
