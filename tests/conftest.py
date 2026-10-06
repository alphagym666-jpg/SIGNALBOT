import pytest

from signalbot.config import settings


@pytest.fixture(autouse=True)
def _no_quiet_hours(monkeypatch):
    """Les tests ne doivent pas dépendre de l'heure à laquelle ils tournent."""
    monkeypatch.setattr(settings, "quiet_hours", "")
