"""Tests for modules/capacity_engine.py — capacity utilization, FTE, trucks."""
import pytest
from modules.capacity_engine import CapacityEngine
from modules.models import (
    Material, Machine, MachineGroup, RoutingItem,
    ProductType, ShiftSystem, LineType, PlanningRow,
)
from unittest.mock import MagicMock
from collections import defaultdict


PERIODS = ["2026-01", "2026-02", "2026-03"]


def _make_data(
    materials=None, machines=None, machine_groups=None,
    routing=None, periods=None, shift_hours=None,
    fte_hours_per_year=1492, config_site="NLX1",
):
    data = MagicMock()
    data.periods = periods or PERIODS
    data.materials = materials or {}
    data.machines = machines or {}
    data.machine_groups = machine_groups or {}
    data.routing = routing or {}
    data.shift_hours = shift_hours or {"3-shift system": 520.0, "2-shift system": 347.0}
    data.fte_hours_per_year = fte_hours_per_year
    data.bom = []
    data.default_shift_name = "3-shift system"
    data.config = MagicMock()
    data.config.site = config_site
    data.config.unlimited_capacity_machine = []
    data.get_all_routings = lambda m: data.routing.get(m, [])
    return data


class TestCapacityUtilizationMaterialLevel:
    """Line 07: cap_util = production_qty / (base_qty / std_time)."""

    def test_basic_cap_util(self):
        routing = {
            "M1": [RoutingItem(
                plant="NLX1", material="M1", material_description="Mat1",
                work_center="PBA11", base_quantity=100, standard_time=10
            )]
        }
        machines = {
            "PBA11": Machine(machine_id="Z_MACH01", machine_code="PBA11",
                             name="Machine1", oee=0.85, machine_group="ZZ_GRP1",
                             availability_by_period={p: 1.0 for p in PERIODS},
                             shift_system=ShiftSystem.THREE_SHIFT),
        }
        machine_groups = {
            "ZZ_GRP1": MachineGroup(group_id="ZZ_GRP1", machine_codes=["PBA11"]),
        }
        materials = {
            "M1": Material(material_number="M1", name="Mat1",
                           product_type=ProductType.BULK_PRODUCT, product_family="FAM"),
            "ZZ_GRP1": Material(material_number="ZZ_GRP1", name="Group1",
                                product_type=ProductType.OTHER, product_family="",
                                fte_requirements=1.0),
        }
        data = _make_data(
            materials=materials, machines=machines,
            machine_groups=machine_groups, routing=routing,
        )
        production_plan = {"M1": {"2026-01": 500, "2026-02": 0, "2026-03": 0}}
        engine = CapacityEngine(data, production_plan)
        results = engine.calculate()

        cap_util_rows = results[LineType.CAPACITY_UTILIZATION.value]
        # Find the material-level row
        mat_rows = [r for r in cap_util_rows
                    if r.material_number == "M1" and r.line_type == LineType.CAPACITY_UTILIZATION.value]
        assert len(mat_rows) == 1
        # AUX2 = base_qty / std_time = 100 / 10 = 10
        assert float(mat_rows[0].aux_2_column) == pytest.approx(10.0)
        # Cap util = 500 / 10 = 50 hours
        assert mat_rows[0].values["2026-01"] == pytest.approx(50.0)


class TestCapacityUtilizationOEEAdjustment:
    """Machine-level cap util = sum(material hours) / OEE."""

    def test_oee_adjustment(self):
        routing = {
            "M1": [RoutingItem(
                plant="NLX1", material="M1", material_description="Mat1",
                work_center="PBA11", base_quantity=100, standard_time=10
            )]
        }
        machines = {
            "PBA11": Machine(machine_id="Z_MACH01", machine_code="PBA11",
                             name="Machine1", oee=0.80, machine_group="ZZ_GRP1",
                             availability_by_period={p: 1.0 for p in PERIODS},
                             shift_system=ShiftSystem.THREE_SHIFT),
        }
        machine_groups = {
            "ZZ_GRP1": MachineGroup(group_id="ZZ_GRP1", machine_codes=["PBA11"]),
        }
        materials = {
            "M1": Material(material_number="M1", name="Mat1",
                           product_type=ProductType.BULK_PRODUCT, product_family="FAM"),
            "ZZ_GRP1": Material(material_number="ZZ_GRP1", name="Group1",
                                product_type=ProductType.OTHER, product_family="",
                                fte_requirements=1.0),
        }
        data = _make_data(
            materials=materials, machines=machines,
            machine_groups=machine_groups, routing=routing,
        )
        production_plan = {"M1": {"2026-01": 500, "2026-02": 0, "2026-03": 0}}
        engine = CapacityEngine(data, production_plan)
        results = engine.calculate()

        cap_util_rows = results[LineType.CAPACITY_UTILIZATION.value]
        # Machine-level row: material_name = machine_code
        machine_rows = [r for r in cap_util_rows if r.material_name == "PBA11"]
        assert len(machine_rows) == 1
        # Raw hours = 500/10 = 50, OEE-adjusted = 50/0.80 = 62.5
        assert machine_rows[0].values["2026-01"] == pytest.approx(62.5)


class TestUtilizationRate:
    """Line 10: util_rate = cap_util / (shift_hours * availability)."""

    def test_utilization_rate(self):
        routing = {
            "M1": [RoutingItem(
                plant="NLX1", material="M1", material_description="Mat1",
                work_center="PBA11", base_quantity=100, standard_time=10
            )]
        }
        machines = {
            "PBA11": Machine(machine_id="Z_MACH01", machine_code="PBA11",
                             name="Machine1", oee=0.80, machine_group="ZZ_GRP1",
                             availability_by_period={p: 1.0 for p in PERIODS},
                             shift_system=ShiftSystem.THREE_SHIFT),
        }
        machine_groups = {
            "ZZ_GRP1": MachineGroup(group_id="ZZ_GRP1", machine_codes=["PBA11"]),
        }
        materials = {
            "M1": Material(material_number="M1", name="Mat1",
                           product_type=ProductType.BULK_PRODUCT, product_family="FAM"),
            "ZZ_GRP1": Material(material_number="ZZ_GRP1", name="Group1",
                                product_type=ProductType.OTHER, product_family="",
                                fte_requirements=1.0),
        }
        data = _make_data(
            materials=materials, machines=machines,
            machine_groups=machine_groups, routing=routing,
        )
        production_plan = {"M1": {"2026-01": 500, "2026-02": 0, "2026-03": 0}}
        engine = CapacityEngine(data, production_plan)
        results = engine.calculate()

        util_rows = results[LineType.UTILIZATION_RATE.value]
        row = util_rows[0]
        # cap_util(OEE-adjusted) = 50/0.80 = 62.5
        # denom = shift_hours * availability = 520 * 1.0 = 520
        # util_rate = 62.5 / 520
        expected = 62.5 / 520.0
        assert row.values["2026-01"] == pytest.approx(expected, rel=1e-4)


class TestTruckFTE:
    """Truck FTE = CapacityUtilization * TimePerTruck / TonPerTruck.

    ZZZZ_TRUCK01/02: FTE = sum(qty * time_per_truck / ton_per_truck) / (fte_hrs/12)
    """

    def test_truck_capacity_utilization(self):
        """Test truck hours: sum(demand * time_per_truck / ton_per_truck)."""
        materials = {
            "MAT1": Material(
                material_number="MAT1", name="Bulk Product",
                product_type=ProductType.BULK_PRODUCT, product_family="FAM",
            ),
            "ZZZZ_TRUCK01": Material(
                material_number="ZZZZ_TRUCK01", name="Bulk Product",
                product_type=ProductType.OTHER, product_family="",
                ton_per_truck=25.0, time_per_truck=2.0,
                product_type_raw="01. Demand forecast",
            ),
        }
        machines = {}
        data = _make_data(materials=materials, machines=machines)

        # all_line_data provides the source for truck SUMIFS
        all_line_data = {
            "01. Demand forecast": {
                "MAT1": {"2026-01": 1000, "2026-02": 500, "2026-03": 0},
            }
        }
        production_plan = {}
        engine = CapacityEngine(data, production_plan, all_line_data)
        results = engine.calculate()

        cap_util_rows = results[LineType.CAPACITY_UTILIZATION.value]
        truck_rows = [r for r in cap_util_rows if r.material_number == "ZZZZ_TRUCK01"]
        assert len(truck_rows) == 1
        # hours = (1000 / 25) * 2 = 80
        assert truck_rows[0].values["2026-01"] == pytest.approx(80.0)
        # hours = (500 / 25) * 2 = 40
        assert truck_rows[0].values["2026-02"] == pytest.approx(40.0)

    def test_truck_fte_requirements(self):
        """FTE for trucks = truck_hours * fte_coeff / (fte_hrs_per_year / 12)."""
        materials = {
            "MAT1": Material(
                material_number="MAT1", name="Bulk Product",
                product_type=ProductType.BULK_PRODUCT, product_family="FAM",
            ),
            "ZZZZ_TRUCK01": Material(
                material_number="ZZZZ_TRUCK01", name="Bulk Product",
                product_type=ProductType.OTHER, product_family="",
                ton_per_truck=25.0, time_per_truck=2.0, fte_requirements=1.0,
                product_type_raw="01. Demand forecast",
            ),
        }
        data = _make_data(materials=materials, fte_hours_per_year=1492)

        all_line_data = {
            "01. Demand forecast": {
                "MAT1": {"2026-01": 1000, "2026-02": 0, "2026-03": 0},
            }
        }
        engine = CapacityEngine(data, {}, all_line_data)
        results = engine.calculate()

        fte_rows = results[LineType.FTE_REQUIREMENTS.value]
        truck_fte = [r for r in fte_rows if r.material_number == "ZZZZ_TRUCK01"]
        assert len(truck_fte) == 1
        # truck_hours = (1000/25)*2 = 80
        # fte = 80 * 1.0 / (1492/12) = 80 / 124.33... = 0.6434...
        expected = 80.0 * 1.0 / (1492 / 12)
        assert truck_fte[0].values["2026-01"] == pytest.approx(expected, rel=1e-3)


class TestFTERequirementsGroup:
    """FTE per group = group_hours * fte_coeff / (fte_hrs/12)."""

    def test_group_fte(self):
        routing = {
            "M1": [RoutingItem(
                plant="NLX1", material="M1", material_description="Mat1",
                work_center="PBA11", base_quantity=100, standard_time=10
            )]
        }
        machines = {
            "PBA11": Machine(machine_id="Z_MACH01", machine_code="PBA11",
                             name="Machine1", oee=1.0, machine_group="ZZ_GRP1",
                             availability_by_period={p: 1.0 for p in PERIODS},
                             shift_system=ShiftSystem.THREE_SHIFT),
        }
        machine_groups = {
            "ZZ_GRP1": MachineGroup(group_id="ZZ_GRP1", machine_codes=["PBA11"]),
        }
        materials = {
            "M1": Material(material_number="M1", name="Mat1",
                           product_type=ProductType.BULK_PRODUCT, product_family="FAM"),
            "ZZ_GRP1": Material(material_number="ZZ_GRP1", name="Group1",
                                product_type=ProductType.OTHER, product_family="",
                                fte_requirements=2.0),
        }
        data = _make_data(
            materials=materials, machines=machines,
            machine_groups=machine_groups, routing=routing,
            fte_hours_per_year=1200,
        )
        production_plan = {"M1": {"2026-01": 1000, "2026-02": 0, "2026-03": 0}}
        engine = CapacityEngine(data, production_plan)
        results = engine.calculate()

        fte_rows = results[LineType.FTE_REQUIREMENTS.value]
        grp_fte = [r for r in fte_rows if r.material_number == "ZZ_GRP1"]
        assert len(grp_fte) == 1
        # raw hours = 1000/10 = 100; OEE=1.0 so adjusted = 100
        # FTE = 100 * 2.0 / (1200/12) = 200 / 100 = 2.0
        assert grp_fte[0].values["2026-01"] == pytest.approx(2.0)


class TestShiftAvailability:
    """Line 11: shift hours per group."""

    def test_shift_availability_row(self):
        machines = {
            "PBA11": Machine(machine_id="Z_MACH01", machine_code="PBA11",
                             name="Machine1", oee=0.85, machine_group="ZZ_GRP1",
                             availability_by_period={p: 1.0 for p in PERIODS},
                             shift_system=ShiftSystem.THREE_SHIFT),
        }
        machine_groups = {
            "ZZ_GRP1": MachineGroup(group_id="ZZ_GRP1", machine_codes=["PBA11"]),
        }
        materials = {
            "ZZ_GRP1": Material(material_number="ZZ_GRP1", name="Group1",
                                product_type=ProductType.OTHER, product_family="",
                                fte_requirements=1.0),
        }
        data = _make_data(materials=materials, machines=machines,
                          machine_groups=machine_groups)
        engine = CapacityEngine(data, {})
        results = engine.calculate()

        shift_rows = results[LineType.SHIFT_AVAILABILITY.value]
        assert len(shift_rows) == 1
        row = shift_rows[0]
        assert row.values["2026-01"] == 520.0
        assert row.aux_column == "3-shift system"
