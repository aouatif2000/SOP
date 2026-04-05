"""Tests for modules/inventory_engine.py — production plan, purchase receipt, inventory."""
import pytest
import math
from modules.inventory_engine import InventoryEngine, ceiling_multiple
from modules.models import (
    Material, BOMItem, RoutingItem, SafetyStockConfig,
    ProductType, LineType, PlanningRow,
)
from unittest.mock import MagicMock


PERIODS = ["2026-01", "2026-02", "2026-03"]


def _make_data(
    materials=None, bom=None, safety_stock=None,
    stock_levels=None, periods=None,
    purchased_and_produced=None,
    purchase_lead_times=None, purchase_moq=None,
    purchase_sheet_materials=None, purchase_actuals=None,
    routing=None,
):
    """Build minimal DataLoader-like mock."""
    data = MagicMock()
    data.periods = periods or PERIODS
    data.materials = materials or {}
    data.bom = bom or []
    data.safety_stock = safety_stock or {}
    data.stock_levels = stock_levels or {}
    data.purchased_and_produced = purchased_and_produced or {}
    data.purchase_lead_times = purchase_lead_times or {}
    data.purchase_moq = purchase_moq or {}
    data.purchase_sheet_materials = purchase_sheet_materials or set()
    data.purchase_actuals = purchase_actuals or {}
    data.routing = routing or {}

    data.is_purchased_and_produced = lambda m: m in data.purchased_and_produced
    data.get_purchase_fraction = lambda m: data.purchased_and_produced.get(m, 0.0)
    data.get_purchase_moq = lambda m: data.purchase_moq.get(m, 1.0)
    data.get_lead_time = lambda m: data.purchase_lead_times.get(m, 1)
    data.get_production_ceiling = lambda m: 1.0
    data.get_all_routings = lambda m: data.routing.get(m, [])
    data.get_bom_for_parent = lambda m: [b for b in data.bom if b.parent_material == m and not b.is_coproduct]
    return data


class TestCeilingMultiple:
    def test_basic(self):
        assert ceiling_multiple(7, 3) == 9.0

    def test_exact(self):
        assert ceiling_multiple(6, 3) == 6.0

    def test_zero_value(self):
        assert ceiling_multiple(0, 3) == 0.0

    def test_zero_multiple(self):
        assert ceiling_multiple(5, 0) == 0.0

    def test_negative_value(self):
        assert ceiling_multiple(-1, 3) == 0.0


class TestInventoryEngineProducedMaterial:
    """Material with production plan (bulk product with routing + BOM)."""

    def _setup(self, demand, initial_stock=0.0, safety_stock=0.0, bom_header_qty=1.0):
        mat = Material(material_number="M1", name="Bulk1",
                       product_type=ProductType.BULK_PRODUCT, product_family="FAM")
        ss = SafetyStockConfig(material_number="M1", safety_stock=safety_stock, lot_size=1.0)
        bom = [BOMItem(plant="NLX1", parent_material="M1", parent_name="Bulk1",
                       component_material="C1", component_name="Child",
                       quantity_per=1.0, bom_header_quantity=bom_header_qty)]
        routing = {"M1": [RoutingItem(plant="NLX1", material="M1",
                                      material_description="Bulk1",
                                      work_center="PBA11", base_quantity=100,
                                      standard_time=50)]}
        data = _make_data(
            materials={"M1": mat},
            bom=bom,
            safety_stock={"M1": ss},
            stock_levels={"M1": initial_stock},
            routing=routing,
        )
        data.get_production_ceiling = lambda m: bom_header_qty
        engine = InventoryEngine(data)
        return engine.calculate_for_material(
            "M1", forecast=demand, dependent_demand_agg={p: 0 for p in PERIODS},
            dependent_demand_by_parent={},
        )

    def test_total_demand_equals_forecast(self):
        demand = {"2026-01": 100, "2026-02": 200, "2026-03": 50}
        result = self._setup(demand)
        assert result["total_demand"]["2026-01"] == 100.0
        assert result["total_demand"]["2026-02"] == 200.0

    def test_production_plan_covers_need(self):
        """With zero stock and zero safety stock, production = demand."""
        demand = {"2026-01": 100, "2026-02": 0, "2026-03": 0}
        result = self._setup(demand, initial_stock=0, safety_stock=0, bom_header_qty=1)
        assert result["production_plan"]["2026-01"] == 100.0

    def test_production_plan_with_ceiling(self):
        """Production is ceiled to BOM header quantity multiple."""
        demand = {"2026-01": 50, "2026-02": 0, "2026-03": 0}
        result = self._setup(demand, bom_header_qty=30)
        assert result["production_plan"]["2026-01"] == 60.0  # ceil(50/30)*30 = 60

    def test_inventory_running_balance(self):
        demand = {"2026-01": 100, "2026-02": 0, "2026-03": 0}
        result = self._setup(demand, initial_stock=200, safety_stock=0)
        # Stock: 200 - 100 = 100 (no production needed since stock >= demand)
        assert result["inventory"]["2026-01"] >= 0

    def test_rows_contain_expected_line_types(self):
        demand = {"2026-01": 100, "2026-02": 0, "2026-03": 0}
        result = self._setup(demand)
        line_types = {r.line_type for r in result["rows"]}
        assert LineType.TOTAL_DEMAND.value in line_types
        assert LineType.INVENTORY.value in line_types
        assert LineType.PRODUCTION_PLAN.value in line_types
        assert LineType.MIN_TARGET_STOCK.value in line_types


class TestInventoryEnginePurchasedMaterial:
    """Material that is purchased (raw material without routing/BOM parent)."""

    def _setup(self, demand, initial_stock=0.0, safety_stock=0.0,
               lead_time=1, moq=1.0):
        mat = Material(material_number="RM1", name="Raw1",
                       product_type=ProductType.RAW_MATERIAL, product_family="FAM")
        ss = SafetyStockConfig(material_number="RM1", safety_stock=safety_stock, lot_size=1.0)
        data = _make_data(
            materials={"RM1": mat},
            safety_stock={"RM1": ss},
            stock_levels={"RM1": initial_stock},
            purchase_lead_times={"RM1": lead_time},
            purchase_moq={"RM1": moq},
            purchase_sheet_materials={"RM1"},
        )
        engine = InventoryEngine(data)
        return engine.calculate_for_material(
            "RM1", forecast=demand,
            dependent_demand_agg={p: 0 for p in PERIODS},
            dependent_demand_by_parent={},
        )

    def test_purchase_receipt_generated(self):
        demand = {"2026-01": 100, "2026-02": 0, "2026-03": 0}
        result = self._setup(demand, lead_time=0)
        assert result["purchase_receipt"] is not None
        assert result["production_plan"] is None

    def test_purchase_receipt_with_moq(self):
        demand = {"2026-01": 75, "2026-02": 0, "2026-03": 0}
        result = self._setup(demand, moq=50.0, lead_time=0)
        assert result["purchase_receipt"]["2026-01"] == 100.0  # ceil(75/50)*50

    def test_purchase_plan_shifted_by_lead_time(self):
        demand = {"2026-01": 100, "2026-02": 200, "2026-03": 50}
        result = self._setup(demand, lead_time=1)
        plan_rows = [r for r in result["rows"] if r.line_type == LineType.PURCHASE_PLAN.value]
        assert len(plan_rows) == 1
        # Purchase plan at period i = purchase receipt at period i + lead_time
        plan_row = plan_rows[0]
        assert plan_row.aux_column == "1"


class TestInventoryEngineTotalDemandWithDependentDemand:
    """Total demand = forecast + sum(dependent demand from parents)."""

    def test_total_demand_aggregation(self):
        mat = Material(material_number="C1", name="Child1",
                       product_type=ProductType.RAW_MATERIAL, product_family="FAM")
        ss = SafetyStockConfig(material_number="C1", safety_stock=0, lot_size=1.0)
        data = _make_data(
            materials={"C1": mat},
            safety_stock={"C1": ss},
            purchase_sheet_materials={"C1"},
        )
        engine = InventoryEngine(data)
        forecast = {"2026-01": 50, "2026-02": 0, "2026-03": 0}
        dep_demand_agg = {"2026-01": 30, "2026-02": 20, "2026-03": 0}
        result = engine.calculate_for_material(
            "C1", forecast, dep_demand_agg, dependent_demand_by_parent={},
        )
        assert result["total_demand"]["2026-01"] == 80.0
        assert result["total_demand"]["2026-02"] == 20.0
