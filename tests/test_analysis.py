import numpy as np
import pandas as pd
import pytest

from signalbot import formatting as fmt
from signalbot.config import INSTRUMENTS
from signalbot.indicators import add_indicators, resample_ohlc, rsi
from signalbot.news import score_text
from signalbot.regime import RANGE, TREND_DOWN, TREND_UP, classify, market_view
from signalbot.signals import evaluate


def make_ohlc(closes, freq="1h", noise=0.2, seed=0):
    rng = np.random.default_rng(seed)
    closes = np.asarray(closes, dtype=float)
    opens = np.r_[closes[0], closes[:-1]]
    spread = np.abs(rng.normal(0, noise, len(closes))) + noise
    idx = pd.date_range("2026-01-01", periods=len(closes), freq=freq, tz="UTC")
    return pd.DataFrame({
        "Open": opens,
        "High": np.maximum(opens, closes) + spread,
        "Low": np.minimum(opens, closes) - spread,
        "Close": closes,
        "Volume": rng.integers(1000, 2000, len(closes)).astype(float),
    }, index=idx)


def trend(n=300, slope=0.5, start=100.0, seed=1):
    rng = np.random.default_rng(seed)
    return start + slope * np.arange(n) + rng.normal(0, 0.3, n)


def ranging(n=300, center=100.0, amp=1.5, seed=2):
    rng = np.random.default_rng(seed)
    return center + amp * np.sin(np.arange(n) / 2) + rng.normal(0, 0.5, n)


def test_rsi_bounds():
    r = rsi(pd.Series(trend())).dropna()
    assert r.between(0, 100).all()
    assert r.iloc[-1] > 60  # tendance haussière -> RSI élevé


def test_uptrend_detected():
    reg = classify(add_indicators(make_ohlc(trend())), "H1")
    assert reg.code == TREND_UP


def test_downtrend_detected():
    reg = classify(add_indicators(make_ohlc(trend(slope=-0.5, start=300))), "H1")
    assert reg.code == TREND_DOWN


def test_range_detected():
    reg = classify(add_indicators(make_ohlc(ranging())), "H1")
    assert reg.code == RANGE
    assert reg.range_low < 100 < reg.range_high


def test_not_enough_data():
    assert classify(add_indicators(make_ohlc(trend(n=40))), "H1") is None


def test_market_view_mentions_buying_in_uptrend():
    df = add_indicators(make_ohlc(trend()))
    regs = {tf: classify(df, tf) for tf in ("D1", "H4", "H1")}
    assert "ACHATS" in market_view(regs)


def test_resample_4h():
    df = make_ohlc(trend(n=40))
    h4 = resample_ohlc(df, "4h")
    assert len(h4) == 10
    assert h4["High"].iloc[0] == df["High"].iloc[:4].max()


def _pullback_series(direction=1):
    """Tendance, repli marqué vers l'EMA20, puis reprise."""
    base = list(100 + 0.6 * np.arange(250))
    top = base[-1]
    pull = [top - 1.2 * i for i in range(1, 9)]
    bounce = [pull[-1] + 2.0, pull[-1] + 4.5]
    closes = np.array(base + pull + bounce)
    return closes if direction == 1 else 1000 - closes


@pytest.mark.parametrize("direction,label", [(1, "ACHAT"), (-1, "VENTE")])
def test_pullback_signal_both_directions(direction, label):
    df = add_indicators(make_ohlc(_pullback_series(direction), noise=0.1))
    frames = {"H1": df}
    regimes = {"H1": classify(df, "H1"), "H4": classify(df, "H4"), "D1": classify(df, "D1")}
    # Forcer le contexte supérieur dans le sens de la tendance
    for r in regimes.values():
        r.code = TREND_UP if direction == 1 else TREND_DOWN
    sigs = evaluate("NQ", frames, regimes)
    assert sigs, "un signal de repli devrait être détecté"
    best = sigs[0]
    assert best.direction == label
    assert best.setup == "Repli dans la tendance"
    if label == "ACHAT":
        assert best.stop < best.entry < best.tp1 < best.tp2
    else:
        assert best.stop > best.entry > best.tp1 > best.tp2
    assert best.risk_reward == pytest.approx(3, rel=0.01)

    # Annonce importante imminente -> score pénalisé + avertissement
    warned = evaluate("NQ", frames, regimes, ["CPI (08:30)"])[0]
    assert warned.score == best.score - 15
    assert warned.warnings

    msg = fmt.signal_message(best, INSTRUMENTS["NQ"])
    assert label in msg and "Stop" in msg


def test_no_signal_against_trend():
    df = add_indicators(make_ohlc(_pullback_series(1), noise=0.1))
    regimes = {tf: classify(df, tf) for tf in ("H1", "H4", "D1")}
    for r in regimes.values():
        r.code = TREND_DOWN
    sigs = evaluate("NQ", {"H1": df}, regimes)
    assert not [s for s in sigs if s.setup == "Repli dans la tendance" and s.direction == "ACHAT"]


def test_news_scoring():
    score, markets, tags = score_text("Gold surges as Powell signals rate cut after soft CPI")
    assert score >= 10
    assert {"NQ", "XAU"} <= markets
    assert "Fed" in tags and "Or" in tags
    assert score_text("Local bakery opens new store")[0] == 0


def test_regime_block_renders():
    df = add_indicators(make_ohlc(trend()))
    regs = {tf: classify(df, tf) for tf in ("D1", "H4", "H1")}
    txt = fmt.regime_block(INSTRUMENTS["XAU"], regs)
    assert "Or" in txt and "H4" in txt
