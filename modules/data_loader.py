"""
S&OP Planning Engine - Data Loader
Reads ONLY raw input sheets (not the Planning sheet).
"""

import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from pathlib import Path

from modules.models import (
    Material, BOMItem, RoutingItem, Machine, MachineGroup,
    SafetyStockConfig, PlanningConfig, ProductType, ShiftSystem
)


class DataLoader:
    """
    Loads raw input data from Excel.
    Does NOT read Planning sheet - calculations are done in Python.
    """
    
    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        if not self.file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        self.excel_file = pd.ExcelFile(file_path)
        
        # Data containers
        self.config: Optional[PlanningConfig] = None
        self.materials: Dict[str, Material] = {}
        self.bom: List[BOMItem] = []
        self.routing: Dict[str, List[RoutingItem]] = {}  # material -> list of routings
        self.machines: Dict[str, Machine] = {}
        self.machine_groups: Dict[str, MachineGroup] = {}
        self.forecasts: Dict[str, Dict[str, float]] = {}
        self.stock_levels: Dict[str, float] = {}
        self.safety_stock: Dict[str, SafetyStockConfig] = {}
        self.periods: List[str] = []
        
        # FTE configuration
        self.fte_hours_per_year: float = 1492
        self.shift_hours: Dict[str, float] = {}
        
    def load_all(self) -> 'DataLoader':
        """Load all raw input data."""
        print(f"Loading raw data from: {self.file_path.name}")
        print("-" * 60)
        
        self._load_config()
        self._load_fte_config()
        self._load_materials()
        self._load_bom()
        self._load_machines()
        self._load_routing()
        self._load_forecasts()
        self._load_stock_levels()
        self._load_safety_stock()
        
        print("-" * 60)
        print(f"Raw data loaded successfully:")
        print(f"  Materials: {len(self.materials)}")
        print(f"  BOM items: {len(self.bom)}")
        print(f"  Routings: {sum(len(v) for v in self.routing.values())}")
        print(f"  Machines: {len(self.machines)}")
        print(f"  Machine groups: {len(self.machine_groups)}")
        print(f"  Forecasts: {len(self.forecasts)}")
        print(f"  Stock levels: {len(self.stock_levels)}")
        print(f"  Safety stock: {len(self.safety_stock)}")
        print(f"  Periods: {len(self.periods)}")
        
        return self
    
    def _safe_float(self, value, default: float = 0.0) -> float:
        """Safely convert value to float."""
        if pd.isna(value):
            return default
        try:
            return float(value)
        except (ValueError, TypeError):
            return default
    
    def _load_config(self):
        """Load planning configuration."""
        try:
            df = pd.read_excel(self.excel_file, sheet_name='Config')
            
            initial_date = datetime(2025, 12, 1)
            forecast_months = 12
            site = "NLX1"
            unlimited_machine = "PBA99"
            
            # Get initial date from column header
            for col in df.columns:
                if isinstance(col, datetime):
                    initial_date = col
                    break
            
            # Read parameters
            for _, row in df.iterrows():
                param = str(row.iloc[0]) if pd.notna(row.iloc[0]) else ""
                value = row.iloc[1] if len(row) > 1 and pd.notna(row.iloc[1]) else None
                
                if param == "ForecastMonths" and value:
                    forecast_months = int(value)
                elif param == "Site" and value:
                    site = str(value)
                elif param == "MachineUnlimitedCapacity" and value:
                    unlimited_machine = str(value)
            
            self.config = PlanningConfig(
                initial_date=initial_date,
                forecast_months=forecast_months,
                site=site,
                unlimited_capacity_machine=unlimited_machine
            )
            self.periods = self.config.get_periods()
            
            print(f"  Config: {forecast_months} months from {initial_date.strftime('%Y-%m')}")
            
        except Exception as e:
            print(f"  Config warning: {e}")
            self.config = PlanningConfig(initial_date=datetime(2025, 12, 1))
            self.periods = self.config.get_periods()
    
    def _load_fte_config(self):
        """Load FTE and shift configuration."""
        try:
            df = pd.read_excel(self.excel_file, sheet_name='FTE sheet')
            
            for _, row in df.iterrows():
                type_val = str(row.get('Type', '')).strip()
                hours = row.get('Hours per year')
                
                if pd.notna(hours):
                    if type_val == 'FTE':
                        self.fte_hours_per_year = float(hours)
                    elif '2-shift' in type_val.lower():
                        self.shift_hours['2-shift system'] = float(hours) / 12
                    elif '3-shift' in type_val.lower():
                        self.shift_hours['3-shift system'] = float(hours) / 12
                    elif '24/7' in type_val.lower():
                        self.shift_hours['24/7 production'] = float(hours) / 12
            
            # Set defaults if not found
            if '3-shift system' not in self.shift_hours:
                self.shift_hours['3-shift system'] = 520
            
            print(f"  FTE config: {self.fte_hours_per_year} hours/year")
            
        except Exception as e:
            print(f"  FTE config warning: {e}")
            self.shift_hours = {'3-shift system': 520}
    
    def _load_materials(self):
        """Load material master data."""
        df = pd.read_excel(self.excel_file, sheet_name='Material master')
        
        for _, row in df.iterrows():
            mat_num = str(row.get('Material number (SKU)', '')).strip()
            if not mat_num or mat_num == 'nan':
                continue
            
            product_type_str = str(row.get(
                'Product type (packaged material/bulk material/raw material/packaging goods/other)', ''
            ))
            
            self.materials[mat_num] = Material(
                material_number=mat_num,
                name=str(row.get('Material name (SKU)', '')),
                product_type=ProductType.from_string(product_type_str),
                product_family=str(row.get('Product family', '') or ''),
                spc_product=str(row.get('SPC product', '') or '') if pd.notna(row.get('SPC product')) else '',
                product_cluster=str(row.get('Product cluster', '') or '') if pd.notna(row.get('Product cluster')) else '',
                product_name=str(row.get('Product name', '') or '') if pd.notna(row.get('Product name')) else '',
                production_line=str(row.get('Production line', '') or '') if pd.notna(row.get('Production line')) else None,
                grouped_production_line=str(row.get('Grouped production line', '') or '') if pd.notna(row.get('Grouped production line')) else None,
                mill_machine_group=str(row.get('Mill machine group', '') or '') if pd.notna(row.get('Mill machine group')) else None,
                packaging_machine_group=str(row.get('Packaging machine group', '') or '') if pd.notna(row.get('Packaging machine group')) else None,
                truck_operation=int(row.get('Truck operation', 0)) if pd.notna(row.get('Truck operation')) else 0,
                fte_requirements=float(row.get('FTE requirements', 0)) if pd.notna(row.get('FTE requirements')) else 0,
                ton_per_truck=float(row.get('Ton per truck')) if pd.notna(row.get('Ton per truck')) else None,
                time_per_truck=float(row.get('Time per truck')) if pd.notna(row.get('Time per truck')) else None,
                control_room=int(row.get('Control Room', 0)) if pd.notna(row.get('Control Room')) else 0,
                default_inventory_value=self._safe_float(row.get('Default inventory value', 0)),
                is_active=row.get('Active', 1) == 1
            )
        
        print(f"  Materials: {len(self.materials)}")
    
    def _load_bom(self):
        """Load Bill of Materials."""
        df = pd.read_excel(self.excel_file, sheet_name='BOM')
        
        for _, row in df.iterrows():
            parent = str(row.get('Material', '')).strip()
            component = str(row.get('Component', '')).strip()
            
            if not parent or not component or parent == 'nan' or component == 'nan':
                continue
            
            qty = row.get('BILLOFMATERIALITEMQUANTITY', 0)
            if pd.isna(qty) or qty == 0:
                continue
            
            header_qty = row.get('BOM Header Quantity in Base UoM', 1)
            if pd.isna(header_qty) or header_qty == 0:
                header_qty = 1
            
            # Quantity per unit of parent
            qty_per = float(qty) / float(header_qty)
            is_coproduct = row.get('Co-product', '') == 'X' or float(qty) < 0
            
            self.bom.append(BOMItem(
                plant=str(row.get('Plant', '')),
                parent_material=parent,
                parent_name=str(row.get('Material Name', '')),
                component_material=component,
                component_name=str(row.get('Component Description', '')),
                quantity_per=abs(qty_per),
                bom_header_quantity=float(header_qty),
                is_coproduct=is_coproduct,
                production_version=str(row.get('PV', '')) if pd.notna(row.get('PV')) else None
            ))
        
        print(f"  BOM: {len(self.bom)} items")
    
    def _load_machines(self):
        """Load machines and create machine groups."""
        df = pd.read_excel(self.excel_file, sheet_name='OEE + Machine groups')
        
        # Find period columns
        period_columns = []
        for col in df.columns:
            if isinstance(col, datetime):
                period_str = col.strftime('%Y-%m')
                if period_str in self.periods:
                    period_columns.append((col, period_str))
        
        # Track machine groups
        groups: Dict[str, List[str]] = {}
        
        for _, row in df.iterrows():
            machine_code = str(row.get('Machine code', '')).strip()
            if not machine_code or machine_code == 'nan':
                continue
            
            # Get availability by period
            availability = {}
            for col, period_str in period_columns:
                val = row.get(col)
                availability[period_str] = float(val) if pd.notna(val) else 1.0
            
            oee = float(row.get('OEE (%)', 0.8))
            if oee > 1:
                oee = oee / 100
            
            machine_group = str(row.get('Machine group', '')) if pd.notna(row.get('Machine group')) else None
            
            # Determine shift system (default 3-shift)
            shift_system = ShiftSystem.THREE_SHIFT
            if machine_code == self.config.unlimited_capacity_machine:
                shift_system = ShiftSystem.UNLIMITED
            
            self.machines[machine_code] = Machine(
                machine_id=str(row.get('MachineID', machine_code)),
                machine_code=machine_code,
                name=str(row.get('Machine name', machine_code)),
                oee=oee,
                machine_group=machine_group,
                availability_by_period=availability,
                shift_system=shift_system
            )
            
            # Build groups
            if machine_group and machine_group != 'nan':
                if machine_group not in groups:
                    groups[machine_group] = []
                groups[machine_group].append(machine_code)
        
        # Create MachineGroup objects
        for group_id, machine_codes in groups.items():
            self.machine_groups[group_id] = MachineGroup(
                group_id=group_id,
                machine_codes=machine_codes,
                shift_system=ShiftSystem.THREE_SHIFT
            )
        
        print(f"  Machines: {len(self.machines)}")
        print(f"  Machine groups: {len(self.machine_groups)}")
    
    def _load_routing(self):
        """Load routing/work center assignments."""
        df = pd.read_excel(self.excel_file, sheet_name='Routing')
        
        for _, row in df.iterrows():
            material = str(row.get('Material', '')).strip()
            if not material or material == 'nan':
                continue
            
            work_center = str(row.get('Work Center', '')).strip()
            if not work_center:
                continue
            
            routing_item = RoutingItem(
                plant=str(row.get('Plant', '')),
                material=material,
                material_description=str(row.get('Material Description', '')),
                work_center=work_center,
                base_quantity=float(row.get('Base Quantity', 1)) if pd.notna(row.get('Base Quantity')) else 1,
                standard_time=float(row.get('Standard Value 01', 0)) if pd.notna(row.get('Standard Value 01')) else 0,
                production_version=str(row.get('PV', '')) if pd.notna(row.get('PV')) else None
            )
            
            if material not in self.routing:
                self.routing[material] = []
            self.routing[material].append(routing_item)
        
        print(f"  Routing: {sum(len(v) for v in self.routing.values())} items")
    
    def _load_forecasts(self):
        """Load demand forecasts - including historical actuals for Aux column calculation."""
        df = pd.read_excel(self.excel_file, sheet_name='Forecast sheet')
        
        # Find ALL period columns (not just planning periods)
        period_columns = []
        for col in df.columns:
            col_str = str(col).strip()
            if '/' in col_str and 'M' in col_str:
                try:
                    parts = col_str.replace(' ', '').split('/')
                    year = parts[0]
                    month = parts[1].replace('M', '').zfill(2)
                    period_str = f"{year}-{month}"
                    # Load ALL periods, not just planning periods
                    period_columns.append((col, period_str))
                except:
                    pass
        
        for _, row in df.iterrows():
            mat_num = str(row.get('Material number', '')).strip()
            if not mat_num or mat_num == 'nan':
                continue
            
            forecast_data = {}
            for col, period_str in period_columns:
                val = row.get(col)
                if pd.notna(val) and float(val) != 0:
                    forecast_data[period_str] = float(val)
            
            if forecast_data:
                self.forecasts[mat_num] = forecast_data
        
        print(f"  Forecasts: {len(self.forecasts)} materials")
    
    def _load_stock_levels(self):
        """Load current stock levels."""
        df = pd.read_excel(self.excel_file, sheet_name='Stock level sheet')
        
        for _, row in df.iterrows():
            material = str(row.get('Material', '')).strip()
            if not material or material == 'nan':
                continue
            
            total = float(row.get('Total Stock', 0)) if pd.notna(row.get('Total Stock')) else 0
            if total == 0:
                total = float(row.get('Unrestricted Stock', 0)) if pd.notna(row.get('Unrestricted Stock')) else 0
            
            if material in self.stock_levels:
                self.stock_levels[material] += total
            else:
                self.stock_levels[material] = total
        
        print(f"  Stock levels: {len(self.stock_levels)} materials")
    
    def _load_safety_stock(self):
        """Load safety stock and lot size configurations."""
        df = pd.read_excel(self.excel_file, sheet_name='Safety stock')
        
        for _, row in df.iterrows():
            mat_num = str(row.get('Material number', '')).strip()
            if not mat_num or mat_num == 'nan':
                continue
            
            self.safety_stock[mat_num] = SafetyStockConfig(
                material_number=mat_num,
                safety_stock=float(row.get('Final stafety stock', 0)) if pd.notna(row.get('Final stafety stock')) else 0,
                lot_size=max(1, float(row.get('Lot size', 1)) if pd.notna(row.get('Lot size')) else 1),
                strategic_stock=float(row.get('Strategic stock', 0)) if pd.notna(row.get('Strategic stock')) else 0,
                target_stock=float(row.get('Target stock', 0)) if pd.notna(row.get('Target stock')) else 0
            )
        
        print(f"  Safety stock: {len(self.safety_stock)} configs")
    
    def get_bom_for_parent(self, parent: str) -> List[BOMItem]:
        """Get all BOM items where this material is the parent."""
        return [b for b in self.bom if b.parent_material == parent and not b.is_coproduct]
    
    def get_primary_routing(self, material: str) -> Optional[RoutingItem]:
        """Get primary routing for a material."""
        routings = self.routing.get(material, [])
        return routings[0] if routings else None
    
    def get_all_routings(self, material: str) -> List[RoutingItem]:
        """Get all routings for a material."""
        return self.routing.get(material, [])
