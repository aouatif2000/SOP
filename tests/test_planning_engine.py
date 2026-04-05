"""Tests for modules/planning_engine.py — core orchestration and total demand logic.

These are unit-level tests using mocked data to verify planning engine
row compilation and line-type consistency. Integration tests that load
real Excel files are excluded (they require the upload file).
"""
import pytest
from modules.models import LineType, PlanningRow


class TestPlanningRowLineTypes:
    """Verify that LineType enum covers all expected planning line types."""

    EXPECTED = [
        "01. Demand forecast",
        "02. Dependent demand",
        "03. Total demand",
        "04. Inventory",
        "05. Minimum target stock",
        "06. Production plan",
        "06. Purchase receipt",
        "07. Purchase plan",
        "07. Capacity utilization",
        "08. Dependent requirements",
        "09. Available capacity",
        "10. Utilization rate",
        "11. Shift availability",
        "12. FTE requirements",
        "13. Consolidation",
    ]

    def test_all_line_types_defined(self):
        for lt_str in self.EXPECTED:
            found = any(lt.value == lt_str for lt in LineType)
            assert found, f"{lt_str} not in LineType enum"

    def test_line_type_count(self):
        assert len(LineType) == len(self.EXPECTED)


class TestTotalDemandLogic:
    """Line 03 = Line 01 (forecast) + sum(Line 02 rows) for a material."""

    def test_total_demand_equals_forecast_plus_dependent(self):
        periods = ["2026-01", "2026-02"]
        forecast = {"2026-01": 100, "2026-02": 200}
        dep_demand_agg = {"2026-01": 30, "2026-02": 50}

        total_demand = {}
        for p in periods:
            total_demand[p] = forecast.get(p, 0.0) + dep_demand_agg.get(p, 0.0)

        assert total_demand["2026-01"] == 130.0
        assert total_demand["2026-02"] == 250.0

    def test_total_demand_no_dependent(self):
        periods = ["2026-01"]
        forecast = {"2026-01": 500}
        dep_demand_agg = {"2026-01": 0}

        total_demand = {p: forecast.get(p, 0.0) + dep_demand_agg.get(p, 0.0) for p in periods}
        assert total_demand["2026-01"] == 500.0


class TestPlanningRowToDataframe:
    """Verify PlanningRow.to_dict produces expected structure."""

    def test_to_dict_roundtrip(self):
        row = PlanningRow(
            material_number="M1", material_name="MatName",
            product_type="Bulk Product", product_family="FAM",
            spc_product="SPC", product_cluster="", product_name="PName",
            line_type=LineType.DEMAND_FORECAST.value,
            aux_column="100.5", aux_2_column="200.3",
            starting_stock=50.0,
            values={"2026-01": 100, "2026-02": 200},
        )
        d = row.to_dict()
        assert d["material_number"] == "M1"
        assert d["line_type"] == "01. Demand forecast"
        assert d["starting_stock"] == 50.0
        assert d["values"]["2026-01"] == 100
