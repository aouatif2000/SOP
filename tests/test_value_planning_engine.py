"""Tests for modules/value_planning_engine.py — financial consolidation rows."""
import pytest
from modules.value_planning_engine import ValuePlanningEngine
from modules.models import (
    Material, ProductType, LineType, PlanningRow,
    ValuationParameters, SalesPriceItem, RawMaterialCost, MachineCost,
)
from unittest.mock import MagicMock


PERIODS = ["2026-01", "2026-02", "2026-03"]


def _make_row(mat_num, line_type, values, aux=None, aux2=None, starting_stock=0.0):
    return PlanningRow(
        material_number=mat_num, material_name=f"Mat {mat_num}",
        product_type="Bulk Product", product_family="FAM",
        spc_product="", product_cluster="", product_name="",
        line_type=line_type, aux_column=aux, aux_2_column=aux2,
        starting_stock=starting_stock, values=dict(values),
    )


def _make_data(
    sales_prices=None, material_costs=None, machine_costs=None,
    stock=None, valuation_params=None, materials=None,
):
    data = MagicMock()
    data.periods = PERIODS
    data.sales_prices = sales_prices or {}
    data.material_costs = material_costs or {}
    data.machine_costs = machine_costs or {}
    data.stock = stock or {}
    data.materials = materials or {}
    data.purchased_and_produced = {}
    data.valuation_params = valuation_params or ValuationParameters(
        direct_fte_cost_per_month=5000.0,
        indirect_fte_cost_per_month=3000.0,
        overhead_cost_per_month=2000.0,
        sga_cost_per_month=1500.0,
        depreciation_per_year=12000.0,
        net_book_value=100000.0,
        days_sales_outstanding=30,
        days_payable_outstanding=45,
    )
    return data


class TestDemandForecastToRevenue:
    """Turnover = demand_volume * sales_price."""

    def test_basic_revenue(self):
        planning_results = {
            LineType.DEMAND_FORECAST.value: [
                _make_row("M1", LineType.DEMAND_FORECAST.value,
                          {"2026-01": 100, "2026-02": 200, "2026-03": 0}),
            ],
            LineType.PURCHASE_PLAN.value: [],
            LineType.TOTAL_DEMAND.value: [],
            LineType.INVENTORY.value: [],
            LineType.PURCHASE_RECEIPT.value: [],
            LineType.CAPACITY_UTILIZATION.value: [],
            LineType.FTE_REQUIREMENTS.value: [],
        }
        data = _make_data(
            sales_prices={
                "M1": SalesPriceItem(plant_code="NLX1", product_id="M1",
                                     volume_2025=1000, ex_works_revenue=10000)
            }
        )
        engine = ValuePlanningEngine(data, planning_results)
        results = engine.calculate()

        demand_val = results[LineType.DEMAND_FORECAST.value]
        assert len(demand_val) == 1
        # price = 10000/1000 = 10
        assert demand_val[0].values["2026-01"] == pytest.approx(1000.0)
        assert demand_val[0].values["2026-02"] == pytest.approx(2000.0)


class TestInventoryValue:
    """Inventory value = volume * (TotalValue / TotalStock)."""

    def test_inventory_conversion(self):
        planning_results = {
            LineType.DEMAND_FORECAST.value: [],
            LineType.PURCHASE_PLAN.value: [],
            LineType.TOTAL_DEMAND.value: [],
            LineType.INVENTORY.value: [
                _make_row("M1", LineType.INVENTORY.value,
                          {"2026-01": 500, "2026-02": 400, "2026-03": 300},
                          starting_stock=600.0),
            ],
            LineType.PURCHASE_RECEIPT.value: [],
            LineType.CAPACITY_UTILIZATION.value: [],
            LineType.FTE_REQUIREMENTS.value: [],
        }
        data = _make_data(
            stock={"M1": {"Total Stock": 1000, "Total Value": 5000, "Value Unrestricted": 3000}},
            materials={"M1": Material(material_number="M1", name="Mat1",
                                      product_type=ProductType.BULK_PRODUCT,
                                      product_family="FAM")},
        )
        engine = ValuePlanningEngine(data, planning_results)
        results = engine.calculate()

        inv_val = results[LineType.INVENTORY.value]
        assert len(inv_val) == 1
        # unit cost = 5000/1000 = 5.0
        assert inv_val[0].values["2026-01"] == pytest.approx(2500.0)
        assert inv_val[0].starting_stock == pytest.approx(3000.0)


class TestConsolidationRows:
    """Verify the 20 consolidation rows: TURNOVER, COGS, GROSS MARGIN, EBITDA, EBIT, ROCE."""

    def _build_engine(self):
        demand_vals = {"2026-01": 100, "2026-02": 100, "2026-03": 100}
        planning_results = {
            LineType.DEMAND_FORECAST.value: [
                _make_row("M1", LineType.DEMAND_FORECAST.value, demand_vals),
            ],
            LineType.PURCHASE_PLAN.value: [
                _make_row("M1", LineType.PURCHASE_PLAN.value, demand_vals),
            ],
            LineType.TOTAL_DEMAND.value: [
                _make_row("M1", LineType.TOTAL_DEMAND.value, demand_vals),
            ],
            LineType.INVENTORY.value: [
                _make_row("M1", LineType.INVENTORY.value, demand_vals, starting_stock=500),
            ],
            LineType.PURCHASE_RECEIPT.value: [
                _make_row("M1", LineType.PURCHASE_RECEIPT.value, demand_vals),
            ],
            LineType.CAPACITY_UTILIZATION.value: [
                PlanningRow(
                    material_number="Z_MACH01", material_name="PBA11",
                    product_type="Machine", product_family="ZZ_GRP1",
                    spc_product="", product_cluster="", product_name="Machine1",
                    line_type=LineType.CAPACITY_UTILIZATION.value,
                    values={"2026-01": 50, "2026-02": 50, "2026-03": 50},
                ),
            ],
            LineType.FTE_REQUIREMENTS.value: [
                _make_row("ZZ_GRP1", LineType.FTE_REQUIREMENTS.value,
                          {"2026-01": 2.0, "2026-02": 2.0, "2026-03": 2.0}),
            ],
        }
        data = _make_data(
            sales_prices={
                "M1": SalesPriceItem(plant_code="NLX1", product_id="M1",
                                     volume_2025=1000, ex_works_revenue=20000),
            },
            material_costs={
                "M1": RawMaterialCost(plant_code="NLX1", product_code="M1",
                                      product_name="Mat1", cost_per_unit=5.0),
            },
            machine_costs={
                "PBA11": MachineCost(plant_code="NLX1", cost_center="PBA11",
                                     variable_cost_per_hour=100.0),
            },
            stock={"M1": {"Total Stock": 1000, "Total Value": 5000, "Value Unrestricted": 2500}},
            materials={"M1": Material(material_number="M1", name="Mat1",
                                      product_type=ProductType.BULK_PRODUCT,
                                      product_family="FAM")},
        )
        return ValuePlanningEngine(data, planning_results)

    def test_consolidation_count(self):
        engine = self._build_engine()
        results = engine.calculate()
        consol = results.get(LineType.CONSOLIDATION.value, [])
        assert len(consol) == 20

    def test_turnover_row(self):
        engine = self._build_engine()
        results = engine.calculate()
        consol = results[LineType.CONSOLIDATION.value]
        turnover = [r for r in consol if r.material_number == "ZZZZZZ_TURNOVER"][0]
        # price = 20000/1000 = 20, demand = 100, so turnover = 2000 per period
        assert turnover.values["2026-01"] == pytest.approx(2000.0)

    def test_cogs_row(self):
        engine = self._build_engine()
        results = engine.calculate()
        consol = results[LineType.CONSOLIDATION.value]
        cogs = [r for r in consol if r.material_number == "ZZZZZZ_COST OF GOODS"][0]
        # COGS = raw_material + machine + direct_fte + indirect + overhead
        # raw_material = 100 * 5 = 500
        # machine = 50 * 100 = 5000
        # direct_fte = 2 * 5000 = 10000
        # indirect = 3000
        # overhead = 2000
        expected = 500 + 5000 + 10000 + 3000 + 2000
        assert cogs.values["2026-01"] == pytest.approx(expected)

    def test_gross_margin(self):
        engine = self._build_engine()
        results = engine.calculate()
        consol = results[LineType.CONSOLIDATION.value]
        gm = [r for r in consol if r.material_number == "ZZZZZZ_GROSS MARGIN"][0]
        turnover = [r for r in consol if r.material_number == "ZZZZZZ_TURNOVER"][0]
        cogs = [r for r in consol if r.material_number == "ZZZZZZ_COST OF GOODS"][0]
        for p in PERIODS:
            assert gm.values[p] == pytest.approx(turnover.values[p] - cogs.values[p])

    def test_ebitda(self):
        engine = self._build_engine()
        results = engine.calculate()
        consol = results[LineType.CONSOLIDATION.value]
        ebitda = [r for r in consol if r.material_number == "ZZZZZZ_EBITDA"][0]
        gm = [r for r in consol if r.material_number == "ZZZZZZ_GROSS MARGIN"][0]
        sga = [r for r in consol if r.material_number == "ZZZZZZ_SG&A COST"][0]
        for p in PERIODS:
            assert ebitda.values[p] == pytest.approx(gm.values[p] - sga.values[p])

    def test_ebit(self):
        engine = self._build_engine()
        results = engine.calculate()
        consol = results[LineType.CONSOLIDATION.value]
        ebit = [r for r in consol if r.material_number == "ZZZZZZ_EBIT"][0]
        ebitda = [r for r in consol if r.material_number == "ZZZZZZ_EBITDA"][0]
        da = [r for r in consol if r.material_number == "ZZZZZZ_D&A COST"][0]
        for p in PERIODS:
            assert ebit.values[p] == pytest.approx(ebitda.values[p] - da.values[p])

    def test_roce(self):
        engine = self._build_engine()
        results = engine.calculate()
        consol = results[LineType.CONSOLIDATION.value]
        roce = [r for r in consol if r.material_number == "ZZZZZZ_ROCE"][0]
        ebit = [r for r in consol if r.material_number == "ZZZZZZ_EBIT"][0]
        ci = [r for r in consol if r.material_number == "ZZZZZZ_CAPITAL INVESTMENT"][0]
        for p in PERIODS:
            if ci.values[p] != 0:
                expected = ebit.values[p] * 12 / ci.values[p]
                assert roce.values[p] == pytest.approx(expected, rel=1e-4)


class TestTruckFTEValuePlanning:
    """Truck FTE value = CapUtil * TimePerTruck / TonPerTruck * fte_cost."""

    def test_truck_fte_value(self):
        cap_util_row = PlanningRow(
            material_number="ZZZZ_TRUCK01", material_name="Bulk Product",
            product_type="Machine Group", product_family="",
            spc_product="", product_cluster="", product_name="",
            line_type=LineType.CAPACITY_UTILIZATION.value,
            aux_column="2.0", aux_2_column="25.0",
            values={"2026-01": 80, "2026-02": 40, "2026-03": 0},
        )
        fte_row = _make_row(
            "ZZZZ_TRUCK01", LineType.FTE_REQUIREMENTS.value,
            {"2026-01": 0.64, "2026-02": 0.32, "2026-03": 0},
        )
        planning_results = {
            LineType.DEMAND_FORECAST.value: [],
            LineType.PURCHASE_PLAN.value: [],
            LineType.TOTAL_DEMAND.value: [],
            LineType.INVENTORY.value: [],
            LineType.PURCHASE_RECEIPT.value: [],
            LineType.CAPACITY_UTILIZATION.value: [cap_util_row],
            LineType.FTE_REQUIREMENTS.value: [fte_row],
        }
        data = _make_data()
        engine = ValuePlanningEngine(data, planning_results)
        results = engine.calculate()

        fte_val_rows = results[LineType.FTE_REQUIREMENTS.value]
        truck_fte_val = [r for r in fte_val_rows if r.material_number == "ZZZZ_TRUCK01"]
        assert len(truck_fte_val) == 1
        # FTE value = cap_util * time_per_truck / ton_per_truck * fte_cost
        # = 80 * 2.0 / 25.0 * 5000 = 6.4 * 5000 = 32000
        assert truck_fte_val[0].values["2026-01"] == pytest.approx(32000.0)
