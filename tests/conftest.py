import pytest

from src.services.assistant import limits


@pytest.fixture(autouse=True)
def reset_assistant_limits():
    limits.reset()
    yield
    limits.reset()
