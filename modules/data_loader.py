"""
S&OP Planning Engine - Data Loader
Reads ONLY raw input sheets (not the Planning sheet).
"""

import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Set
from pathlib import Path
from collections import defaultdict

from modules.models import (
    Material, BOMItem, RoutingItem, Machine, MachineGroup,
    SafetyStockConfig, PlanningConfig, ProductType, ShiftSystem,
    ValuationParameters, SalesPriceItem, RawMaterialCost, MachineCost
)


class DataLoader:
    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        if not self.file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        self.excel_file = pd.ExcelFile(file_path)

        self.config: Optional[PlanningConfig] = None
        self.materials: Dict[str, Material] = {}
        self.bom: List[BOMItem] = []
        self.routing: Dict[str, List[RoutingItem]] = {}
        self.machines: Dict[str, Machine] = {}
        self.machine_groups: Dict[str, MachineGroup] = {}
        self.forecasts: Dict[str, Dict[str, float]] = {}
        self.stock_levels: Dict[str, float] = {}
        self.stock: Dict[str, Dict[str, float]] = {}  # NEW: includes both qty and value
        self.safety_stock: Dict[str, SafetyStockConfig] = {}
        self.periods: List[str] = []

        self.fte_hours_per_year: float = 1492
        self.shift_hours: Dict[str, float] = {}
        self.purchase_lead_times: Dict[str, int] = {}
        self.purchase_sheet_materials: Set[str] = set()
        self.purchased_and_produced: Dict[str, float] = {}
        self.bom_levels: Dict[str, int] = {}
        
        # Financial data (NEW)
        self.sales_prices: Dict[str, SalesPriceItem] = {}
        self.material_costs: Dict[str, RawMaterialCost] = {}
        self.machine_costs: Dict[str, MachineCost] = {}
        self.valuation_params: Optional[ValuationParameters] = None

    def load_all(self) -> 'DataLoader':
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
        self._load_purchase_sheet()
        self._calculate_bom_levels()
        self._load_avg_sales_price()
        self._load_cost_raw_material()
        self._load_cost_machine_hour()
        self._load_valuation_params()
        print("-" * 60)
        print(f"  Materials: {len(self.materials)}, BOM: {len(self.bom)}, Routings: {sum(len(v) for v in self.routing.values())}")
        print(f"  Machines: {len(self.machines)}, Groups: {len(self.machine_groups)}")
        print(f"  Forecasts: {len(self.forecasts)}, Stock: {len(self.stock_levels)}, Safety: {len(self.safety_stock)}")
        ml = max(self.bom_levels.values()) if self.bom_levels else 0
        print(f"  BOM levels: {ml+1} (0..{ml})")
        return self

    def _safe_float(self, value, default=0.0):
        if pd.isna(value):
            return default
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    def _load_config(self):
        try:
            df = pd.read_excel(self.excel_file, sheet_name='Config')
            initial_date = datetime(2025, 12, 1)
            forecast_months = 12
            forecast_actuals_months = 12
            site = "NLX1"
            unlimited_machine = "PBA99"

            for col in df.columns:
                if isinstance(col, datetime):
                    initial_date = col
                    break

            for _, row in df.iterrows():
                param = str(row.iloc[0]) if pd.notna(row.iloc[0]) else ""
                value = row.iloc[1] if len(row) > 1 and pd.notna(row.iloc[1]) else None
                if param == "ForecastMonths" and value:
                    forecast_months = int(value)
                elif param == "ForecastActualsMonths" and value:
                    forecast_actuals_months = int(value)
                elif param == "Site" and value:
                    site = str(value)
                elif param == "MachineUnlimitedCapacity" and value:
                    unlimited_machine = str(value)
                elif param == "PurchasedAndProducedMaterials" and value:
                    for entry in str(value).split(';'):
                        parts = entry.strip().split(':')
                        if len(parts) == 2:
                            self.purchased_and_produced[parts[0].strip()] = float(parts[1].strip())

            self.config = PlanningConfig(
                initial_date=initial_date, forecast_months=forecast_months,
                site=site, unlimited_capacity_machine=unlimited_machine
            )
            self.forecast_actuals_months = forecast_actuals_months
            self.periods = self.config.get_periods()
            print(f"  Config: {forecast_months}mo from {initial_date.strftime('%Y-%m')}, site={site}")
            print(f"  ForecastActualsMonths: {forecast_actuals_months}")
            if self.purchased_and_produced:
                print(f"  PurchasedAndProduced: {self.purchased_and_produced}")
        except Exception as e:
            print(f"  Config warning: {e}")
            self.config = PlanningConfig(initial_date=datetime(2025, 12, 1))
            self.periods = self.config.get_periods()

    def _load_fte_config(self):
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
            if '3-shift system' not in self.shift_hours:
                self.shift_hours['3-shift system'] = 520
            print(f"  FTE config: {self.fte_hours_per_year} hours/year")
        except Exception as e:
            print(f"  FTE config warning: {e}")
            self.shift_hours = {'3-shift system': 520}

    def _load_materials(self):
        df = pd.read_excel(self.excel_file, sheet_name='Material master')
        for _, row in df.iterrows():
            mat_num = str(row.get('Material number (SKU)', '')).strip()
            if not mat_num or mat_num == 'nan':
                continue
            product_type_str = str(row.get(
                'Product type (packaged material/bulk material/raw material/packaging goods/other)', ''))
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
            qty_per = float(qty) / float(header_qty)
            is_coproduct = row.get('Co-product', '') == 'X' or float(qty) < 0
            self.bom.append(BOMItem(
                plant=str(row.get('Plant', '')),
                parent_material=parent, parent_name=str(row.get('Material Name', '')),
                component_material=component, component_name=str(row.get('Component Description', '')),
                quantity_per=abs(qty_per), bom_header_quantity=float(header_qty),
                is_coproduct=is_coproduct,
                production_version=str(row.get('PV', '')) if pd.notna(row.get('PV')) else None
            ))
        print(f"  BOM: {len(self.bom)} items")

    def _load_machines(self):
        df = pd.read_excel(self.excel_file, sheet_name='OEE + Machine groups')
        period_columns = []
        for col in df.columns:
            if isinstance(col, datetime):
                ps = col.strftime('%Y-%m')
                if ps in self.periods:
                    period_columns.append((col, ps))
        groups: Dict[str, List[str]] = {}
        for _, row in df.iterrows():
            mc = str(row.get('Machine code', '')).strip()
            if not mc or mc == 'nan':
                continue
            avail = {}
            for col, ps in period_columns:
                val = row.get(col)
                avail[ps] = float(val) if pd.notna(val) else 1.0
            oee = float(row.get('OEE (%)', 0.8))
            if oee > 1:
                oee = oee / 100
            mg = str(row.get('Machine group', '')) if pd.notna(row.get('Machine group')) else None
            ss = ShiftSystem.THREE_SHIFT
            if mc == self.config.unlimited_capacity_machine:
                ss = ShiftSystem.UNLIMITED
            mid = str(row.get('MachineID', mc))
            self.machines[mc] = Machine(
                machine_id=mid, machine_code=mc, name=str(row.get('Machine name', mc)),
                oee=oee, machine_group=mg if mg != 'nan' else None,
                availability_by_period=avail, shift_system=ss
            )
            if mg and mg != 'nan':
                groups.setdefault(mg, []).append(mc)
        for gid, mcs in groups.items():
            self.machine_groups[gid] = MachineGroup(group_id=gid, machine_codes=mcs, shift_system=ShiftSystem.THREE_SHIFT)
        print(f"  Machines: {len(self.machines)}, Groups: {len(self.machine_groups)}")

    def _load_routing(self):
        df = pd.read_excel(self.excel_file, sheet_name='Routing')
        for _, row in df.iterrows():
            mat = str(row.get('Material', '')).strip()
            if not mat or mat == 'nan':
                continue
            wc = str(row.get('Work Center', '')).strip()
            if not wc:
                continue
            ri = RoutingItem(
                plant=str(row.get('Plant', '')), material=mat,
                material_description=str(row.get('Material Description', '')),
                work_center=wc,
                base_quantity=float(row.get('Base Quantity', 1)) if pd.notna(row.get('Base Quantity')) else 1,
                standard_time=float(row.get('Standard Value 01', 0)) if pd.notna(row.get('Standard Value 01')) else 0,
                production_version=str(row.get('PV', '')) if pd.notna(row.get('PV')) else None
            )
            self.routing.setdefault(mat, []).append(ri)
        print(f"  Routing: {sum(len(v) for v in self.routing.values())} items")

    def _load_forecasts(self):
        df = pd.read_excel(self.excel_file, sheet_name='Forecast sheet')
        period_columns = []
        for col in df.columns:
            cs = str(col).strip()
            if '/' in cs and 'M' in cs:
                try:
                    parts = cs.replace(' ', '').split('/')
                    ps = f"{parts[0]}-{parts[1].replace('M', '').zfill(2)}"
                    period_columns.append((col, ps))
                except:
                    pass
        for _, row in df.iterrows():
            mn = str(row.get('Material number', '')).strip()
            if not mn or mn == 'nan':
                continue
            fd = {}
            for col, ps in period_columns:
                val = row.get(col)
                # Store ALL values including zeros - Excel AVERAGE counts zeros, only ignores blanks
                if pd.notna(val):
                    fd[ps] = float(val)
            # Include material if it's in material master (even if all zeros for active ones)
            mat = self.materials.get(mn)
            if mat and not mat.is_active:
                continue  # Always skip inactive materials
            if mat and fd is not None:
                self.forecasts[mn] = fd if fd else {}
            elif fd:
                self.forecasts[mn] = fd
        print(f"  Forecasts: {len(self.forecasts)} materials")

    def _load_stock_levels(self):
        df = pd.read_excel(self.excel_file, sheet_name='Stock level sheet')
        for _, row in df.iterrows():
            mat = str(row.get('Material', '')).strip()
            if not mat or mat == 'nan':
                continue
            plant = str(row.get('Plant', '')).strip() if pd.notna(row.get('Plant')) else ''
            if self.config and self.config.site and plant and plant != self.config.site:
                continue
            # VBA uses Unrestricted Stock only (not Total Stock which includes blocked)
            total_qty = float(row.get('Unrestricted Stock', 0)) if pd.notna(row.get('Unrestricted Stock')) else 0
            total_value = float(row.get('Total Value', 0)) if pd.notna(row.get('Total Value')) else 0
            total_stock_qty = float(row.get('Total Stock', 0)) if pd.notna(row.get('Total Stock')) else 0
            # VBA ValueStartStockLevel uses column 17 = "Value of Unrestricted Stock"
            value_unrestricted = float(row.get('Value of Unrestricted Stock', 0)) if pd.notna(row.get('Value of Unrestricted Stock')) else 0
            
            self.stock_levels[mat] = self.stock_levels.get(mat, 0) + total_qty
            
            # Store both quantity and value for inventory valuation
            if mat not in self.stock:
                self.stock[mat] = {'Total Stock': 0, 'Total Value': 0, 'Value Unrestricted': 0}
            self.stock[mat]['Total Stock'] += total_stock_qty
            self.stock[mat]['Total Value'] += total_value
            self.stock[mat]['Value Unrestricted'] += value_unrestricted
            
        print(f"  Stock levels: {len(self.stock_levels)}")

    def _load_safety_stock(self):
        df = pd.read_excel(self.excel_file, sheet_name='Safety stock')
        for _, row in df.iterrows():
            mn = str(row.get('Material number', '')).strip()
            if not mn or mn == 'nan':
                continue
            self.safety_stock[mn] = SafetyStockConfig(
                material_number=mn,
                safety_stock=float(row.get('Final stafety stock', 0)) if pd.notna(row.get('Final stafety stock')) else 0,
                lot_size=max(1, float(row.get('Lot size', 1)) if pd.notna(row.get('Lot size')) else 1),
                strategic_stock=float(row.get('Strategic stock', 0)) if pd.notna(row.get('Strategic stock')) else 0,
                target_stock=float(row.get('Target stock', 0)) if pd.notna(row.get('Target stock')) else 0
            )
        print(f"  Safety stock: {len(self.safety_stock)} configs")

    def _load_purchase_sheet(self):
        try:
            df = pd.read_excel(self.excel_file, sheet_name='Purchase sheet', header=None)
            for i in range(1, len(df)):
                mn = str(df.iloc[i, 0]).strip()
                if not mn or mn == 'nan' or mn == 'Material number':
                    continue
                lt = df.iloc[i, 3]
                try:
                    lt_val = int(float(lt)) if pd.notna(lt) else 1
                except (ValueError, TypeError):
                    lt_val = 1
                self.purchase_lead_times[mn] = lt_val
                self.purchase_sheet_materials.add(mn)
            print(f"  Purchase sheet: {len(self.purchase_lead_times)} lead times")
        except Exception as e:
            print(f"  Purchase sheet warning: {e}")

    def _calculate_bom_levels(self):
        parent_to_children = defaultdict(set)
        child_to_parents = defaultdict(set)
        all_mats = set()
        for b in self.bom:
            if b.is_coproduct:
                continue
            parent_to_children[b.parent_material].add(b.component_material)
            child_to_parents[b.component_material].add(b.parent_material)
            all_mats.add(b.parent_material)
            all_mats.add(b.component_material)

        roots = set()
        for m in all_mats:
            if m not in child_to_parents:
                roots.add(m)
        for m in self.forecasts:
            if m in all_mats:
                roots.add(m)

        levels = {}
        def assign(mat, level, visited):
            if mat in visited:
                return
            visited.add(mat)
            if mat not in levels or level > levels[mat]:
                levels[mat] = level
            for c in parent_to_children.get(mat, set()):
                assign(c, level + 1, visited.copy())

        for r in roots:
            assign(r, 0, set())
        for m in all_mats:
            if m not in levels:
                levels[m] = 0
        self.bom_levels = levels

    def get_materials_at_level(self, level):
        return [m for m, l in self.bom_levels.items() if l == level]

    def get_max_bom_level(self):
        return max(self.bom_levels.values()) if self.bom_levels else 0

    def _load_avg_sales_price(self):
        """Load average sales price data.
        
        VBA logic: For each material, SUM volume and ExWorksRevenue across ALL rows
        where material AND site match, then price = totalRevenue / totalVolume.
        """
        sheet_name = 'Average sales price'
        if sheet_name not in self.excel_file.sheet_names:
            print(f"  Warning: '{sheet_name}' sheet not found")
            return
        
        df = pd.read_excel(self.excel_file, sheet_name=sheet_name)
        site = self.config.site if self.config else None
        
        # Accumulate volume and revenue per product
        accum = {}  # product_id -> {volume, revenue, plant_code}
        for _, row in df.iterrows():
            product_id = str(row.get('ProductId', '')).strip()
            plant_code = str(row.get('PlantCode', '')).strip() if pd.notna(row.get('PlantCode')) else ''
            if not product_id or product_id == 'nan':
                continue
            if site and plant_code and plant_code != site:
                continue
            vol = float(row.get('Volume 2025 (t)', 0)) if pd.notna(row.get('Volume 2025 (t)')) else 0
            rev = float(row.get('ExWorksRevenue', 0)) if pd.notna(row.get('ExWorksRevenue')) else 0
            if product_id not in accum:
                accum[product_id] = {'volume': 0, 'revenue': 0, 'plant_code': plant_code}
            accum[product_id]['volume'] += vol
            accum[product_id]['revenue'] += rev
        
        for product_id, data in accum.items():
            if data['volume'] > 0:
                self.sales_prices[product_id] = SalesPriceItem(
                    plant_code=data['plant_code'],
                    product_id=product_id,
                    volume_2025=data['volume'],
                    ex_works_revenue=data['revenue']
                )
        
        print(f"  Loaded {len(self.sales_prices)} sales price items")

    def _load_cost_raw_material(self):
        """Load raw material costs, filtered by site."""
        sheet_name = 'Cost raw material'
        if sheet_name not in self.excel_file.sheet_names:
            print(f"  Warning: '{sheet_name}' sheet not found")
            return
        
        df = pd.read_excel(self.excel_file, sheet_name=sheet_name)
        site = self.config.site if self.config else None
        
        for _, row in df.iterrows():
            material = str(row.get('Product Code', '')).strip()
            plant_code = str(row.get('Plant Code', '')).strip() if pd.notna(row.get('Plant Code')) else ''
            if site and plant_code and plant_code != site:
                continue
            if material and pd.notna(row.get('Cost Per Unit')):
                self.material_costs[material] = RawMaterialCost(
                    plant_code=plant_code,
                    product_code=material,
                    product_name=str(row.get('Product Name', '')),
                    cost_per_unit=float(row.get('Cost Per Unit', 0))
                )
        
        print(f"  Loaded {len(self.material_costs)} raw material costs")

    def _load_cost_machine_hour(self):
        """Load machine hour costs (Fixed price for Activity Type 'Machine Variable').
        
        VBA logic: CostMHClmn = 7 (Fxd Prices in OCrcy), CostMHActType = 6 (Act. type short text),
        matches where Act. type short text = 'Machine Variable' AND site matches.
        Key = first 5 chars of Cost Center (e.g. 'PBA11' from 'PBA11-NLX1').
        """
        sheet_name = 'Cost machine hour'
        if sheet_name not in self.excel_file.sheet_names:
            print(f"  Warning: '{sheet_name}' sheet not found")
            return
        
        df = pd.read_excel(self.excel_file, sheet_name=sheet_name)
        
        site = self.config.site if self.config else None
        
        for _, row in df.iterrows():
            act_type_text = str(row.get('Act. type short text', '')).strip()
            plant_code = str(row.get('Plant Code', '')).strip() if pd.notna(row.get('Plant Code')) else ''
            
            # VBA: CostMHActType checks "Machine Variable" string
            if act_type_text == 'Machine Variable':
                if site and plant_code and plant_code != site:
                    continue
                cost_center_full = str(row.get('Cost Center', '')).strip()
                cost_center = cost_center_full[:5]  # First 5 chars (e.g. PBA11)
                # VBA: CostMHClmn = 7 = "Fxd Prices in OCrcy"
                fxd_price = float(row.get('Fxd Prices in OCrcy', 0)) if pd.notna(row.get('Fxd Prices in OCrcy')) else 0
                if cost_center and fxd_price != 0:
                    self.machine_costs[cost_center] = MachineCost(
                        plant_code=plant_code,
                        cost_center=cost_center,
                        variable_cost_per_hour=fxd_price  # Actually fixed price per VBA
                    )
        
        print(f"  Loaded {len(self.machine_costs)} machine costs")

    def _load_valuation_params(self):
        """Load valuation parameters for financial calculations."""
        sheet_name = 'Valuation parameters'
        if sheet_name not in self.excel_file.sheet_names:
            print(f"  Warning: '{sheet_name}' sheet not found")
            return
        
        df = pd.read_excel(self.excel_file, sheet_name=sheet_name)
        
        # Extract values by cost number
        params = {}
        for _, row in df.iterrows():
            cost_num = row.get('Cost number')
            value = row.get('Value')
            if pd.notna(cost_num) and pd.notna(value):
                params[int(cost_num)] = float(value)
        
        if len(params) < 8:
            print(f"  Warning: Only {len(params)}/8 valuation parameters loaded")
            return
        
        self.valuation_params = ValuationParameters(
            direct_fte_cost_per_month=params.get(1, 0),
            indirect_fte_cost_per_month=params.get(2, 0),
            overhead_cost_per_month=params.get(3, 0),
            sga_cost_per_month=params.get(4, 0),
            depreciation_per_year=params.get(5, 0),
            net_book_value=params.get(6, 0),
            days_sales_outstanding=int(params.get(7, 0)),
            days_payable_outstanding=int(params.get(8, 0))
        )
        
        print(f"  Loaded valuation parameters")

    # === Helper methods ===
    def is_purchased_and_produced(self, mat_num):
        return mat_num in self.purchased_and_produced

    def get_purchase_fraction(self, mat_num):
        """Config fraction (e.g. 0.2) = PRODUCTION fraction."""
        return self.purchased_and_produced.get(mat_num, 0.0)

    def get_production_ceiling(self, mat_num):
        """BOM header qty = ceiling multiple for production plan."""
        children = self.get_bom_for_parent(mat_num)
        if children:
            return children[0].bom_header_quantity
        return 1.0

    def get_lead_time(self, mat_num):
        if mat_num in self.purchase_lead_times:
            return self.purchase_lead_times[mat_num]
        return 1  # Default

    def get_bom_for_parent(self, parent):
        return [b for b in self.bom if b.parent_material == parent and not b.is_coproduct]

    def get_primary_routing(self, material):
        r = self.routing.get(material, [])
        return r[0] if r else None

    def get_all_routings(self, material):
        return self.routing.get(material, [])
