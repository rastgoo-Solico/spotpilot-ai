"""Focused unit tests for pure indicator/data-normalization helpers in app.py.

Run with: python -m pytest -q
These tests extract pure functions from app.py's AST to avoid executing the
Streamlit dashboard or making live exchange/API calls during import.
"""
import ast
import math
import time

import numpy as np
import pandas as pd
import pytest

APP_PATH = "app.py"
TIMEFRAMES = {
    "15 دقیقه": {"seconds": 15 * 60},
    "4 ساعت": {"seconds": 4 * 60 * 60},
    "روزانه": {"seconds": 24 * 60 * 60},
}


def load_pure_functions():
    source = open(APP_PATH, "r", encoding="utf-8").read()
    tree = ast.parse(source, filename=APP_PATH)
    wanted = {"calculate_atr", "tiered_score", "tiered_score_atr", "_normalize_ccxt_ohlcv"}
    nodes = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted
    ]
    namespace = {
        "pd": pd,
        "np": np,
        "math": math,
        "time": time,
        "TIMEFRAMES": TIMEFRAMES,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), APP_PATH, "exec"), namespace)
    return namespace


@pytest.fixture(scope="module")
def funcs():
    return load_pure_functions()


def sample_frame():
    return pd.DataFrame({
        "High": [11, 13, 14, 15, 18, 19, 20, 22, 21, 23, 24, 26, 27, 28, 30, 32],
        "Low":  [9, 10, 12, 12, 14, 15, 17, 18, 17, 20, 21, 22, 24, 25, 26, 29],
        "Close":[10, 12, 13, 14, 16, 17, 19, 20, 19, 22, 23, 25, 26, 27, 29, 31],
    })


def test_atr_period_one_is_true_range(funcs):
    frame = sample_frame()
    atr = funcs["calculate_atr"](frame, 1)
    prev = frame["Close"].shift(1)
    expected = pd.concat([
        frame["High"] - frame["Low"],
        (frame["High"] - prev).abs(),
        (frame["Low"] - prev).abs(),
    ], axis=1).max(axis=1, skipna=True)
    np.testing.assert_allclose(atr.to_numpy(), expected.to_numpy(), equal_nan=True)


def test_atr_period_four_uses_sma_seed_then_wilder_recurrence(funcs):
    frame = sample_frame()
    atr = funcs["calculate_atr"](frame, 4)
    tr = funcs["calculate_atr"](frame, 1).to_numpy()
    assert np.isnan(atr.iloc[:3]).all()
    expected_seed = float(np.mean(tr[:4]))
    assert atr.iloc[3] == pytest.approx(expected_seed)
    assert atr.iloc[4] == pytest.approx((expected_seed * 3 + tr[4]) / 4)


@pytest.mark.parametrize(
    "pct,atr,expected",
    [(5, 2, 2), (2, 2, 1), (-2, 2, -1), (-7, 2, -2), (0.5, 2, 0)],
)
def test_atr_relative_score(funcs, pct, atr, expected):
    assert funcs["tiered_score_atr"](pct, atr) == expected


def test_atr_relative_score_falls_back_when_atr_missing(funcs):
    assert funcs["tiered_score_atr"](2.0, None) == 1


def test_ccxt_normalization_sorts_deduplicates_validates_and_drops_open_candles(funcs):
    # 15-minute candles; now_seconds is fixed so the test is deterministic.
    base = 1_800_000_000
    raw = [
        [((base + 900) * 1000), 10, 12, 9, 11, 5],  # future/open
        [((base - 900) * 1000), 10, 12, 9, 11, 5],
        [((base - 1800) * 1000), 10, 11, 8, 9, 4],
        [((base - 900) * 1000), 10, 13, 9, 12, 6],  # duplicate: keep last
        [((base - 2700) * 1000), 10, 8, 9, 11, 5],   # invalid high/low geometry
        [((base - 3600) * 1000), 10, 12, 9, 11, -1], # invalid volume
    ]
    result = funcs["_normalize_ccxt_ohlcv"](raw, "15 دقیقه", now_seconds=base)
    assert result["timestamp"].tolist() == [base - 1800, base - 900]
    assert result.iloc[-1]["Close"] == 12
    assert (result["Volume"] >= 0).all()
    assert (result["High"] >= result[["Open", "Close", "Low"]].max(axis=1)).all()


def test_ccxt_normalization_empty_input(funcs):
    assert funcs["_normalize_ccxt_ohlcv"]([], "15 دقیقه", now_seconds=1_800_000_000) is None
