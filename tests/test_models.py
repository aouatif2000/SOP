"""Tests for modules/models.py — data model structures."""
import pytest
from modules.models import (
    ProductType, LineType, ShiftSystem, SHIFT_HOURS, FTE_HOURS_PER_YEAR,
    Material, BOMItem, RoutingItem, Machine, MachineGroup,
    SafetyStockConfig, PlanningConfig, PlanningRow, ValuationParameters,
    SalesPriceItem, RawMaterialCost, MachineCost,
)
from datetime import datetime


class TestProductType:
    def test_from_string_raw(self):
        assert ProductType.from_string("Raw Material") == ProductType.RAW_MATERIAL

    def test_from_string_bulk(self):
        assert ProductType.from_string("Bulk Product") == ProductType.BULK_PRODUCT

    def test_from_string_packaged(self):
        assert ProductType.from_string("Packaged Product") == ProductType.PACKAGED_PRODUCT

    def test_from_string_packed(self):
        assert ProductType.from_string("Packed material") == ProductType.PACKAGED_PRODUCT

    def test_from_string_packaging(self):
        assert ProductType.from_string("Packaging Goods") == ProductType.PACKAGING_GOODS

    def test_from_string_other(self):
        assert ProductType.from_string("unknown") == ProductType.OTHER

    def test_from_string_empty(self):
        assert ProductType.from_string("") == ProductType.OTHER


class TestMaterial:
    def _make(self, product_type=ProductType.BULK_PRODUCT):
        return Material(
            material_number="MAT001",
            name="Test Material",
            product_type=product_type,
            product_family="FAM1",
        )

    def test_is_purchased_raw(self):
        m = self._make(ProductType.RAW_MATERIAL)
        assert m.is_purchased is True
        assert m.is_produced is False

    def test_is_purchased_packaging(self):
        m = self._make(ProductType.PACKAGING_GOODS)
        assert m.is_purchased is True

    def test_is_produced_bulk(self):
        m = self._make(ProductType.BULK_PRODUCT)
        assert m.is_produced is True
        assert m.is_purchased is False

    def test_is_produced_packaged(self):
        m = self._make(ProductType.PACKAGED_PRODUCT)
        assert m.is_produced is True

    def test_other_neither(self):
        m = self._make(ProductType.OTHER)
        assert m.is_purchased is False
        assert m.is_produced is False


class TestRoutingItem:
    def test_time_per_unit(self):
        ri = RoutingItem(
            plant="NLX1", material="MAT1", material_description="Test",
            work_center="PBA11", base_quantity=100.0, standard_time=50.0,
        )
        assert ri.time_per_unit == pytest.approx(0.5)

    def test_time_per_unit_zero_base(self):
        ri = RoutingItem(
            plant="NLX1", material="MAT1", material_description="Test",
            work_center="PBA11", base_quantity=0.0, standard_time=50.0,
        )
        assert ri.time_per_unit == 0.0


class TestMachine:
    def test_get_availability_default(self):
        m = Machine(machine_id="1", machine_code="PBA11", name="Test", oee=0.85)
        assert m.get_availability("2026-01") == 1.0

    def test_get_availability_custom(self):
        m = Machine(machine_id="1", machine_code="PBA11", name="Test", oee=0.85,
                    availability_by_period={"2026-01": 0.9})
        assert m.get_availability("2026-01") == 0.9

    def test_get_available_hours(self):
        m = Machine(machine_id="1", machine_code="PBA11", name="Test", oee=0.85,
                    shift_system=ShiftSystem.THREE_SHIFT,
                    availability_by_period={"2026-01": 1.0})
        hours = m.get_available_hours("2026-01")
        expected = SHIFT_HOURS[ShiftSystem.THREE_SHIFT] * 0.85 * 1.0
        assert hours == pytest.approx(expected)


class TestPlanningConfig:
    def test_get_periods(self):
        cfg = PlanningConfig(initial_date=datetime(2026, 1, 1), forecast_months=3)
        assert cfg.get_periods() == ["2026-01", "2026-02", "2026-03"]

    def test_get_periods_year_wrap(self):
        cfg = PlanningConfig(initial_date=datetime(2025, 11, 1), forecast_months=4)
        assert cfg.get_periods() == ["2025-11", "2025-12", "2026-01", "2026-02"]


class TestPlanningRow:
    def test_get_set_value(self):
        row = PlanningRow(
            material_number="MAT1", material_name="Test", product_type="Bulk",
            product_family="", spc_product="", product_cluster="",
            product_name="", line_type="01. Demand forecast",
        )
        row.set_value("2026-01", 100.0)
        assert row.get_value("2026-01") == 100.0
        assert row.get_value("2026-02") == 0.0

    def test_to_dict(self):
        row = PlanningRow(
            material_number="MAT1", material_name="Test", product_type="Bulk",
            product_family="FAM", spc_product="", product_cluster="",
            product_name="", line_type="01. Demand forecast",
            values={"2026-01": 50.0},
        )
        d = row.to_dict()
        assert d["material_number"] == "MAT1"
        assert d["values"]["2026-01"] == 50.0


class TestSalesPriceItem:
    def test_price_per_unit(self):
        sp = SalesPriceItem(plant_code="NLX1", product_id="P1",
                            volume_2025=200.0, ex_works_revenue=1000.0)
        assert sp.price_per_unit == pytest.approx(5.0)

    def test_price_per_unit_zero_volume(self):
        sp = SalesPriceItem(plant_code="NLX1", product_id="P1",
                            volume_2025=0.0, ex_works_revenue=1000.0)
        assert sp.price_per_unit == 0.0
