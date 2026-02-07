"""
S&OP Planning Engine - Capacity Engine
Calculates:
- Line 07: Capacity Utilization
- Line 09: Available Capacity
- Line 10: Utilization Rate
- Line 11: Shift Availability
- Line 12: FTE Requirements
"""

import numpy as np
from typing import Dict, List, Set
from modules.models import PlanningRow, LineType, ShiftSystem, SHIFT_HOURS, FTE_HOURS_PER_YEAR
from modules.data_loader import DataLoader


class CapacityEngine:
    """
    Generates capacity-related planning lines.
    
    Calculation sequence:
    1. Line 07 (Capacity Utilization): Hours used per material on each machine
    2. Line 11 (Shift Availability): Hours available per machine group
    3. Line 09 (Available Capacity): Hours available per machine
    4. Line 10 (Utilization Rate): Used / Available
    5. Line 12 (FTE Requirements): Labor needs per machine group
    """
    
    def __init__(
        self, 
        data: DataLoader,
        production_plan: Dict[str, Dict[str, float]]
    ):
        self.data = data
        self.periods = data.periods
        self.production_plan = production_plan
        
        # Results
        self.capacity_utilization: Dict[str, Dict[str, Dict[str, float]]] = {}  # material -> machine -> {period: hours}
        self.machine_hours_used: Dict[str, Dict[str, float]] = {}  # machine -> {period: total_hours}
        self.available_capacity: Dict[str, Dict[str, float]] = {}  # machine -> {period: hours}
        self.utilization_rate: Dict[str, Dict[str, float]] = {}  # machine -> {period: rate}
        self.shift_availability: Dict[str, Dict[str, float]] = {}  # group -> {period: hours}
        self.group_hours_used: Dict[str, Dict[str, float]] = {}  # group -> {period: hours}
        self.fte_requirements: Dict[str, Dict[str, float]] = {}  # group -> {period: fte}
        
        # Planning rows
        self.rows_07_cap: List[PlanningRow] = []  # Capacity utilization
        self.rows_09: List[PlanningRow] = []  # Available capacity
        self.rows_10: List[PlanningRow] = []  # Utilization rate
        self.rows_11: List[PlanningRow] = []  # Shift availability
        self.rows_12: List[PlanningRow] = []  # FTE requirements
    
    def calculate(self) -> Dict[str, List[PlanningRow]]:
        """Calculate all capacity-related lines."""
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
        """
        Line 07: Capacity Utilization
        
        Formula: Hours = (Production_Qty / Base_Qty) × Standard_Time
        
        Creates one row per material-machine combination.
        """
        print("  [07] Calculating Capacity Utilization...")
        
        # Initialize machine hours
        for machine_code in self.data.machines:
            self.machine_hours_used[machine_code] = {p: 0.0 for p in self.periods}
        
        # Calculate hours for each material
        for mat_num, plan_data in self.production_plan.items():
            # Get all routings for this material
            routings = self.data.get_all_routings(mat_num)
            if not routings:
                continue
            
            material = self.data.materials.get(mat_num)
            if not material:
                continue
            
            for routing in routings:
                work_center = routing.work_center
                if work_center not in self.data.machines:
                    continue
                
                base_qty = routing.base_quantity if routing.base_quantity > 0 else 1.0
                std_time = routing.standard_time
                
                hours_data = {}
                for period in self.periods:
                    prod_qty = plan_data.get(period, 0.0)
                    if prod_qty > 0:
                        hours = (prod_qty / base_qty) * std_time
                        hours_data[period] = hours
                        
                        # Add to machine totals
                        self.machine_hours_used[work_center][period] += hours
                    else:
                        hours_data[period] = 0.0
                
                # Store detailed utilization
                if mat_num not in self.capacity_utilization:
                    self.capacity_utilization[mat_num] = {}
                self.capacity_utilization[mat_num][work_center] = hours_data
                
                # Create planning row
                if any(v > 0 for v in hours_data.values()):
                    machine = self.data.machines[work_center]
                    row = PlanningRow(
                        material_number=mat_num,
                        material_name=material.name,
                        product_type=material.product_type.value,
                        product_family=material.product_family,
                        spc_product=material.spc_product or '',
                        product_cluster=material.product_cluster or '',
                        product_name=material.product_name or '',
                        line_type=LineType.CAPACITY_UTILIZATION.value,
                        aux_column=work_center,
                        values=hours_data.copy()
                    )
                    self.rows_07_cap.append(row)
        
        print(f"       → {len(self.rows_07_cap)} capacity utilization rows")
    
    def _calculate_shift_availability(self):
        """
        Line 11: Shift Availability
        
        Hours available per machine group based on shift system.
        Default: 3-shift system = 520 hours/month
        """
        print("  [11] Calculating Shift Availability...")
        
        # Calculate hours by machine group
        for group_id, group in self.data.machine_groups.items():
            shift_hours = group.get_shift_hours()
            
            self.shift_availability[group_id] = {p: shift_hours for p in self.periods}
            
            # Get machine names in this group
            machine_names = []
            for mc in group.machine_codes:
                machine = self.data.machines.get(mc)
                if machine:
                    machine_names.append(machine.machine_code)
            
            # Create planning row
            row = PlanningRow(
                material_number=group_id,
                material_name=';'.join(machine_names),
                product_type='Machine Group',
                product_family='',
                spc_product='',
                product_cluster='',
                product_name='',
                line_type=LineType.SHIFT_AVAILABILITY.value,
                aux_column='3-shift system',  # Default
                values={p: shift_hours for p in self.periods}
            )
            self.rows_11.append(row)
        
        print(f"       → {len(self.rows_11)} shift availability rows")
    
    def _calculate_available_capacity(self):
        """
        Line 09: Available Capacity
        
        Formula: Available = Shift_Hours × OEE × Availability_Factor
        
        For unlimited capacity machines, shows 1.0 as placeholder.
        """
        print("  [09] Calculating Available Capacity...")
        
        for machine_code, machine in self.data.machines.items():
            self.available_capacity[machine_code] = {}
            
            for period in self.periods:
                if machine.shift_system == ShiftSystem.UNLIMITED:
                    # Unlimited capacity - use 1.0 as indicator
                    available = 1.0
                    shift_hours = 'Unlimited'
                else:
                    # Calculate based on shift system and OEE
                    base_hours = SHIFT_HOURS.get(machine.shift_system, 520)
                    availability = machine.get_availability(period)
                    available = base_hours * machine.oee * availability
                    shift_hours = str(int(base_hours))
                
                self.available_capacity[machine_code][period] = available
            
            # Create planning row
            row = PlanningRow(
                material_number=machine.machine_id,
                material_name=machine.machine_code,
                product_type='Machine',
                product_family=machine.machine_group or '',
                spc_product='',
                product_cluster='',
                product_name=machine.name,
                line_type=LineType.AVAILABLE_CAPACITY.value,
                aux_column=shift_hours if machine.shift_system != ShiftSystem.UNLIMITED else 'Unlimited',
                values=self.available_capacity[machine_code].copy()
            )
            self.rows_09.append(row)
        
        print(f"       → {len(self.rows_09)} available capacity rows")
    
    def _calculate_utilization_rate(self):
        """
        Line 10: Utilization Rate
        
        Formula: Rate = Hours_Used / Hours_Available
        """
        print("  [10] Calculating Utilization Rate...")
        
        for machine_code, machine in self.data.machines.items():
            self.utilization_rate[machine_code] = {}
            
            used = self.machine_hours_used.get(machine_code, {})
            available = self.available_capacity.get(machine_code, {})
            
            for period in self.periods:
                used_hours = used.get(period, 0.0)
                available_hours = available.get(period, 1.0)
                
                if available_hours > 0 and available_hours != 1.0:  # Skip unlimited
                    rate = used_hours / available_hours
                else:
                    rate = 0.0 if used_hours == 0 else used_hours / 520  # Default
                
                self.utilization_rate[machine_code][period] = rate
            
            # Create planning row
            row = PlanningRow(
                material_number=machine.machine_id,
                material_name=machine.machine_code,
                product_type='Machine',
                product_family=machine.machine_group or '',
                spc_product='',
                product_cluster='',
                product_name=machine.name,
                line_type=LineType.UTILIZATION_RATE.value,
                values=self.utilization_rate[machine_code].copy()
            )
            self.rows_10.append(row)
        
        print(f"       → {len(self.rows_10)} utilization rate rows")
    
    def _calculate_fte_requirements(self):
        """
        Line 12: FTE Requirements
        
        Formula: FTE = Total_Hours_Used / (FTE_Hours_Per_Year / 12)
        
        Calculated per machine group.
        """
        print("  [12] Calculating FTE Requirements...")
        
        fte_hours_per_month = self.data.fte_hours_per_year / 12
        
        # Aggregate hours by machine group
        for group_id, group in self.data.machine_groups.items():
            self.group_hours_used[group_id] = {p: 0.0 for p in self.periods}
            
            for machine_code in group.machine_codes:
                machine_hours = self.machine_hours_used.get(machine_code, {})
                for period in self.periods:
                    self.group_hours_used[group_id][period] += machine_hours.get(period, 0.0)
            
            # Calculate FTE
            self.fte_requirements[group_id] = {}
            for period in self.periods:
                hours = self.group_hours_used[group_id][period]
                fte = hours / fte_hours_per_month if fte_hours_per_month > 0 else 0
                self.fte_requirements[group_id][period] = fte
            
            # Get machine names in group
            machine_names = []
            for mc in group.machine_codes:
                machine = self.data.machines.get(mc)
                if machine:
                    machine_names.append(machine.machine_code)
            
            # Create planning row
            row = PlanningRow(
                material_number=group_id,
                material_name=';'.join(machine_names),
                product_type='Machine Group',
                product_family='',
                spc_product='',
                product_cluster='',
                product_name='',
                line_type=LineType.FTE_REQUIREMENTS.value,
                aux_column='1',  # FTE indicator
                values=self.fte_requirements[group_id].copy()
            )
            self.rows_12.append(row)
        
        print(f"       → {len(self.rows_12)} FTE requirement rows")
    
    def get_machine_utilization(self) -> Dict[str, Dict[str, float]]:
        """Get total hours used per machine."""
        return self.machine_hours_used
    
    def get_utilization_rates(self) -> Dict[str, Dict[str, float]]:
        """Get utilization rates per machine."""
        return self.utilization_rate
