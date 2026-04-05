"""Tests for modules/inventory_quality_engine.py — overstock and Top 10 calculation."""
import pytest
from modules.inventory_quality_engine import InventoryQualityEngine
from modules.models import (
    Material, SafetyStockConfig, ProductType, PlanningRow, LineType,
)
from unittest.mock import MagicMock


PERIODS = ["2026-01", "2026-02", "2026-03"]


def _make_inv_row(mat_num, values, starting_stock=0.0):
    return PlanningRow(
        material_number=mat_num, material_name=f"Mat {mat_num}",
        product_type="Bulk Product", product_family="FAM",
        spc_product="", product_cluster="", product_name="",
        line_type=LineType.INVENTORY.value,
        aux_column="5.0",  # unit value
        starting_stock=starting_stock,
        values=dict(values),
    )


def _make_data(safety_stock_configs, stock=None, materials=None):
    data = MagicMock()
    data.periods = PERIODS
    data.safety_stock = safety_stock_configs
    data.stock = stock or {}
    data.materials = materials or {}
    return data


class TestOverstockCalculation:
    """Overstock = inv - safety - strategic - lot when inv >= safety + strategic + lot, else 0."""

    def test_overstock_when_above_threshold(self):
        ss = {"M1": SafetyStockConfig(
            material_number="M1", safety_stock=100, lot_size=50, strategic_stock=50,
        )}
        inv_row = _make_inv_row("M1", {"2026-01": 5000, "2026-02": 1000, "2026-03": 500})
        # unit_value from aux_column = 5.0
        # safety_val = 100*5 = 500, strategic_val = 50*5 = 250, lot_val = 50*5 = 250
        # target = 500+250 = 750, threshold = 750+250 = 1000

        value_results = {
            "04. Inventory": [inv_row],
        }
        data = _make_data(ss, materials={"M1": Material(
            material_number="M1", name="Mat1",
            product_type=ProductType.BULK_PRODUCT, product_family="FAM")})
        engine = InventoryQualityEngine(data, {}, value_results)
        result = engine.calculate()

        per_mat = result["per_material"]
        assert len(per_mat) == 1
        m = per_mat[0]

        # Period 2026-01: inv=5000 >= threshold=1000, overstock=5000-1000=4000
        assert m["periods"]["2026-01"]["overstock"] == pytest.approx(4000.0)
        # Period 2026-02: inv=1000 >= threshold=1000, overstock=1000-1000=0
        assert m["periods"]["2026-02"]["overstock"] == pytest.approx(0.0)
        # Period 2026-03: inv=500 < threshold=1000, overstock=0
        assert m["periods"]["2026-03"]["overstock"] == pytest.approx(0.0)

    def test_under_stock_when_below_target(self):
        ss = {"M1": SafetyStockConfig(
            material_number="M1", safety_stock=200, lot_size=10, strategic_stock=100,
        )}
        inv_row = _make_inv_row("M1", {"2026-01": 500, "2026-02": 0, "2026-03": 0})
        value_results = {"04. Inventory": [inv_row]}
        data = _make_data(ss, materials={"M1": Material(
            material_number="M1", name="Mat1",
            product_type=ProductType.BULK_PRODUCT, product_family="FAM")})
        engine = InventoryQualityEngine(data, {}, value_results)
        result = engine.calculate()

        m = result["per_material"][0]
        # target = (200+100)*5 = 1500
        # inv=500 < 1500 → under = 500 - 1500 = -1000
        assert m["periods"]["2026-01"]["under"] == pytest.approx(-1000.0)
        assert m["periods"]["2026-01"]["overstock"] == pytest.approx(0.0)

    def test_invariant_holds(self):
        """under + safety + strategic + normal + overstock = inventory."""
        ss = {"M1": SafetyStockConfig(
            material_number="M1", safety_stock=100, lot_size=50, strategic_stock=50,
        )}
        inv_row = _make_inv_row("M1", {"2026-01": 3000, "2026-02": 700, "2026-03": 0})
        value_results = {"04. Inventory": [inv_row]}
        data = _make_data(ss, materials={"M1": Material(
            material_number="M1", name="Mat1",
            product_type=ProductType.BULK_PRODUCT, product_family="FAM")})
        engine = InventoryQualityEngine(data, {}, value_results)
        result = engine.calculate()

        m = result["per_material"][0]
        for p in PERIODS:
            pd = m["periods"][p]
            total = pd["under"] + pd["safety"] + pd["strategic"] + pd["normal"] + pd["overstock"]
            assert total == pytest.approx(pd["inventory"], abs=0.02)


class TestTop10Overstocks:
    """Top 10 sorted by total overstock descending."""

    def test_top10_ordering(self):
        ss = {}
        value_rows = []
        materials = {}
        for i in range(15):
            mat_id = f"M{i:02d}"
            ss[mat_id] = SafetyStockConfig(
                material_number=mat_id, safety_stock=10, lot_size=5, strategic_stock=5,
            )
            inv_val = (i + 1) * 1000  # M00=1000, M14=15000
            value_rows.append(_make_inv_row(mat_id, {
                "2026-01": inv_val, "2026-02": inv_val, "2026-03": inv_val,
            }))
            materials[mat_id] = Material(
                material_number=mat_id, name=f"Mat{i}",
                product_type=ProductType.BULK_PRODUCT, product_family="FAM",
            )

        value_results = {"04. Inventory": value_rows}
        data = _make_data(ss, materials=materials)
        engine = InventoryQualityEngine(data, {}, value_results)
        result = engine.calculate()

        top10 = result["top_10_overstocks"]
        assert len(top10) == 10
        # Highest overstock material should be first
        assert top10[0]["total_overstock"] >= top10[1]["total_overstock"]
        assert top10[0]["material_number"] == "M14"


class TestStartingStockOverstock:
    """Starting stock period should also produce overstock values."""

    def test_starting_stock_in_overstock_by_period(self):
        ss = {"M1": SafetyStockConfig(
            material_number="M1", safety_stock=100, lot_size=50, strategic_stock=50,
        )}
        # Starting stock = 5000 (value)
        inv_row = _make_inv_row("M1", {"2026-01": 2000, "2026-02": 0, "2026-03": 0},
                                starting_stock=5000)
        value_results = {"04. Inventory": [inv_row]}
        data = _make_data(ss, materials={"M1": Material(
            material_number="M1", name="Mat1",
            product_type=ProductType.BULK_PRODUCT, product_family="FAM")})
        engine = InventoryQualityEngine(data, {}, value_results)
        result = engine.calculate()

        m = result["per_material"][0]
        # Starting stock overstock: max(0, 5000 - (500+250) - 250) = max(0, 5000 - 1000) = 4000
        assert "Starting stock" in m["overstock_by_period"]
        assert m["overstock_by_period"]["Starting stock"] == pytest.approx(4000.0)


class TestPeriodTotals:
    """Global period totals should sum across all materials."""

    def test_period_totals_sum(self):
        ss = {
            "M1": SafetyStockConfig(material_number="M1", safety_stock=10, lot_size=5, strategic_stock=5),
            "M2": SafetyStockConfig(material_number="M2", safety_stock=20, lot_size=10, strategic_stock=10),
        }
        value_results = {"04. Inventory": [
            _make_inv_row("M1", {"2026-01": 1000, "2026-02": 0, "2026-03": 0}),
            _make_inv_row("M2", {"2026-01": 2000, "2026-02": 0, "2026-03": 0}),
        ]}
        materials = {
            "M1": Material(material_number="M1", name="Mat1",
                           product_type=ProductType.BULK_PRODUCT, product_family="FAM"),
            "M2": Material(material_number="M2", name="Mat2",
                           product_type=ProductType.BULK_PRODUCT, product_family="FAM"),
        }
        data = _make_data(ss, materials=materials)
        engine = InventoryQualityEngine(data, {}, value_results)
        result = engine.calculate()

        pt = result["period_totals"]["2026-01"]
        assert pt["inventory"] == pytest.approx(3000.0)
        # Sum of both materials' overstock
        m1_ov = result["per_material"][0]["periods"]["2026-01"]["overstock"]
        m2_ov = result["per_material"][1]["periods"]["2026-01"]["overstock"]
        assert pt["overstock"] == pytest.approx(m1_ov + m2_ov)
