"""Tests for modules/forecast_engine.py — Line 01: Demand Forecast."""
import pytest
from modules.forecast_engine import ForecastEngine
from modules.models import (
    Material, ProductType, PlanningConfig, PlanningRow, LineType,
)
from datetime import datetime
from unittest.mock import MagicMock


def _make_data(periods, forecasts, forecast_first_period=None, months_actuals=12):
    """Build a minimal DataLoader-like mock."""
    data = MagicMock()
    data.periods = periods
    data.forecasts = forecasts
    data.forecast_first_period = forecast_first_period
    data.materials = {}
    for mat_num in forecasts:
        data.materials[mat_num] = Material(
            material_number=mat_num, name=f"Mat {mat_num}",
            product_type=ProductType.BULK_PRODUCT, product_family="FAM",
        )
    return data


class TestForecastEngineBasic:
    """Verify that forecast values are placed into the correct planning periods."""

    def test_single_material_values(self):
        periods = ["2026-01", "2026-02", "2026-03"]
        # forecast_first_period = "2024-11", months_actuals=12
        # aux2_anchor = offset("2024-11", 13) = "2025-12"
        # planning period i=0 ("2026-01") reads forecast["2025-12"]
        # planning period i=1 ("2026-02") reads forecast["2026-01"]
        forecast_data = {
            "MAT1": {
                "2024-11": 10, "2024-12": 20, "2025-01": 30,
                "2025-02": 15, "2025-03": 25, "2025-04": 35,
                "2025-05": 40, "2025-06": 45, "2025-07": 50,
                "2025-08": 55, "2025-09": 60, "2025-10": 65,
                "2025-11": 70,
                "2025-12": 100, "2026-01": 200, "2026-02": 300,
            }
        }
        data = _make_data(periods, forecast_data, forecast_first_period="2024-11")
        engine = ForecastEngine(data, months_actuals=12, months_forecast=3)
        rows = engine.calculate()
        assert len(rows) == 1
        row = rows[0]
        assert row.line_type == LineType.DEMAND_FORECAST.value
        assert row.values["2026-01"] == 100.0
        assert row.values["2026-02"] == 200.0
        assert row.values["2026-03"] == 300.0

    def test_missing_forecast_yields_zero(self):
        periods = ["2026-01", "2026-02"]
        forecast_data = {"MAT1": {"2025-12": 50}}
        data = _make_data(periods, forecast_data, forecast_first_period="2024-11")
        engine = ForecastEngine(data, months_actuals=12, months_forecast=2)
        rows = engine.calculate()
        row = rows[0]
        assert row.values["2026-01"] == 50.0
        assert row.values["2026-02"] == 0.0

    def test_material_not_in_master_skipped(self):
        periods = ["2026-01"]
        forecast_data = {"UNKNOWN": {"2025-12": 100}}
        data = _make_data(periods, forecast_data, forecast_first_period="2024-11")
        data.materials = {}
        engine = ForecastEngine(data, months_actuals=12, months_forecast=1)
        rows = engine.calculate()
        assert len(rows) == 0


class TestForecastEngineAuxColumns:
    """Verify aux1 (avg actuals) and aux2 (avg forecast) calculations."""

    def test_aux_columns(self):
        periods = ["2026-01", "2026-02"]
        # Create 12 months of actuals starting from 2024-11 plus forecast
        forecast_data = {"MAT1": {}}
        # Actuals: 12 months from 2024-11 to 2025-10
        for i, val in enumerate([10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120]):
            m = 11 + i
            y = 2024 + (m - 1) // 12
            mm = ((m - 1) % 12) + 1
            key = f"{y}-{str(mm).zfill(2)}"
            forecast_data["MAT1"][key] = val
        # Skip Nov 2025 (gap period)
        forecast_data["MAT1"]["2025-12"] = 500
        forecast_data["MAT1"]["2026-01"] = 600

        data = _make_data(periods, forecast_data, forecast_first_period="2024-11")
        engine = ForecastEngine(data, months_actuals=12, months_forecast=2)
        rows = engine.calculate()
        row = rows[0]

        # aux1 = average of first 12 months
        expected_aux1 = round(sum([10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120]) / 12, 2)
        assert float(row.aux_column) == pytest.approx(expected_aux1)

    def test_offset_period_static(self):
        assert ForecastEngine._offset_period("2024-11", 0) == "2024-11"
        assert ForecastEngine._offset_period("2024-11", 1) == "2024-12"
        assert ForecastEngine._offset_period("2024-11", 2) == "2025-01"
        assert ForecastEngine._offset_period("2024-11", 13) == "2025-12"
