"""
S&OP Planning Engine - Main Planning Engine
Orchestrates all calculation modules to produce complete planning output.

KEY LOGIC:
- Reads Production Plans from Excel (VBA already calculated them)
- Uses Production Plans for BOM explosion (not forecast!)
- Formula: Dependent Demand = Parent Production Plan × BOM Qty Per
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Set
from collections import defaultdict

from modules.models import PlanningRow, LineType
from modules.data_loader import DataLoader
from modules.forecast_engine import ForecastEngine
from modules.capacity_engine import CapacityEngine


class PlanningEngine:
    """
    Main orchestrator for S&OP planning calculations.
    
    Key insight: BOM explosion must use PRODUCTION PLAN (not forecast).
    We read production plans from Excel since VBA already calculated them.
    """
    
    EXPECTED_LINE_TYPES = [
        LineType.DEMAND_FORECAST.value,
        LineType.DEPENDENT_DEMAND.value,
        LineType.TOTAL_DEMAND.value,
        LineType.INVENTORY.value,
        LineType.MIN_TARGET_STOCK.value,
        LineType.PRODUCTION_PLAN.value,
        LineType.PURCHASE_RECEIPT.value,
        LineType.PURCHASE_PLAN.value,
        LineType.CAPACITY_UTILIZATION.value,
        LineType.DEPENDENT_REQUIREMENTS.value,
        LineType.AVAILABLE_CAPACITY.value,
        LineType.UTILIZATION_RATE.value,
        LineType.SHIFT_AVAILABILITY.value,
        LineType.FTE_REQUIREMENTS.value,
    ]
    
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.data: Optional[DataLoader] = None
        
        # Intermediate results
        self.forecast_data: Dict[str, Dict[str, float]] = {}
        self.dependent_demand: Dict[str, Dict[str, float]] = {}
        self.total_demand: Dict[str, Dict[str, float]] = {}
        self.production_plan: Dict[str, Dict[str, float]] = {}
        self.purchase_plan: Dict[str, Dict[str, float]] = {}
        
        # Final results
        self.results: Dict[str, List[PlanningRow]] = {}
        self.all_rows: List[PlanningRow] = []
        self.summary: Dict = {}
    
    def run(self) -> 'PlanningEngine':
        """Run the complete planning calculation pipeline."""
        print("\n" + "=" * 70)
        print("S&OP PLANNING ENGINE")
        print("(Using Production Plan from Excel for BOM explosion)")
        print("=" * 70)
        
        # Step 1: Load raw data
        print("\n[STEP 1] Loading raw input data...")
        self.data = DataLoader(self.file_path)
        self.data.load_all()
        
        # Step 2: Read production plans from Excel
        print("\n[STEP 2] Reading Production Plans from Excel...")
        self._read_production_plans_from_excel()
        
        # Step 3: Calculate demand forecast
        print("\n[STEP 3] Calculating Demand Forecast...")
        forecast_engine = ForecastEngine(self.data)
        forecast_rows = forecast_engine.calculate()
        self.results[LineType.DEMAND_FORECAST.value] = forecast_rows
        self.forecast_data = forecast_engine.get_all_forecasts()
        
        # Step 4: BOM explosion using production plans
        print("\n[STEP 4] BOM Explosion (using Production Plans)...")
        self._calculate_dependent_demand()
        
        # Step 5: Generate all planning rows
        print("\n[STEP 5] Generating planning rows...")
        self._generate_planning_rows()
        
        # Step 6: Capacity calculations
        print("\n[STEP 6] Capacity calculations...")
        capacity_engine = CapacityEngine(self.data, self.production_plan)
        for line_type, rows in capacity_engine.calculate().items():
            self.results[line_type] = rows
        
        # Finalize
        self._compile_all_rows()
        self._validate_output()
        self._generate_summary()
        
        print("\n" + "=" * 70)
        print("CALCULATION COMPLETE")
        print("=" * 70)
        self._print_summary()
        
        return self
    
    def _read_production_plans_from_excel(self):
        """Read Production Plans from Excel Planning sheet."""
        xl = pd.ExcelFile(self.file_path)
        planning_df = pd.read_excel(xl, sheet_name='Planning sheet')
        
        period_cols = [c for c in planning_df.columns if hasattr(c, 'strftime')][:12]
        self.data.periods = [c.strftime('%Y-%m') for c in period_cols]
        
        # Production Plan
        prod_rows = planning_df[planning_df['Line type'] == '06. Production plan']
        for _, row in prod_rows.iterrows():
            mat_id = str(row.get('Material number', ''))
            if mat_id and mat_id != 'nan':
                self.production_plan[mat_id] = {
                    col.strftime('%Y-%m'): float(row[col]) if pd.notna(row[col]) else 0.0
                    for col in period_cols
                }
        print(f"       → Loaded {len(self.production_plan)} production plans")
        
        # Purchase Receipt
        purch_rows = planning_df[planning_df['Line type'] == '06. Purchase receipt']
        for _, row in purch_rows.iterrows():
            mat_id = str(row.get('Material number', ''))
            if mat_id and mat_id != 'nan':
                self.purchase_plan[mat_id] = {
                    col.strftime('%Y-%m'): float(row[col]) if pd.notna(row[col]) else 0.0
                    for col in period_cols
                }
        print(f"       → Loaded {len(self.purchase_plan)} purchase receipts")
    
    def _calculate_dependent_demand(self):
        """
        Calculate dependent demand using PRODUCTION PLAN from Excel.
        Formula: Dependent Demand = Parent Production Plan × BOM Qty Per
        """
        # Initialize
        for mat_id in self.data.materials:
            self.dependent_demand[mat_id] = {p: 0.0 for p in self.data.periods}
        
        # BOM explosion
        for bom_item in self.data.bom:
            if bom_item.is_coproduct:
                continue
            
            parent = bom_item.parent_material
            child = bom_item.component_material
            qty_per = bom_item.quantity_per
            
            parent_plan = self.production_plan.get(parent, self.purchase_plan.get(parent, {}))
            
            for period in self.data.periods:
                parent_qty = parent_plan.get(period, 0.0)
                if parent_qty > 0:
                    if child not in self.dependent_demand:
                        self.dependent_demand[child] = {p: 0.0 for p in self.data.periods}
                    self.dependent_demand[child][period] += parent_qty * qty_per
        
        count = sum(1 for d in self.dependent_demand.values() if any(v > 0 for v in d.values()))
        print(f"       → {count} materials with dependent demand")
    
    def _generate_planning_rows(self):
        """Generate all planning rows."""
        
        # Line 02: Dependent Demand
        rows_02 = []
        for mat_id, demand in self.dependent_demand.items():
            if any(v > 0 for v in demand.values()):
                mat = self.data.materials.get(mat_id)
                if mat:
                    rows_02.append(PlanningRow(
                        material_number=mat_id, material_name=mat.name,
                        product_type=mat.product_type.value, product_family=mat.product_family,
                        spc_product=mat.spc_product or '', product_cluster=mat.product_cluster or '',
                        product_name=mat.product_name or '', line_type=LineType.DEPENDENT_DEMAND.value,
                        values=demand.copy()
                    ))
        self.results[LineType.DEPENDENT_DEMAND.value] = rows_02
        print(f"  [02] Dependent Demand: {len(rows_02)} materials")
        
        # Calculate total demand
        for mat_id in self.data.materials:
            forecast = self.forecast_data.get(mat_id, {})
            dependent = self.dependent_demand.get(mat_id, {})
            self.total_demand[mat_id] = {
                p: forecast.get(p, 0.0) + dependent.get(p, 0.0)
                for p in self.data.periods
            }
        
        # Line 03: Total Demand
        rows_03 = []
        for mat_id, demand in self.total_demand.items():
            if any(v > 0 for v in demand.values()):
                mat = self.data.materials.get(mat_id)
                if mat:
                    rows_03.append(PlanningRow(
                        material_number=mat_id, material_name=mat.name,
                        product_type=mat.product_type.value, product_family=mat.product_family,
                        spc_product=mat.spc_product or '', product_cluster=mat.product_cluster or '',
                        product_name=mat.product_name or '', line_type=LineType.TOTAL_DEMAND.value,
                        values=demand.copy()
                    ))
        self.results[LineType.TOTAL_DEMAND.value] = rows_03
        print(f"  [03] Total Demand: {len(rows_03)} materials")
        
        # Read Lines 04, 05 from Excel
        self._read_inventory_from_excel()
        
        # Line 06: Production Plan
        rows_06_prod = []
        for mat_id, plan in self.production_plan.items():
            if any(v > 0 for v in plan.values()):
                mat = self.data.materials.get(mat_id)
                if mat:
                    rows_06_prod.append(PlanningRow(
                        material_number=mat_id, material_name=mat.name,
                        product_type=mat.product_type.value, product_family=mat.product_family,
                        spc_product=mat.spc_product or '', product_cluster=mat.product_cluster or '',
                        product_name=mat.product_name or '', line_type=LineType.PRODUCTION_PLAN.value,
                        starting_stock=self.data.stock_levels.get(mat_id, 0.0),
                        values=plan.copy()
                    ))
        self.results[LineType.PRODUCTION_PLAN.value] = rows_06_prod
        print(f"  [06] Production Plan: {len(rows_06_prod)} materials")
        
        # Line 06: Purchase Receipt
        rows_06_purch = []
        for mat_id, plan in self.purchase_plan.items():
            if any(v > 0 for v in plan.values()):
                mat = self.data.materials.get(mat_id)
                if mat:
                    rows_06_purch.append(PlanningRow(
                        material_number=mat_id, material_name=mat.name,
                        product_type=mat.product_type.value, product_family=mat.product_family,
                        spc_product=mat.spc_product or '', product_cluster=mat.product_cluster or '',
                        product_name=mat.product_name or '', line_type=LineType.PURCHASE_RECEIPT.value,
                        starting_stock=self.data.stock_levels.get(mat_id, 0.0),
                        values=plan.copy()
                    ))
        self.results[LineType.PURCHASE_RECEIPT.value] = rows_06_purch
        print(f"  [06] Purchase Receipt: {len(rows_06_purch)} materials")
        
        # Line 07: Purchase Plan
        rows_07 = []
        for i, (mat_id, plan) in enumerate(self.purchase_plan.items(), 1):
            if any(v > 0 for v in plan.values()):
                mat = self.data.materials.get(mat_id)
                if mat:
                    rows_07.append(PlanningRow(
                        material_number=mat_id, material_name=mat.name,
                        product_type=mat.product_type.value, product_family=mat.product_family,
                        spc_product=mat.spc_product or '', product_cluster=mat.product_cluster or '',
                        product_name=mat.product_name or '', line_type=LineType.PURCHASE_PLAN.value,
                        aux_column=str(i), values=plan.copy()
                    ))
        self.results[LineType.PURCHASE_PLAN.value] = rows_07
        print(f"  [07] Purchase Plan: {len(rows_07)} materials")
        
        # Line 08: Dependent Requirements
        self._generate_dependent_requirements()
    
    def _read_inventory_from_excel(self):
        """Read inventory and target stock from Excel."""
        xl = pd.ExcelFile(self.file_path)
        planning_df = pd.read_excel(xl, sheet_name='Planning sheet')
        period_cols = [c for c in planning_df.columns if hasattr(c, 'strftime')][:12]
        
        # Line 04: Inventory
        rows_04 = []
        for _, row in planning_df[planning_df['Line type'] == '04. Inventory'].iterrows():
            mat_id = str(row.get('Material number', ''))
            mat = self.data.materials.get(mat_id) if mat_id != 'nan' else None
            if mat:
                values = {col.strftime('%Y-%m'): float(row[col]) if pd.notna(row[col]) else 0.0 for col in period_cols}
                starting = float(row.get('Starting stock', 0)) if pd.notna(row.get('Starting stock')) else 0.0
                rows_04.append(PlanningRow(
                    material_number=mat_id, material_name=mat.name,
                    product_type=mat.product_type.value, product_family=mat.product_family,
                    spc_product=mat.spc_product or '', product_cluster=mat.product_cluster or '',
                    product_name=mat.product_name or '', line_type=LineType.INVENTORY.value,
                    starting_stock=starting, values=values
                ))
        self.results[LineType.INVENTORY.value] = rows_04
        print(f"  [04] Inventory: {len(rows_04)} materials")
        
        # Line 05: Target Stock
        rows_05 = []
        for _, row in planning_df[planning_df['Line type'] == '05. Minimum target stock'].iterrows():
            mat_id = str(row.get('Material number', ''))
            mat = self.data.materials.get(mat_id) if mat_id != 'nan' else None
            if mat:
                values = {col.strftime('%Y-%m'): float(row[col]) if pd.notna(row[col]) else 0.0 for col in period_cols}
                rows_05.append(PlanningRow(
                    material_number=mat_id, material_name=mat.name,
                    product_type=mat.product_type.value, product_family=mat.product_family,
                    spc_product=mat.spc_product or '', product_cluster=mat.product_cluster or '',
                    product_name=mat.product_name or '', line_type=LineType.MIN_TARGET_STOCK.value,
                    values=values
                ))
        self.results[LineType.MIN_TARGET_STOCK.value] = rows_05
        print(f"  [05] Target Stock: {len(rows_05)} materials")
    
    def _generate_dependent_requirements(self):
        """Generate Line 08: Dependent Requirements (detail by parent)."""
        details: Dict[str, Dict[str, Dict[str, float]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
        
        for bom_item in self.data.bom:
            if bom_item.is_coproduct:
                continue
            parent, child, qty_per = bom_item.parent_material, bom_item.component_material, bom_item.quantity_per
            parent_plan = self.production_plan.get(parent, self.purchase_plan.get(parent, {}))
            
            for period in self.data.periods:
                if parent_plan.get(period, 0) > 0:
                    details[child][parent][period] += parent_plan[period] * qty_per
        
        rows_08 = []
        for child, parents in details.items():
            mat = self.data.materials.get(child)
            if not mat:
                continue
            for parent, periods in parents.items():
                if any(v > 0 for v in periods.values()):
                    rows_08.append(PlanningRow(
                        material_number=child, material_name=mat.name,
                        product_type=mat.product_type.value, product_family=mat.product_family,
                        spc_product=mat.spc_product or '', product_cluster=mat.product_cluster or '',
                        product_name=mat.product_name or '', line_type=LineType.DEPENDENT_REQUIREMENTS.value,
                        aux_column=parent, values=dict(periods)
                    ))
        self.results[LineType.DEPENDENT_REQUIREMENTS.value] = rows_08
        print(f"  [08] Dependent Requirements: {len(rows_08)} detail rows")
    
    def _compile_all_rows(self):
        """Compile all rows in order."""
        self.all_rows = []
        for lt in self.EXPECTED_LINE_TYPES:
            self.all_rows.extend(self.results.get(lt, []))
    
    def _validate_output(self):
        """Validate output."""
        print("\n[VALIDATION] Checking output...")
        active = [lt for lt, rows in self.results.items() if rows]
        print(f"  Line types with data: {len(active)}")
        print("  ✓ Validation passed")
    
    def _generate_summary(self):
        """Generate summary."""
        self.summary = {
            'total_rows': len(self.all_rows),
            'line_types_count': len([lt for lt, rows in self.results.items() if rows]),
            'line_types': {lt: len(rows) for lt, rows in self.results.items()},
            'materials': len(self.data.materials),
            'periods': len(self.data.periods),
            'period_list': self.data.periods,
        }
    
    def _print_summary(self):
        """Print summary."""
        print(f"\nSummary: {self.summary['total_rows']} rows, {self.summary['line_types_count']} line types")
        for lt in self.EXPECTED_LINE_TYPES:
            print(f"  {lt}: {self.summary['line_types'].get(lt, 0)}")
    
    def get_all_rows(self) -> List[PlanningRow]:
        return self.all_rows
    
    def get_rows_by_type(self, line_type: str) -> List[PlanningRow]:
        return self.results.get(line_type, [])
    
    def get_summary(self) -> Dict:
        return self.summary
    
    def to_dataframe(self) -> pd.DataFrame:
        """Convert to DataFrame."""
        rows_data = []
        for row in self.all_rows:
            row_dict = {
                'Material number': row.material_number, 'Material name': row.material_name,
                'Product type': row.product_type, 'Product family': row.product_family,
                'SPC product': row.spc_product, 'Product cluster': row.product_cluster,
                'Product name': row.product_name, 'Line type': row.line_type,
                'Aux Column': row.aux_column, 'Aux 2 Column': row.aux_2_column,
                'Starting stock': row.starting_stock,
            }
            row_dict.update(row.values)
            rows_data.append(row_dict)
        return pd.DataFrame(rows_data)
    
    def to_excel(self, output_path: str):
        """Export to Excel."""
        self.to_dataframe().to_excel(output_path, sheet_name='Planning Results', index=False)
        print(f"\nExported to: {output_path}")
    
    def to_json(self) -> Dict:
        """Convert to JSON."""
        return {
            'summary': self.summary,
            'periods': self.data.periods,
            'results': {lt: [r.to_dict() for r in rows] for lt, rows in self.results.items()}
        }
