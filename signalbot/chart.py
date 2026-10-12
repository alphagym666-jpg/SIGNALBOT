"""Image du graphique (bougies 1 h) avec les zones ACHAT / VENTE, style TradingView sombre."""

from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")  # pas d'écran : on dessine dans un fichier
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from .zones import Zone  # noqa: E402

BG, GRID, TEXT = "#131722", "#2a2e39", "#d1d4dc"
UP, DOWN = "#26a69a", "#ef5350"
BUY, SELL = "#1e9e5a", "#d93a3a"


def _stars(force: int) -> str:
    return "★" * min(force, 5)


def render(title: str, candles: pd.DataFrame, zones: list[Zone], price: float | None,
           subtitle: str = "", offset: float = 0.0, bars: int = 90) -> bytes:
    """PNG du graphique. `offset` décale les bougies vers le prix du courtier (comme les zones)."""
    df = candles.tail(bars) if candles is not None and not candles.empty else pd.DataFrame()
    fig, ax = plt.subplots(figsize=(12, 7), dpi=110)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.tick_params(colors=TEXT, labelsize=9)
    ax.grid(color=GRID, linewidth=0.6)
    ax.yaxis.tick_right()

    n = len(df)
    right = n + max(28, n // 3)  # espace à droite pour les étiquettes des zones
    if n:
        o, h, l, c = (df[k].to_numpy() + offset for k in ("Open", "High", "Low", "Close"))
        for i in range(n):
            color = UP if c[i] >= o[i] else DOWN
            ax.vlines(i, l[i], h[i], color=color, linewidth=0.8)
            ax.add_patch(plt.Rectangle((i - 0.35, min(o[i], c[i])), 0.7, max(abs(c[i] - o[i]), 1e-9),
                                       color=color))
        step = max(1, n // 8)
        ticks = list(range(0, n, step))
        idx = df.index.tz_convert("America/Toronto") if df.index.tz is not None else df.index
        ax.set_xticks(ticks, [idx[i].strftime("%d/%m %Hh") for i in ticks])

    for z in zones:
        color = BUY if z.side == "ACHAT" else SELL
        ax.axhspan(z.low, z.high, xmin=0, xmax=1, color=color, alpha=0.18 + 0.06 * min(z.force, 5))
        ax.axhline(z.mid, color=color, linewidth=0.8, linestyle="--", alpha=0.8)
        label = f"{z.side} {_stars(z.force)} {z.mid:,.2f}\n" + ", ".join(z.reasons[:3])
        ax.text(n + 1, z.mid, label, color="white", fontsize=8, va="center",
                bbox=dict(facecolor=color, edgecolor="none", alpha=0.85, boxstyle="round,pad=0.3"))

    if price:
        p = price + offset
        ax.axhline(p, color="#f0b90b", linewidth=1.1)
        ax.text(right - 1, p, f" {p:,.2f}", color="black", fontsize=9, va="center", ha="right",
                bbox=dict(facecolor="#f0b90b", edgecolor="none", boxstyle="round,pad=0.2"))

    values = [z.low for z in zones] + [z.high for z in zones]
    if n:
        values += [float(df["Low"].min()) + offset, float(df["High"].max()) + offset]
    if price:
        values.append(price + offset)
    if values:
        lo, hi = min(values), max(values)
        margin = (hi - lo) * 0.05 or 1
        ax.set_ylim(lo - margin, hi + margin)
    ax.set_xlim(-1, right)
    ax.set_title(title, color=TEXT, fontsize=14, loc="left", fontweight="bold")
    if subtitle:
        fig.text(0.01, 0.01, subtitle, color="#9598a1", fontsize=8)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG)
    plt.close(fig)
    return buf.getvalue()
