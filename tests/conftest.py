import pytest

from signalbot.config import settings


@pytest.fixture(autouse=True)
def _no_quiet_hours(monkeypatch):
    """Les tests ne doivent pas dépendre de l'heure à laquelle ils tournent."""
    monkeypatch.setattr(settings, "quiet_hours", "")
    monkeypatch.setattr(settings, "zones_after_news", False)  # pas de graphiques (réseau) par défaut
    monkeypatch.setattr(settings, "zones_after_moves", False)
    monkeypatch.setattr(settings, "mt5_files_dir", "")
    monkeypatch.setattr(settings, "coach_alerts", False)
