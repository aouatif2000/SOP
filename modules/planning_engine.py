"""
S&OP Planning Engine - Complete Calculation Engine
KEY FORMULAS (from feedback):
- Line 02: Dependent Demand = Parent Prod Plan × BOM qty_per (Aux2)
- Line 10: Utilization Rate - show 2 decimal places
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional
from collections import defaultdict
from modules.models import PlanningRow, LineType
from modules.data_loader import DataLoader
from modules.capacity_engine import CapacityEngine


class PlanningEngine:
    EXPECTED_LINE_TYPES = [
        LineType.DEMAND_FORECAST.value, LineType.DEPENDENT_DEMAND.value,
        LineType.TOTAL_DEMAND.value, LineType.INVENTORY.value,
        LineType.MIN_TARGET_STOCK.value, LineType.PRODUCTION_PLAN.value,
        LineType.PURCHASE_RECEIPT.value, LineType.PURCHASE_PLAN.value,
        LineType.CAPACITY_UTILIZATION.value, LineType.DEPENDENT_REQUIREMENTS.value,
        LineType.AVAILABLE_CAPACITY.value, LineType.UTILIZATION_RATE.value,
        LineType.SHIFT_AVAILABILITY.value, LineType.FTE_REQUIREMENTS.value,
    ]
    
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.data = None
        self.xl = None
        self.bom_df = None
        self.forecast_data = {}
        self.dependent_demand = {}
        self.total_demand = {}
        self.production_plan = {}
        self.purchase_receipt = {}
        self.results = {}
        self.all_rows = []
        self.summary = {}
    
    def run(self):
        print("\n" + "=" * 70)
        print("S&OP PLANNING ENGINE")
        print("=" * 70)
        
        print("\n[1] Loading data...")
        self._load_all_data()
        
        print("\n[2] Loading Production Plans...")
        self._load_production_plans()
        
        print("\n[3] Calculating Line 01 - Forecast...")
        self._calc_line_01()
        
        print("\n[4] Calculating Line 02 - Dependent Demand...")
        self._calc_line_02()
        
        print("\n[5] Calculating Line 03 - Total Demand...")
        self._calc_line_03()
        
        print("\n[6] Loading Lines 04-07...")
        self._load_lines_04_07()
        
        print("\n[7] Generating Line 08...")
        self._gen_line_08()
        
        print("\n[8] Capacity Lines (09-12)...")
        self._calc_capacity()
        
        self._finalize()
        return self
    
    def _load_all_data(self):
        self.xl = pd.ExcelFile(self.file_path)
        self.data = DataLoader(self.file_path)
        self.data.load_all()
        self.bom_df = pd.read_excel(self.xl, sheet_name='BOM')
        self.bom_df['qty_per'] = self.bom_df['BILLOFMATERIALITEMQUANTITY'] / self.bom_df['BOM Header Quantity in Base UoM']
        self.bom_df['Material'] = self.bom_df['Material'].astype(str)
        self.bom_df['Component'] = self.bom_df['Component'].astype(str)
        self.periods = self.data.periods
        print(f"  BOM: {len(self.bom_df)}, Periods: {len(self.periods)}")
    
    def _load_production_plans(self):
        planning_df = pd.read_excel(self.xl, sheet_name='Planning sheet')
        planning_df['Material number'] = planning_df['Material number'].astype(str)
        period_cols = [c for c in planning_df.columns if hasattr(c, 'strftime')][:12]
        
        for _, row in planning_df[planning_df['Line type'] == '06. Production plan'].iterrows():
            mat_id = str(row['Material number'])
            if mat_id and mat_id != 'nan':
                self.production_plan[mat_id] = {col.strftime('%Y-%m'): float(row[col]) if pd.notna(row[col]) else 0.0 for col in period_cols}
        
        for _, row in planning_df[planning_df['Line type'] == '06. Purchase receipt'].iterrows():
            mat_id = str(row['Material number'])
            if mat_id and mat_id != 'nan':
                self.purchase_receipt[mat_id] = {col.strftime('%Y-%m'): float(row[col]) if pd.notna(row[col]) else 0.0 for col in period_cols}
        
        print(f"  Production: {len(self.production_plan)}, Purchase: {len(self.purchase_receipt)}")
    
    def _calc_line_01(self):
        rows = []
        for mat_id, forecast_dict in self.data.forecasts.items():
            mat = self.data.materials.get(mat_id)
            if not mat: continue
            values = {p: forecast_dict.get(p, 0.0) for p in self.periods}
            fvals = list(values.values())
            aux1 = sum(fvals)
            aux2 = (fvals[0] + sum(fvals[1:])/11*11)/12 if len(fvals) > 1 else (fvals[0] if fvals else 0)
            self.forecast_data[mat_id] = values
            rows.append(PlanningRow(
                material_number=mat_id, material_name=mat.name,
                product_type=mat.product_type.value, product_family=mat.product_family,
                spc_product=mat.spc_product or '', product_cluster=mat.product_cluster or '',
                product_name=mat.product_name or '', line_type=LineType.DEMAND_FORECAST.value,
                aux_column=f"{aux1:.3f}" if aux1 > 0 else '', aux_2_column=f"{aux2:.3f}" if aux2 > 0 else '',
                values=values))
        rows.sort(key=lambda r: int(r.material_number) if r.material_number.isdigit() else 0)
        self.results[LineType.DEMAND_FORECAST.value] = rows
        print(f"  [01]: {len(rows)} rows")
    
    def _calc_line_02(self):
        rows = []
        for mat_id in self.data.materials:
            self.dependent_demand[mat_id] = {p: 0.0 for p in self.periods}
        
        for _, bom in self.bom_df.iterrows():
            parent, child, qty = str(bom['Material']), str(bom['Component']), bom['qty_per']
            if pd.isna(qty) or qty <= 0: continue
            parent_plan = self.production_plan.get(parent, {})
            if not parent_plan or not any(v > 0 for v in parent_plan.values()): continue
            child_mat = self.data.materials.get(child)
            if not child_mat: continue
            
            values, has = {}, False
            for p in self.periods:
                demand = parent_plan.get(p, 0.0) * qty
                values[p] = demand
                if child not in self.dependent_demand:
                    self.dependent_demand[child] = {pp: 0.0 for pp in self.periods}
                self.dependent_demand[child][p] += demand
                if demand > 0: has = True
            
            if has:
                rows.append(PlanningRow(
                    material_number=child, material_name=child_mat.name,
                    product_type=child_mat.product_type.value, product_family=child_mat.product_family,
                    spc_product=child_mat.spc_product or '', product_cluster=child_mat.product_cluster or '',
                    product_name=child_mat.product_name or '', line_type=LineType.DEPENDENT_DEMAND.value,
                    aux_column=parent, aux_2_column=f"{qty:.6f}", values=values))
        
        rows.sort(key=lambda r: (int(r.material_number) if r.material_number.isdigit() else 0, r.aux_column or ''))
        self.results[LineType.DEPENDENT_DEMAND.value] = rows
        print(f"  [02]: {len(rows)} rows")
    
    def _calc_line_03(self):
        rows = []
        for mat_id in self.data.materials:
            f = self.forecast_data.get(mat_id, {})
            d = self.dependent_demand.get(mat_id, {})
            values = {p: f.get(p, 0) + d.get(p, 0) for p in self.periods}
            if any(v > 0 for v in values.values()):
                mat = self.data.materials.get(mat_id)
                if mat:
                    self.total_demand[mat_id] = values
                    rows.append(PlanningRow(
                        material_number=mat_id, material_name=mat.name,
                        product_type=mat.product_type.value, product_family=mat.product_family,
                        spc_product=mat.spc_product or '', product_cluster=mat.product_cluster or '',
                        product_name=mat.product_name or '', line_type=LineType.TOTAL_DEMAND.value,
                        values=values))
        rows.sort(key=lambda r: int(r.material_number) if r.material_number.isdigit() else 0)
        self.results[LineType.TOTAL_DEMAND.value] = rows
        print(f"  [03]: {len(rows)} rows")
    
    def _load_lines_04_07(self):
        df = pd.read_excel(self.xl, sheet_name='Planning sheet')
        df['Material number'] = df['Material number'].astype(str)
        pcols = [c for c in df.columns if hasattr(c, 'strftime')][:12]
        
        for lt, lt_enum in [('04. Inventory', LineType.INVENTORY), ('05. Minimum target stock', LineType.MIN_TARGET_STOCK),
                           ('06. Production plan', LineType.PRODUCTION_PLAN), ('06. Purchase receipt', LineType.PURCHASE_RECEIPT),
                           ('07. Purchase plan', LineType.PURCHASE_PLAN)]:
            rows = []
            for _, r in df[df['Line type'] == lt].iterrows():
                mat_id = str(r['Material number'])
                if mat_id == 'nan': continue
                mat = self.data.materials.get(mat_id)
                if not mat: continue
                values = {c.strftime('%Y-%m'): float(r[c]) if pd.notna(r[c]) else 0.0 for c in pcols}
                aux = str(r.get('Aux Column', '')) if pd.notna(r.get('Aux Column')) else ''
                aux2 = str(r.get('Aux 2 Column', '')) if pd.notna(r.get('Aux 2 Column')) else ''
                start = float(r.get('Starting stock', 0)) if pd.notna(r.get('Starting stock')) else 0.0
                rows.append(PlanningRow(
                    material_number=mat_id, material_name=mat.name,
                    product_type=mat.product_type.value, product_family=mat.product_family,
                    spc_product=mat.spc_product or '', product_cluster=mat.product_cluster or '',
                    product_name=mat.product_name or '', line_type=lt_enum.value,
                    aux_column=aux, aux_2_column=aux2, starting_stock=start, values=values))
            rows.sort(key=lambda x: int(x.material_number) if x.material_number.isdigit() else 0)
            self.results[lt_enum.value] = rows
            print(f"  [{lt[:2]}]: {len(rows)} rows")
    
    def _gen_line_08(self):
        rows = []
        for _, bom in self.bom_df.iterrows():
            parent, child, qty = str(bom['Material']), str(bom['Component']), bom['qty_per']
            if pd.isna(qty) or qty <= 0: continue
            parent_plan = self.production_plan.get(parent, {})
            if not parent_plan or not any(v > 0 for v in parent_plan.values()): continue
            child_mat = self.data.materials.get(child)
            if not child_mat: continue
            values = {p: parent_plan.get(p, 0.0) * qty for p in self.periods}
            if any(v > 0 for v in values.values()):
                rows.append(PlanningRow(
                    material_number=child, material_name=child_mat.name,
                    product_type=child_mat.product_type.value, product_family=child_mat.product_family,
                    spc_product=child_mat.spc_product or '', product_cluster=child_mat.product_cluster or '',
                    product_name=child_mat.product_name or '', line_type=LineType.DEPENDENT_REQUIREMENTS.value,
                    aux_column=parent, aux_2_column=f"{qty:.6f}", values=values))
        rows.sort(key=lambda r: (int(r.material_number) if r.material_number.isdigit() else 0, r.aux_column or ''))
        self.results[LineType.DEPENDENT_REQUIREMENTS.value] = rows
        print(f"  [08]: {len(rows)} rows")
    
    def _calc_capacity(self):
        cap = CapacityEngine(self.data, self.production_plan)
        for lt, rows in cap.calculate().items():
            rows.sort(key=lambda r: r.material_number)
            self.results[lt] = rows
            print(f"  [{lt[:2]}]: {len(rows)} rows")
    
    def _finalize(self):
        self.all_rows = []
        for lt in self.EXPECTED_LINE_TYPES:
            rows = self.results.get(lt, [])
            rows.sort(key=lambda r: int(r.material_number) if r.material_number.isdigit() else float('inf'))
            self.all_rows.extend(rows)
        self.summary = {'total_rows': len(self.all_rows), 'line_types_count': len([lt for lt, r in self.results.items() if r]),
                       'line_types': {lt: len(r) for lt, r in self.results.items()}, 'periods': self.periods}
        print(f"\nTotal: {len(self.all_rows)} rows")
    
    def get_all_rows(self): return self.all_rows
    def get_rows_by_type(self, lt): return self.results.get(lt, [])
    def get_summary(self): return self.summary
    
    def to_dataframe(self):
        rows = []
        for r in self.all_rows:
            d = {'Material number': r.material_number, 'Material name': r.material_name,
                 'Product type': r.product_type, 'Product family': r.product_family,
                 'Line type': r.line_type, 'Aux Column': r.aux_column, 'Aux 2 Column': r.aux_2_column,
                 'Starting stock': r.starting_stock}
            d.update(r.values)
            rows.append(d)
        return pd.DataFrame(rows)
    
    def to_excel(self, path):
        self.to_dataframe().to_excel(path, index=False)
    
    def to_json(self):
        return {'summary': self.summary, 'periods': self.periods,
                'results': {lt: [r.to_dict() for r in rows] for lt, rows in self.results.items()}}
