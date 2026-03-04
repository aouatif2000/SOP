"""
S&OP Planning Engine - Capacity Engine

VBA Logic (from PDF):
- Line 07 Cap Util: AUX1=work_center, AUX2=BaseQty/StdValue. Values=ProductionPlan/AUX2
  Rows generated for ALL routing entries (not just OEE machines).
  Machine-level rows (Z_MACHxx): AUX1=group, AUX2=OEE. Values=SUM of material-level rows.
  Group-level rows: SUM of machine-level rows in group.
- Line 09 Avail Cap: AUX1=shift_hours, AUX2=avg_availability. Values=availability factor per period.
- Line 10 Util Rate: = Cap Util / (Shift Hours × Availability). NO OEE in denominator!
- Line 11 Shift Avail: 520 hours per group.
- Line 12 FTE: hours / (FTE_hours_per_year / 12)
"""

from typing import Dict, List
from collections import defaultdict
from modules.models import PlanningRow, LineType, ShiftSystem, SHIFT_HOURS, FTE_HOURS_PER_YEAR
from modules.data_loader import DataLoader


class CapacityEngine:
    def __init__(self, data: DataLoader, production_plan: Dict[str, Dict[str, float]]):
        self.data = data
        self.periods = data.periods
        self.production_plan = production_plan

        self.machine_hours_used: Dict[str, Dict[str, float]] = {}
        self.rows_07_cap: List[PlanningRow] = []
        self.rows_09: List[PlanningRow] = []
        self.rows_10: List[PlanningRow] = []
        self.rows_11: List[PlanningRow] = []
        self.rows_12: List[PlanningRow] = []

        # All groups from material master (including empty ones)
        self.all_groups: List[str] = []
        for mn in sorted(self.data.materials.keys()):
            if mn.startswith('ZZ') and not mn.startswith('ZZZZ'):
                self.all_groups.append(mn)

    def calculate(self) -> Dict[str, List[PlanningRow]]:
        self._calculate_capacity_utilization()
        self._calculate_shift_availability()
        self._calculate_available_capacity()
        self._calculate_utilization_rate()
        self._calculate_fte_requirements()

        return {
            LineType.CAPACITY_UTILIZATION.value: self.rows_07_cap,
            LineType.AVAILABLE_CAPACITY.value: self.rows_09,
            LineType.UTILIZATION_RATE.value: self.rows_10,
            LineType.SHIFT_AVAILABILITY.value: self.rows_11,
            LineType.FTE_REQUIREMENTS.value: self.rows_12,
        }

    def _calculate_capacity_utilization(self):
        """Line 07: Capacity Utilization.
        VBA: CapUtil = ProductionPlan / AUX2 where AUX2 = BaseQty/StdValue
        Generate rows for ALL routing entries, not just OEE machines.
        """
        print("  [07] Calculating Capacity Utilization...")

        # Initialize machine hours for OEE machines
        for mc in self.data.machines:
            self.machine_hours_used[mc] = {p: 0.0 for p in self.periods}

        # 1. Material-level rows: one per (material, routing entry)
        for mat_num, plan_data in self.production_plan.items():
            routings = self.data.get_all_routings(mat_num)
            if not routings:
                continue
            material = self.data.materials.get(mat_num)
            if not material:
                continue

            for routing in routings:
                wc = routing.work_center
                base_qty = routing.base_quantity if routing.base_quantity > 0 else 1.0
                std_time = routing.standard_time if routing.standard_time > 0 else 1.0

                # AUX2 = BaseQty / StdValue (throughput rate)
                aux2_val = base_qty / std_time if std_time > 0 else 1.0

                hours_data = {}
                for period in self.periods:
                    prod_qty = plan_data.get(period, 0.0)
                    if prod_qty > 0:
                        # CapUtil = ProductionPlan / AUX2
                        hours = prod_qty / aux2_val
                        hours_data[period] = hours
                        # Track hours for OEE machines
                        if wc in self.machine_hours_used:
                            self.machine_hours_used[wc][period] += hours
                    else:
                        hours_data[period] = 0.0

                # VBA generates cap util rows for ALL routing entries, even if production is 0
                self.rows_07_cap.append(PlanningRow(
                        material_number=mat_num,
                        material_name=material.name,
                        product_type=material.product_type.value,
                        product_family=material.product_family,
                        spc_product=material.spc_product or '',
                        product_cluster=material.product_cluster or '',
                        product_name=material.product_name or '',
                        line_type=LineType.CAPACITY_UTILIZATION.value,
                        aux_column=wc,
                        aux_2_column=str(aux2_val),
                        values=hours_data.copy()
                    ))

        # 2. Machine-level aggregation rows (Z_MACHxx)
        for mc_code, machine in self.data.machines.items():
            mid = machine.machine_id
            group = machine.machine_group
            oee = machine.oee
            hours = self.machine_hours_used.get(mc_code, {p: 0.0 for p in self.periods})
            self.rows_07_cap.append(PlanningRow(
                material_number=mid,
                material_name=mc_code,
                product_type='Machine', product_family=group or '',
                spc_product='', product_cluster='', product_name=machine.name,
                line_type=LineType.CAPACITY_UTILIZATION.value,
                aux_column=group if group else None,
                aux_2_column=str(oee),
                values=dict(hours)
            ))

        # 3. Group-level aggregation rows
        group_hours = defaultdict(lambda: {p: 0.0 for p in self.periods})
        for mc_code, machine in self.data.machines.items():
            grp = machine.machine_group
            if grp:
                for p in self.periods:
                    group_hours[grp][p] += self.machine_hours_used.get(mc_code, {}).get(p, 0.0)

        for grp_id in self.all_groups:
            hours = group_hours.get(grp_id, {p: 0.0 for p in self.periods})
            self.rows_07_cap.append(PlanningRow(
                material_number=grp_id,
                material_name='', product_type='Machine Group',
                product_family='', spc_product='', product_cluster='', product_name='',
                line_type=LineType.CAPACITY_UTILIZATION.value,
                values=dict(hours)
            ))

        # 4. Truck capacity utilization
        self._calculate_truck_cap_util()

        # 5. Control room capacity utilization
        self._calculate_control_room_cap_util()

        print(f"       -> {len(self.rows_07_cap)} capacity utilization rows")

    def _calculate_truck_cap_util(self):
        truck_hours = {}
        for mat_num, plan_data in self.production_plan.items():
            material = self.data.materials.get(mat_num)
            if not material:
                continue
            truck_mat = None
            if material.product_type.value == 'Bulk Product':
                truck_mat = 'ZZZZ_TRUCK01'
            elif material.product_type.value == 'Packaged Product':
                truck_mat = 'ZZZZ_TRUCK02'
            if not truck_mat:
                continue
            truck_material = self.data.materials.get(truck_mat)
            if not truck_material or not truck_material.ton_per_truck or truck_material.ton_per_truck <= 0:
                continue
            if truck_mat not in truck_hours:
                truck_hours[truck_mat] = {p: 0.0 for p in self.periods}
            for period in self.periods:
                qty = plan_data.get(period, 0.0)
                if qty > 0:
                    trucks = qty / truck_material.ton_per_truck
                    hours = trucks * (truck_material.time_per_truck or 0)
                    truck_hours[truck_mat][period] += hours

        for truck_mat, hours in truck_hours.items():
            tm = self.data.materials.get(truck_mat)
            if tm:
                self.rows_07_cap.append(PlanningRow(
                    material_number=truck_mat, material_name=tm.name,
                    product_type='Machine Group', product_family='',
                    spc_product='', product_cluster='', product_name='',
                    line_type=LineType.CAPACITY_UTILIZATION.value,
                    aux_column=str(tm.time_per_truck) if tm.time_per_truck else None,
                    aux_2_column=str(tm.ton_per_truck) if tm.ton_per_truck else None,
                    values=dict(hours)
                ))

    def _calculate_control_room_cap_util(self):
        cr_mat = self.data.materials.get('ZZZZZ_CONTROLROOM')
        if cr_mat:
            shift_hours = 520.0
            self.rows_07_cap.append(PlanningRow(
                material_number='ZZZZZ_CONTROLROOM',
                material_name='Control room operators',
                product_type='Machine Group', product_family='',
                spc_product='', product_cluster='', product_name='',
                line_type=LineType.CAPACITY_UTILIZATION.value,
                aux_column='3-shift system',
                aux_2_column=str(shift_hours * 12),
                values={p: shift_hours for p in self.periods}
            ))

    def _calculate_shift_availability(self):
        """Line 11: Shift Availability per machine group."""
        print("  [11] Calculating Shift Availability...")
        shift_hours = 520.0
        for group_id in self.all_groups:
            group = self.data.machine_groups.get(group_id)
            machine_names = []
            if group:
                for mc in group.machine_codes:
                    m = self.data.machines.get(mc)
                    if m:
                        machine_names.append(m.machine_code)
            self.rows_11.append(PlanningRow(
                material_number=group_id,
                material_name=';'.join(machine_names) if machine_names else '',
                product_type='Machine Group', product_family='',
                spc_product='', product_cluster='', product_name='',
                line_type=LineType.SHIFT_AVAILABILITY.value,
                aux_column='3-shift system',
                values={p: shift_hours for p in self.periods}
            ))
        print(f"       -> {len(self.rows_11)} shift availability rows")

    def _calculate_available_capacity(self):
        """Line 09: Available Capacity.
        AUX1 = shift hours or 'Unlimited'. AUX2 = average OEE.
        Values = availability factor from OEE sheet.
        """
        print("  [09] Calculating Available Capacity...")
        for machine_code, machine in self.data.machines.items():
            avail_data = {}
            if machine.shift_system == ShiftSystem.UNLIMITED:
                avail_data = {p: 1.0 for p in self.periods}
                aux_col = 'Unlimited'
            else:
                for period in self.periods:
                    avail_data[period] = machine.get_availability(period)
                aux_col = '520'
            vals = list(avail_data.values())
            avg_avail = sum(vals) / len(vals) if vals else 1.0
            self.rows_09.append(PlanningRow(
                material_number=machine.machine_id,
                material_name=machine.machine_code,
                product_type='Machine', product_family=machine.machine_group or '',
                spc_product='', product_cluster='', product_name=machine.name,
                line_type=LineType.AVAILABLE_CAPACITY.value,
                aux_column=aux_col,
                aux_2_column=str(avg_avail),
                values=avail_data.copy()
            ))
        print(f"       -> {len(self.rows_09)} available capacity rows")

    def _calculate_utilization_rate(self):
        """Line 10: Utilization Rate.
        VBA: = Capacity Utilization / (Shift Hours × Machine Availability)
        NO OEE in denominator!
        """
        print("  [10] Calculating Utilization Rate...")
        base_shift_hours = 520.0
        for machine_code, machine in self.data.machines.items():
            rate_data = {}
            used = self.machine_hours_used.get(machine_code, {})
            for period in self.periods:
                if machine.shift_system == ShiftSystem.UNLIMITED:
                    rate_data[period] = 1.0
                else:
                    avail_factor = machine.get_availability(period)
                    # VBA: cap_util / (shift_hours * availability) - NO OEE!
                    available = base_shift_hours * avail_factor
                    used_hours = used.get(period, 0.0)
                    rate_data[period] = used_hours / available if available > 0 else 0.0
            vals = list(rate_data.values())
            avg_rate = sum(vals) / len(vals) if vals else 0.0
            self.rows_10.append(PlanningRow(
                material_number=machine.machine_id,
                material_name=machine.machine_code,
                product_type='Machine', product_family=machine.machine_group or '',
                spc_product='', product_cluster='', product_name=machine.name,
                line_type=LineType.UTILIZATION_RATE.value,
                aux_column=str(avg_rate),
                values=rate_data.copy()
            ))
        print(f"       -> {len(self.rows_10)} utilization rate rows")

    def _calculate_fte_requirements(self):
        """Line 12: FTE Requirements per group + trucks + control room."""
        print("  [12] Calculating FTE Requirements...")
        fte_per_month = self.data.fte_hours_per_year / 12
        group_hours = defaultdict(lambda: {p: 0.0 for p in self.periods})
        for mc_code, machine in self.data.machines.items():
            grp = machine.machine_group
            if grp:
                for p in self.periods:
                    group_hours[grp][p] += self.machine_hours_used.get(mc_code, {}).get(p, 0.0)

        for group_id in self.all_groups:
            hours = group_hours.get(group_id, {p: 0.0 for p in self.periods})
            group = self.data.machine_groups.get(group_id)
            machine_names = []
            if group:
                for mc in group.machine_codes:
                    m = self.data.machines.get(mc)
                    if m:
                        machine_names.append(m.machine_code)
            fte_data = {p: hours[p] / fte_per_month if fte_per_month > 0 else 0 for p in self.periods}
            self.rows_12.append(PlanningRow(
                material_number=group_id,
                material_name=';'.join(machine_names) if machine_names else '',
                product_type='Machine Group', product_family='',
                spc_product='', product_cluster='', product_name='',
                line_type=LineType.FTE_REQUIREMENTS.value,
                aux_column='1',
                values=fte_data.copy()
            ))
        self._calculate_truck_fte(fte_per_month)
        self._calculate_control_room_fte(fte_per_month)
        print(f"       -> {len(self.rows_12)} FTE requirement rows")

    def _calculate_truck_fte(self, fte_per_month):
        truck_hours = {}
        for mat_num, plan_data in self.production_plan.items():
            material = self.data.materials.get(mat_num)
            if not material:
                continue
            truck_mat = None
            if material.product_type.value == 'Bulk Product':
                truck_mat = 'ZZZZ_TRUCK01'
            elif material.product_type.value == 'Packaged Product':
                truck_mat = 'ZZZZ_TRUCK02'
            if not truck_mat:
                continue
            truck_material = self.data.materials.get(truck_mat)
            if not truck_material or not truck_material.ton_per_truck or truck_material.ton_per_truck <= 0:
                continue
            if truck_mat not in truck_hours:
                truck_hours[truck_mat] = {p: 0.0 for p in self.periods}
            for period in self.periods:
                qty = plan_data.get(period, 0.0)
                if qty > 0:
                    trucks = qty / truck_material.ton_per_truck
                    hours = trucks * (truck_material.time_per_truck or 0)
                    truck_hours[truck_mat][period] += hours
        for truck_mat, hours in truck_hours.items():
            tm = self.data.materials.get(truck_mat)
            fte_data = {p: hours[p] / fte_per_month if fte_per_month > 0 else 0 for p in self.periods}
            self.rows_12.append(PlanningRow(
                material_number=truck_mat, material_name=tm.name if tm else '',
                product_type='Machine Group', product_family='',
                spc_product='', product_cluster='', product_name='',
                line_type=LineType.FTE_REQUIREMENTS.value,
                aux_column='1', values=fte_data.copy()
            ))

    def _calculate_control_room_fte(self, fte_per_month):
        shift_hours = 520.0
        fte_val = shift_hours / fte_per_month if fte_per_month > 0 else 0
        self.rows_12.append(PlanningRow(
            material_number='ZZZZZ_CONTROLROOM',
            material_name='Control room operators',
            product_type='Machine Group', product_family='',
            spc_product='', product_cluster='', product_name='',
            line_type=LineType.FTE_REQUIREMENTS.value,
            aux_column='1', values={p: fte_val for p in self.periods}
        ))
