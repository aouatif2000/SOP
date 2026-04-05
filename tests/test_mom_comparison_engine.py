"""Tests for modules/mom_comparison_engine.py — Month-over-Month delta calculation."""
import pytest
import pandas as pd
import numpy as np
from modules.mom_comparison_engine import MoMComparisonEngine


def _make_planning_df(inventory_data, periods):
    """Build a planning DataFrame with 04. Inventory rows.

    inventory_data: dict of {mat_num: {period: value}}
    """
    rows = []
    for mat_num, period_vals in inventory_data.items():
        row = {
            "Material number": mat_num,
            "Material name": f"Mat {mat_num}",
            "Product type": "Bulk Product",
            "Line type": "04. Inventory",
        }
        for p in periods:
            row[p] = period_vals.get(p, 0.0)
        rows.append(row)
    return pd.DataFrame(rows)


PERIODS = ["2026-01", "2026-02", "2026-03"]


class TestMoMDeltaCalculation:
    def test_basic_delta(self):
        current = _make_planning_df(
            {"M1": {"2026-01": 100, "2026-02": 200, "2026-03": 300}},
            PERIODS,
        )
        previous = _make_planning_df(
            {"M1": {"2026-01": 80, "2026-02": 180, "2026-03": 350}},
            PERIODS,
        )
        engine = MoMComparisonEngine(current, previous)
        result = engine.calculate()

        assert len(result) == 3
        row_jan = result[(result["Material number"] == "M1") & (result["Period"] == "2026-01")]
        assert row_jan["Delta"].values[0] == pytest.approx(20.0)
        row_mar = result[(result["Material number"] == "M1") & (result["Period"] == "2026-03")]
        assert row_mar["Delta"].values[0] == pytest.approx(-50.0)

    def test_delta_percentage(self):
        current = _make_planning_df(
            {"M1": {"2026-01": 150}}, ["2026-01"],
        )
        previous = _make_planning_df(
            {"M1": {"2026-01": 100}}, ["2026-01"],
        )
        engine = MoMComparisonEngine(current, previous)
        result = engine.calculate()
        assert result["Delta %"].values[0] == pytest.approx(50.0)

    def test_delta_percentage_zero_previous(self):
        current = _make_planning_df(
            {"M1": {"2026-01": 100}}, ["2026-01"],
        )
        previous = _make_planning_df(
            {"M1": {"2026-01": 0}}, ["2026-01"],
        )
        engine = MoMComparisonEngine(current, previous)
        result = engine.calculate()
        assert result["Delta %"].values[0] == np.inf

    def test_only_common_periods(self):
        current = _make_planning_df(
            {"M1": {"2026-01": 100, "2026-02": 200}}, ["2026-01", "2026-02"],
        )
        previous = _make_planning_df(
            {"M1": {"2026-02": 150, "2026-03": 300}}, ["2026-02", "2026-03"],
        )
        engine = MoMComparisonEngine(current, previous)
        result = engine.calculate()
        # Only 2026-02 is common
        assert len(result) == 1
        assert result["Period"].values[0] == "2026-02"
        assert result["Delta"].values[0] == pytest.approx(50.0)

    def test_only_common_materials(self):
        current = _make_planning_df(
            {"M1": {"2026-01": 100}, "M2": {"2026-01": 200}}, ["2026-01"],
        )
        previous = _make_planning_df(
            {"M1": {"2026-01": 80}}, ["2026-01"],
        )
        engine = MoMComparisonEngine(current, previous)
        result = engine.calculate()
        # Only M1 is in both
        assert len(result) == 1
        assert result["Material number"].values[0] == "M1"

    def test_empty_input(self):
        engine = MoMComparisonEngine(pd.DataFrame(), pd.DataFrame())
        result = engine.calculate()
        assert result.empty


class TestMoMScatterData:
    def test_scatter_colors(self):
        current = _make_planning_df(
            {"M1": {"2026-01": 100}, "M2": {"2026-01": -50}}, ["2026-01"],
        )
        previous = _make_planning_df(
            {"M1": {"2026-01": 80}, "M2": {"2026-01": 60}}, ["2026-01"],
        )
        engine = MoMComparisonEngine(current, previous)
        scatter = engine.create_scatter_data()

        assert len(scatter["materials"]) == 2
        assert len(scatter["colors"]) == 2
        # M1: current>0, previous>0 → green
        idx_m1 = scatter["materials"].index("M1")
        assert scatter["colors"][idx_m1] == "C6EFCE"
        # M2: current<0, previous>0 → orange
        idx_m2 = scatter["materials"].index("M2")
        assert scatter["colors"][idx_m2] == "FFC896"

    def test_scatter_empty(self):
        engine = MoMComparisonEngine(pd.DataFrame(), pd.DataFrame())
        scatter = engine.create_scatter_data()
        assert scatter["materials"] == []
