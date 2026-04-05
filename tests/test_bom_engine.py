"""Tests for modules/bom_engine.py — BOM explosion and dependent demand cascading."""
import pytest
from modules.bom_engine import BOMEngine
from modules.models import (
    Material, BOMItem, ProductType, PlanningRow, LineType,
)
from unittest.mock import MagicMock


def _make_data(bom_items, materials, periods):
    """Build minimal DataLoader-like mock."""
    data = MagicMock()
    data.bom = bom_items
    data.materials = materials
    data.periods = periods
    data.bom_levels = {}
    data.get_materials_at_level = MagicMock(side_effect=lambda lvl: [
        m for m, l in data.bom_levels.items() if l == lvl
    ])
    data.get_max_bom_level = MagicMock(return_value=max(data.bom_levels.values()) if data.bom_levels else 0)
    return data


PERIODS = ["2026-01", "2026-02", "2026-03"]

# Parent P1 -> Child C1 (qty_per=0.5), Child C2 (qty_per=2.0)
BOM = [
    BOMItem(plant="NLX1", parent_material="P1", parent_name="Parent1",
            component_material="C1", component_name="Child1",
            quantity_per=0.5, bom_header_quantity=1.0),
    BOMItem(plant="NLX1", parent_material="P1", parent_name="Parent1",
            component_material="C2", component_name="Child2",
            quantity_per=2.0, bom_header_quantity=1.0),
]

MATERIALS = {
    "P1": Material(material_number="P1", name="Parent1",
                   product_type=ProductType.BULK_PRODUCT, product_family="FAM"),
    "C1": Material(material_number="C1", name="Child1",
                   product_type=ProductType.RAW_MATERIAL, product_family="FAM"),
    "C2": Material(material_number="C2", name="Child2",
                   product_type=ProductType.PACKAGING_GOODS, product_family="FAM"),
}


class TestBOMEngineComputeDependentRequirements:
    def test_basic_explosion(self):
        data = _make_data(BOM, MATERIALS, PERIODS)
        data.bom_levels = {"P1": 0, "C1": 1, "C2": 1}
        engine = BOMEngine(data)

        production_plan = {"2026-01": 100, "2026-02": 200, "2026-03": 0}
        result = engine.compute_dependent_requirements("P1", production_plan)

        assert "C1" in result
        assert "C2" in result
        # C1: qty_per=0.5 => 100*0.5=50, 200*0.5=100, 0*0.5=0
        assert result["C1"]["2026-01"] == pytest.approx(50.0)
        assert result["C1"]["2026-02"] == pytest.approx(100.0)
        assert result["C1"]["2026-03"] == pytest.approx(0.0)
        # C2: qty_per=2.0 => 100*2=200, 200*2=400, 0*2=0
        assert result["C2"]["2026-01"] == pytest.approx(200.0)
        assert result["C2"]["2026-02"] == pytest.approx(400.0)

    def test_no_children(self):
        data = _make_data([], MATERIALS, PERIODS)
        data.bom_levels = {"P1": 0}
        engine = BOMEngine(data)
        result = engine.compute_dependent_requirements("P1", {"2026-01": 100})
        assert result == {}


class TestBOMEngineDependentDemandRows:
    def test_create_rows(self):
        data = _make_data(BOM, MATERIALS, PERIODS)
        data.bom_levels = {"P1": 0, "C1": 1, "C2": 1}
        engine = BOMEngine(data)

        demand_by_parent = {
            "P1": {"2026-01": 50, "2026-02": 100, "2026-03": 0}
        }
        rows = engine.create_dependent_demand_rows("C1", demand_by_parent)
        assert len(rows) == 1
        row = rows[0]
        assert row.material_number == "C1"
        assert row.line_type == LineType.DEPENDENT_DEMAND.value
        assert row.aux_column == "P1"
        assert row.values["2026-01"] == 50.0

    def test_multiple_parents(self):
        """A child with two parents gets two Line 02 rows."""
        bom = list(BOM) + [
            BOMItem(plant="NLX1", parent_material="P2", parent_name="Parent2",
                    component_material="C1", component_name="Child1",
                    quantity_per=3.0, bom_header_quantity=1.0),
        ]
        materials = dict(MATERIALS)
        materials["P2"] = Material(material_number="P2", name="Parent2",
                                   product_type=ProductType.BULK_PRODUCT, product_family="FAM")
        data = _make_data(bom, materials, PERIODS)
        data.bom_levels = {"P1": 0, "P2": 0, "C1": 1, "C2": 1}
        engine = BOMEngine(data)

        demand_by_parent = {
            "P1": {"2026-01": 10, "2026-02": 0, "2026-03": 0},
            "P2": {"2026-01": 20, "2026-02": 0, "2026-03": 0},
        }
        rows = engine.create_dependent_demand_rows("C1", demand_by_parent)
        assert len(rows) == 2
        aux_values = {r.aux_column for r in rows}
        assert aux_values == {"P1", "P2"}


class TestBOMEngineDependentRequirementsRows:
    def test_create_line_08_rows(self):
        data = _make_data(BOM, MATERIALS, PERIODS)
        data.bom_levels = {"P1": 0, "C1": 1, "C2": 1}
        engine = BOMEngine(data)

        children_demand = {
            "C1": {"2026-01": 50, "2026-02": 100, "2026-03": 0},
            "C2": {"2026-01": 200, "2026-02": 400, "2026-03": 0},
        }
        rows = engine.create_dependent_requirements_rows("P1", children_demand)
        assert len(rows) == 2
        for row in rows:
            assert row.material_number == "P1"
            assert row.line_type == LineType.DEPENDENT_REQUIREMENTS.value
        # Check aux column is child, aux_2 is qty_per
        c1_row = [r for r in rows if r.aux_column == "C1"][0]
        assert c1_row.aux_2_column == "0.5"


class TestBOMCascadingTwoLevels:
    """Test cascading: P1 -> C1 -> GC1 (grandchild)."""

    def test_two_level_cascade(self):
        bom = [
            BOMItem(plant="NLX1", parent_material="P1", parent_name="Parent",
                    component_material="C1", component_name="Child",
                    quantity_per=2.0, bom_header_quantity=1.0),
            BOMItem(plant="NLX1", parent_material="C1", parent_name="Child",
                    component_material="GC1", component_name="Grandchild",
                    quantity_per=3.0, bom_header_quantity=1.0),
        ]
        materials = {
            "P1": Material(material_number="P1", name="Parent",
                           product_type=ProductType.BULK_PRODUCT, product_family="FAM"),
            "C1": Material(material_number="C1", name="Child",
                           product_type=ProductType.BULK_PRODUCT, product_family="FAM"),
            "GC1": Material(material_number="GC1", name="Grandchild",
                            product_type=ProductType.RAW_MATERIAL, product_family="FAM"),
        }
        data = _make_data(bom, materials, PERIODS)
        data.bom_levels = {"P1": 0, "C1": 1, "GC1": 2}
        engine = BOMEngine(data)

        # Level 0: P1 produces 100 in Jan
        p1_prod = {"2026-01": 100, "2026-02": 0, "2026-03": 0}
        level0_deps = engine.compute_dependent_requirements("P1", p1_prod)
        assert level0_deps["C1"]["2026-01"] == pytest.approx(200.0)

        # Level 1: C1 produces 200 in Jan (matching demand)
        c1_prod = {"2026-01": 200, "2026-02": 0, "2026-03": 0}
        level1_deps = engine.compute_dependent_requirements("C1", c1_prod)
        # GC1 needs 200 * 3.0 = 600
        assert level1_deps["GC1"]["2026-01"] == pytest.approx(600.0)
