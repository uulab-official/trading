"""Pure, dependency-free quantitative performance metrics.

Inputs are ordered periodic equity observations. No trades are executed.
"""
from math import isclose, isfinite, sqrt
from typing import Sequence


def _returns(equity: Sequence[float]) -> list[float]:
    if len(equity) < 2:
        return []
    if any(not isfinite(x) or x <= 0 for x in equity):
        raise ValueError("equity must be positive and finite")
    return [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity))]


def max_drawdown(equity: Sequence[float]) -> float:
    """Nonnegative peak-to-trough fraction; zero for empty series."""
    if not equity:
        return 0.0
    if any(not isfinite(x) or x <= 0 for x in equity):
        raise ValueError("equity must be positive and finite")
    peak = equity[0]
    worst = 0.0
    for value in equity:
        peak = max(peak, value)
        worst = max(worst, (peak - value) / peak)
    return worst


def sharpe_ratio(equity: Sequence[float], periods_per_year: float = 252, risk_free_annual: float = 0) -> float | None:
    """Annualized arithmetic Sharpe; None when undefined.

    Observations must be equally spaced. Risk-free annual rate is converted
    to an effective per-period rate.
    """
    if not isfinite(periods_per_year) or periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive")
    if not isfinite(risk_free_annual) or risk_free_annual <= -1:
        raise ValueError("risk_free_annual must be greater than -1")
    values = _returns(equity)
    if len(values) < 2:
        return None
    rf = (1 + risk_free_annual) ** (1 / periods_per_year) - 1
    excess = [r - rf for r in values]
    mean = sum(excess) / len(excess)
    variance = sum((r - mean) ** 2 for r in excess) / (len(excess) - 1)
    if variance <= 0 or isclose(variance, 0.0, abs_tol=1e-28):
        return None
    return mean / sqrt(variance) * sqrt(periods_per_year)
