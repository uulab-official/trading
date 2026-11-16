"""Leakage-aware chronological walk-forward fold generator.

All indices refer to original observations. Embargo excludes observations
between train and test. Never shuffle financial time series.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Fold:
    train_start: int
    train_end: int
    test_start: int
    test_end: int


def walk_forward_folds(
    n_observations: int,
    train_size: int,
    test_size: int,
    embargo: int = 0,
    anchored: bool = False,
) -> list[Fold]:
    """Return half-open train/test index intervals, advancing by test_size."""
    if n_observations < 0 or train_size <= 0 or test_size <= 0 or embargo < 0:
        raise ValueError("invalid fold configuration")
    folds: list[Fold] = []
    test_start = train_size + embargo
    while test_start + test_size <= n_observations:
        train_end = test_start - embargo
        train_start = 0 if anchored else train_end - train_size
        folds.append(Fold(train_start, train_end, test_start, test_start + test_size))
        test_start += test_size
    return folds
