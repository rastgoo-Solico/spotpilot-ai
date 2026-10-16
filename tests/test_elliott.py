"""Focused tests for Elliott impulse validation and forecast gating."""
import ast
import math

import numpy as np
import pandas as pd
import pytest


def load_context_function():
    source = open("app.py", "r", encoding="utf-8").read()
    tree = ast.parse(source, filename="app.py")
    wanted = {"analyze_elliott_context"}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    namespace = {"pd": pd, "np": np, "math": math, "number": lambda x, default=0.0: default if x is None or pd.isna(x) else float(x)}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "app.py", "exec"), namespace)
    return namespace["analyze_elliott_context"]


def make_frame(last_close=150.0):
    count = 50
    times = pd.date_range("2026-01-01", periods=count, freq="4h", tz="UTC")
    close = np.linspace(145, 150, count)
    close[-1] = last_close
    return pd.DataFrame({"Time": times, "Open": close, "High": close + 1,
                         "Low": close - 1, "Close": close, "Volume": 100.0})


def make_impulse(prices=(100, 120, 110, 140, 130, 150)):
    kinds = ["low", "high", "low", "high", "low", "high"]
    return pd.DataFrame({
        "index": [10, 15, 20, 25, 30, 35],
        "Time": pd.date_range("2026-01-02", periods=6, freq="4h", tz="UTC"),
        "Price": prices, "Kind": kinds, "Label": ["0", "1", "2", "3", "4", "5"],
    })


def test_valid_impulse_produces_three_point_abc_scenario():
    fn = load_context_function()
    pivots = make_impulse()
    pivots.attrs["count_quality"] = "higher"
    result = fn(make_frame(), pivots)
    assert result["phase"] == "abc_correction_scenario"
    assert len(result["forecast"]) == 3
    assert result["forecast_labels"][0].startswith("A")
    assert result["direction"] == 1


def test_no_impulse_means_no_forecast_and_no_zigzag_fallback():
    fn = load_context_function()
    result = fn(make_frame(), make_impulse((100, 120, 121, 140, 130, 150)))
    assert result["phase"] == "no_valid_impulse"
    assert result["forecast"] == []
    assert result["invalid"]


def test_stale_wave_five_does_not_generate_detached_forecast():
    fn = load_context_function()
    pivots = make_impulse()
    result = fn(make_frame(last_close=160), pivots)
    assert result["phase"] == "post_impulse_unconfirmed"
    assert result["forecast"] == []
