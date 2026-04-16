"""Shared test fixtures."""

import pytest

from op_aromic.config.settings import AutomicSettings


@pytest.fixture
def settings() -> AutomicSettings:
    return AutomicSettings(
        url="http://localhost:8080/ae/api/v1",
        client_id=100,
        user="TEST/USER",
        department="TEST",
        password="test-password",
    )
