from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass
class PatternResult:
    name: str
    side: str
    confidence: float
    trigger: float | None = None
    invalidation: float | None = None


def _swing_points(df: pd.DataFrame, window: int = 3):
    highs, lows = [], []
    h = df['high'].to_numpy(float)
    l = df['low'].to_numpy(float)
    for i in range(window, len(df) - window):
        if h[i] >= h[i-window:i+window+1].max():
            highs.append((i, h[i]))
        if l[i] <= l[i-window:i+window+1].min():
            lows.append((i, l[i]))
    return highs, lows


def _near(a, b, tol=0.012):
    return abs(a-b) / max(abs(a), abs(b), 1e-9) <= tol


def detect_patterns(df: pd.DataFrame) -> list[PatternResult]:
    """Approximate rule-based detector for the user's chart-pattern playbook.
    Patterns are evidence for scoring; they are never sufficient by themselves.
    """
    if len(df) < 45:
        return []
    d = df.tail(120).copy().reset_index(drop=True)
    highs, lows = _swing_points(d, 3)
    if len(highs) < 2 or len(lows) < 2:
        return []

    out = []
    close = float(d['close'].iloc[-1])
    atr = float((d['high'] - d['low']).rolling(14).mean().iloc[-1])
    tol = max(0.006, min(0.02, atr / max(close, 1e-9) * 1.5))

    hs = highs[-4:]
    ls = lows[-4:]
    hvals = [x[1] for x in hs]
    lvals = [x[1] for x in ls]

    # Double / triple tops & bottoms.
    if len(hvals) >= 2 and _near(hvals[-1], hvals[-2], tol):
        neckline = min(x[1] for x in lows if x[0] > hs[-2][0]) if any(x[0] > hs[-2][0] for x in lows) else close
        out.append(PatternResult('Double Top', 'PUT', 0.78, neckline, max(hvals[-2:])))
    if len(lvals) >= 2 and _near(lvals[-1], lvals[-2], tol):
        neckline = max(x[1] for x in highs if x[0] > ls[-2][0]) if any(x[0] > ls[-2][0] for x in highs) else close
        out.append(PatternResult('Double Bottom', 'CALL', 0.78, neckline, min(lvals[-2:])))
    if len(hvals) >= 3 and all(_near(hvals[-1], x, tol) for x in hvals[-3:-1]):
        out.append(PatternResult('Triple Top', 'PUT', 0.84, min(lvals[-2:]), max(hvals[-3:])))
    if len(lvals) >= 3 and all(_near(lvals[-1], x, tol) for x in lvals[-3:-1]):
        out.append(PatternResult('Triple Bottom', 'CALL', 0.84, max(hvals[-2:]), min(lvals[-3:])))

    # Head & shoulders / inverse H&S using last three major swings.
    if len(hvals) >= 3 and hvals[-2] > hvals[-3] and hvals[-2] > hvals[-1] and _near(hvals[-3], hvals[-1], 0.025):
        out.append(PatternResult('Head & Shoulders', 'PUT', 0.82, min(lvals[-2:]), hvals[-1]))
    if len(lvals) >= 3 and lvals[-2] < lvals[-3] and lvals[-2] < lvals[-1] and _near(lvals[-3], lvals[-1], 0.025):
        out.append(PatternResult('Inverse Head & Shoulders', 'CALL', 0.82, max(hvals[-2:]), lvals[-1]))

    # Triangles / wedges from recent swing slopes.
    if len(hs) >= 3 and len(ls) >= 3:
        hx = np.array([x[0] for x in hs[-3:]], float); hy = np.array(hvals[-3:], float)
        lx = np.array([x[0] for x in ls[-3:]], float); ly = np.array(lvals[-3:], float)
        h_slope = np.polyfit(hx, hy, 1)[0] / close
        l_slope = np.polyfit(lx, ly, 1)[0] / close
        flat = 0.00035
        if abs(h_slope) < flat and l_slope > flat:
            out.append(PatternResult('Ascending Triangle', 'CALL', 0.82, float(np.mean(hvals[-3:])), min(lvals[-2:])))
        if h_slope < -flat and abs(l_slope) < flat:
            out.append(PatternResult('Descending Triangle', 'PUT', 0.82, float(np.mean(lvals[-3:])), max(hvals[-2:])))
        if h_slope < -flat and l_slope > flat:
            side = 'CALL' if close > (hvals[-1] + lvals[-1]) / 2 else 'PUT'
            out.append(PatternResult('Symmetrical Triangle', side, 0.70))
        if h_slope > flat and l_slope > flat and l_slope > h_slope:
            out.append(PatternResult('Rising Wedge', 'PUT', 0.73))
        if h_slope < -flat and l_slope < -flat and h_slope > l_slope:
            out.append(PatternResult('Falling Wedge', 'CALL', 0.73))

    # Rectangle/range.
    recent = d.tail(30)
    rng = (recent['high'].max() - recent['low'].min()) / close
    if rng < 0.06 and len(hvals) >= 2 and len(lvals) >= 2 and _near(hvals[-1], hvals[-2], 0.018) and _near(lvals[-1], lvals[-2], 0.018):
        mid = (recent['high'].max() + recent['low'].min()) / 2
        side = 'CALL' if close >= mid else 'PUT'
        out.append(PatternResult('Rectangle', side, 0.68, float(recent['high'].max() if side=='CALL' else recent['low'].min())))

    # Flag / pennant: impulsive move followed by tight compression.
    impulse = float(d['close'].iloc[-16] - d['close'].iloc[-31]) if len(d) >= 31 else 0
    compression = (d['high'].tail(15).max() - d['low'].tail(15).min()) / close
    if abs(impulse) / close > 0.035 and compression < 0.035:
        side = 'CALL' if impulse > 0 else 'PUT'
        out.append(PatternResult('Flag / Pennant', side, 0.72))

    # Cup/handle family: rounded extreme + shallow recent handle (coarse detector).
    w = d.tail(70)
    min_i = int(np.argmin(w['low'].to_numpy()))
    max_i = int(np.argmax(w['high'].to_numpy()))
    if 15 < min_i < 50 and close > float(w['close'].iloc[0]) * 0.98 and float(w['low'].iloc[min_i]) < min(float(w['low'].iloc[:10].mean()), float(w['low'].iloc[-10:].mean())):
        out.append(PatternResult('Cup & Handle', 'CALL', 0.62))
    if 15 < max_i < 50 and close < float(w['close'].iloc[0]) * 1.02 and float(w['high'].iloc[max_i]) > max(float(w['high'].iloc[:10].mean()), float(w['high'].iloc[-10:].mean())):
        out.append(PatternResult('Inverse Cup & Handle', 'PUT', 0.62))

    # Keep highest-confidence unique names.
    best = {}
    for p in out:
        if p.name not in best or p.confidence > best[p.name].confidence:
            best[p.name] = p
    return sorted(best.values(), key=lambda x: x.confidence, reverse=True)
