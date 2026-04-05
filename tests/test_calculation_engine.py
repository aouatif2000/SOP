"""Tests for modules/calculation_engine.py — legacy demand/total demand calculations."""
import pytest
from modules.calculation_engine import CalculationEngine, PlanningConfig, Material, BOMItem


class TestCalculationEngineDemandForecast:
    def test_demand_forecast_returns_produced_materials(self):
        config = PlanningConfig(initial_date="2026-01", forecast_months=3)
        engine = CalculationEngine(config)
        engine.materials = {
            "P1": Material(material_id="P1", name="Produced", material_type="PRODUCED"),
            "R1": Material(material_id="R1", name="Raw", material_type="PURCHASED"),
        }
        engine.forecast = {
            "P1": {"2026-01": 100, "2026-02": 200},
            "R1": {"2026-01": 50},
        }
        result = engine.calculate_demand_forecast()
        assert "P1" in result
        assert "R1" not in result
        assert result["P1"]["2026-01"] == 100


class TestCalculationEngineDependentDemand:
    def test_dependent_demand(self):
        config = PlanningConfig(initial_date="2026-01")
        engine = CalculationEngine(config)
        engine.materials = {
            "P1": Material(material_id="P1", name="Produced", material_type="PRODUCED"),
            "C1": Material(material_id="C1", name="Child", material_type="PURCHASED"),
        }
        engine.bom = [BOMItem(parent_id="P1", child_id="C1", qty_per=2.5)]
        production_plan = {"P1": {"2026-01": 100}}
        result = engine.calculate_dependent_demand(production_plan)
        assert result["C1"]["2026-01"] == pytest.approx(250.0)

    def test_no_bom(self):
        config = PlanningConfig(initial_date="2026-01")
        engine = CalculationEngine(config)
        engine.materials = {"P1": Material(material_id="P1", name="Produced", material_type="PRODUCED")}
        engine.bom = []
        result = engine.calculate_dependent_demand({"P1": {"2026-01": 100}})
        assert result.get("P1", {}).get("2026-01", 0) == 0


class TestCalculationEngineTotalDemand:
    def test_total_demand(self):
        config = PlanningConfig(initial_date="2026-01")
        engine = CalculationEngine(config)
        df = {"P1": {"2026-01": 100, "2026-02": 200}}
        dd = {"P1": {"2026-01": 50}}
        result = engine.calculate_total_demand(df, dd)
        assert result["P1"]["2026-01"] == 150
        assert result["P1"]["2026-02"] == 200

    def test_total_demand_union_of_materials(self):
        config = PlanningConfig(initial_date="2026-01")
        engine = CalculationEngine(config)
        df = {"P1": {"2026-01": 100}}
        dd = {"C1": {"2026-01": 50}}
        result = engine.calculate_total_demand(df, dd)
        assert "P1" in result
        assert "C1" in result
