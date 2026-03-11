"""
S&OP Planning Engine - Main Orchestrator
Processes materials in BOM topological order (level by level).

For each BOM level:
  1. Aggregate dependent demand from parent levels
  2. Calculate total demand, target stock, production/purchase plan, inventory
  3. Compute dependent requirements -> feed to next level

Then: capacity utilization, shift availability, available capacity, utilization rate, FTE
"""

import pandas as pd
from typing import Dict, List, Optional
from datetime import datetime
from pathlib import Path
from collections import defaultdict

from modules.models import PlanningRow, LineType
from modules.data_loader import DataLoader
from modules.forecast_engine import ForecastEngine
from modules.bom_engine import BOMEngine
from modules.inventory_engine import InventoryEngine
from modules.capacity_engine import CapacityEngine
from modules.value_planning_engine import ValuePlanningEngine


class PlanningEngine:
    """Main orchestrator that runs all planning calculations."""

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
        LineType.CONSOLIDATION.value,
    ]

    def __init__(self, file_path: str, planning_month: str = None,
                 months_actuals: int = 0, months_forecast: int = 12):
        self.file_path = file_path
        self.planning_month = planning_month
        self.months_actuals = months_actuals
        self.months_forecast = months_forecast
        self.data: Optional[DataLoader] = None

        self.results: Dict[str, List[PlanningRow]] = {lt: [] for lt in self.EXPECTED_LINE_TYPES}
        self.all_rows: List[PlanningRow] = []
        self.summary: Dict = {}

        # Cross-material tracking
        self.all_production_plans: Dict[str, Dict[str, float]] = {}
        self.all_purchase_receipts: Dict[str, Dict[str, float]] = {}
        self.all_total_demands: Dict[str, Dict[str, float]] = {}
        
        # Value planning (NEW)
        self.value_results: Dict[str, List[PlanningRow]] = {}
        self.value_engine: Optional[ValuePlanningEngine] = None

    def run(self) -> 'PlanningEngine':
        """Run the complete planning pipeline."""
        print("\n" + "=" * 70)
        print("S&OP PLANNING ENGINE - FULL CALCULATION")
        print("=" * 70)

        # ===== STEP 1: Load data =====
        print("\n[STEP 1] Loading raw input data...")
        self.data = DataLoader(self.file_path)
        self.data.load_all()

        # ===== STEP 1b: Apply UI parameter overrides =====
        # All three UI values (planning_month, months_forecast, months_actuals) must be
        # applied here — before any engine is instantiated — so that data.periods and
        # data.forecast_actuals_months are the single source of truth for the full
        # pipeline.  Engines read these attributes directly; none of them re-read the
        # Excel Config sheet after this point.
        #
        # planning_month  → data.config.initial_date  → period window start
        #                                              → actuals/forecast boundary
        #                                              → opening inventory date
        # months_forecast → data.config.forecast_months → length of data.periods
        #                                              → every engine's period loop
        # months_actuals  → data.forecast_actuals_months → Aux2 start index in
        #                                              ForecastEngine._calculate_aux_columns

        if self.planning_month:
            try:
                pm = datetime.strptime(self.planning_month, '%Y/%m')
                self.data.config.initial_date = pm
                print(f"  >> planning_month override: start={pm.strftime('%Y-%m')}")
            except ValueError:
                print(f"  >> WARNING: Could not parse planning_month='{self.planning_month}' "
                      f"(expected YYYY/MM) — using date from Excel Config sheet")

        if self.months_forecast > 0:
            self.data.config.forecast_months = self.months_forecast
            print(f"  >> months_forecast override: {self.months_forecast} periods")

        # Regenerate periods once — after both start-date and length are finalised.
        self.data.periods = self.data.config.get_periods()

        # months_actuals = 0 means "keep the Excel Config value" (existing convention).
        if self.months_actuals > 0:
            self.data.forecast_actuals_months = self.months_actuals
            print(f"  >> months_actuals override: {self.months_actuals} actuals months")

        print(f"  >> Final horizon: {len(self.data.periods)} periods starting "
              f"{self.data.periods[0] if self.data.periods else 'N/A'}, "
              f"actuals={self.data.forecast_actuals_months}")

        # ===== STEP 2: Demand Forecast (Line 01) =====
        print("\n[STEP 2] Calculating Demand Forecast (Line 01)...")
        # Use config values if not explicitly overridden
        actuals_months = self.months_actuals if self.months_actuals > 0 else getattr(self.data, 'forecast_actuals_months', 12)
        forecast_months = self.months_forecast if self.months_forecast > 0 else self.data.config.forecast_months
        print(f"  >> USING: actuals_months={actuals_months} (input={self.months_actuals}), forecast_months={forecast_months} (input={self.months_forecast})")
        forecast_engine = ForecastEngine(self.data, actuals_months, forecast_months)
        forecast_rows = forecast_engine.calculate()
        self.results[LineType.DEMAND_FORECAST.value] = forecast_rows
        forecasts = forecast_engine.get_all_forecasts()

        # ===== STEP 3: BOM structure analysis =====
        print("\n[STEP 3] Analyzing BOM structure...")
        bom_engine = BOMEngine(self.data)

        # ===== STEP 4: Level-by-level calculation =====
        print("\n[STEP 4] Level-by-level planning calculation...")
        inv_engine = InventoryEngine(self.data)
        periods = self.data.periods

        # Accumulate dependent demand for each material from parents
        dep_demand_by_parent: Dict[str, Dict[str, Dict[str, float]]] = defaultdict(
            lambda: defaultdict(lambda: {p: 0.0 for p in periods})
        )
        dep_demand_agg: Dict[str, Dict[str, float]] = defaultdict(
            lambda: {p: 0.0 for p in periods}
        )

        max_level = bom_engine.get_max_level()

        for level in range(max_level + 1):
            materials_at_level = bom_engine.get_materials_at_level(level)
            print(f"\n  --- Level {level}: {len(materials_at_level)} materials ---")

            for mat_num in materials_at_level:
                material = self.data.materials.get(mat_num)
                if not material:
                    continue

                mat_forecast = forecasts.get(mat_num, {})
                mat_dep_agg = dict(dep_demand_agg.get(mat_num, {p: 0.0 for p in periods}))
                mat_dep_by_parent = dict(dep_demand_by_parent.get(mat_num, {}))

                # Create dependent demand rows (Line 02)
                if mat_dep_by_parent:
                    dd_rows = bom_engine.create_dependent_demand_rows(
                        mat_num, mat_dep_by_parent
                    )
                    self.results[LineType.DEPENDENT_DEMAND.value].extend(dd_rows)

                # Calculate inventory-related lines for this material
                result = inv_engine.calculate_for_material(
                    mat_num, mat_forecast, mat_dep_agg, mat_dep_by_parent
                )

                for row in result['rows']:
                    lt = row.line_type
                    if lt in self.results:
                        self.results[lt].append(row)

                if result['production_plan'] is not None:
                    self.all_production_plans[mat_num] = result['production_plan']
                if result['purchase_receipt'] is not None:
                    self.all_purchase_receipts[mat_num] = result['purchase_receipt']
                if result['total_demand']:
                    self.all_total_demands[mat_num] = result['total_demand']

                # ===== Compute dependent requirements (Line 08) =====
                # Always generate dep requirements if material has BOM children
                # (even if production plan is all zeros - Excel does this)
                prod_plan = result['production_plan']
                if prod_plan is not None:
                    children_demand = bom_engine.compute_dependent_requirements(
                        mat_num, prod_plan
                    )

                    if children_demand:
                        dr_rows = bom_engine.create_dependent_requirements_rows(
                            mat_num, children_demand
                        )
                        self.results[LineType.DEPENDENT_REQUIREMENTS.value].extend(dr_rows)

                        for child, child_period_data in children_demand.items():
                            for period in periods:
                                dep_demand_agg[child][period] += child_period_data.get(period, 0.0)
                            dep_demand_by_parent[child][mat_num] = child_period_data

        # ===== STEP 4b: Process standalone safety stock materials =====
        bom_processed = set()
        for level in range(max_level + 1):
            bom_processed.update(bom_engine.get_materials_at_level(level))

        standalone_mats = [
            mat_num for mat_num in self.data.safety_stock
            if mat_num not in bom_processed and mat_num in self.data.materials
        ]

        if standalone_mats:
            print(f"\n  --- Standalone safety stock materials: {len(standalone_mats)} ---")
            for mat_num in standalone_mats:
                mat_forecast = forecasts.get(mat_num, {})
                empty_demand = {p: 0.0 for p in periods}

                result = inv_engine.calculate_for_material(
                    mat_num, mat_forecast, empty_demand, {}
                )

                for row in result['rows']:
                    lt = row.line_type
                    if lt in self.results:
                        self.results[lt].append(row)

                if result['production_plan'] is not None:
                    self.all_production_plans[mat_num] = result['production_plan']
                if result['purchase_receipt'] is not None:
                    self.all_purchase_receipts[mat_num] = result['purchase_receipt']
                if result['total_demand']:
                    self.all_total_demands[mat_num] = result['total_demand']

        # ===== STEP 5: Capacity calculations =====
        print("\n[STEP 5] Calculating capacity...")
        capacity_engine = CapacityEngine(self.data, self.all_production_plans)
        capacity_results = capacity_engine.calculate()
        for line_type, rows in capacity_results.items():
            self.results[line_type] = rows
        
        # ===== STEP 6: Value planning calculations =====
        print("\n[STEP 6] Calculating value planning...")
        self.value_engine = ValuePlanningEngine(self.data, self.results)
        self.value_results = self.value_engine.calculate()

        # ===== Compile and validate =====
        self._compile_all_rows()
        self._validate_output()
        self._generate_summary()

        print("\n" + "=" * 70)
        print("CALCULATION COMPLETE")
        print("=" * 70)
        self._print_summary()

        return self

    def _compile_all_rows(self):
        self.all_rows = []
        for line_type in self.EXPECTED_LINE_TYPES:
            if line_type in self.results:
                self.all_rows.extend(self.results[line_type])

    def _validate_output(self):
        print("\n[VALIDATION] Checking output...")
        active_types = [lt for lt, rows in self.results.items() if len(rows) > 0]
        print(f"  Line types with data: {len(active_types)}/{len(self.EXPECTED_LINE_TYPES)}")

        for lt in self.EXPECTED_LINE_TYPES:
            count = len(self.results.get(lt, []))
            status = "V" if count > 0 else "X"
            print(f"    {status} {lt}: {count} rows")

        if len(active_types) < 10:
            print(f"  WARNING: Only {len(active_types)} line types have data")
        else:
            print("  Validation passed")

    def _generate_summary(self):
        self.summary = {
            'total_rows': len(self.all_rows),
            'line_types_count': len([lt for lt, rows in self.results.items() if rows]),
            'line_types': {lt: len(rows) for lt, rows in self.results.items()},
            'materials': len(self.data.materials),
            'bom_items': len(self.data.bom),
            'machines': len(self.data.machines),
            'machine_groups': len(self.data.machine_groups),
            'periods': len(self.data.periods),
            'period_list': self.data.periods,
        }

    def _print_summary(self):
        print(f"\nSummary:")
        print(f"  Total rows: {self.summary['total_rows']}")
        print(f"  Line types: {self.summary['line_types_count']}")
        print(f"  Periods: {self.summary['periods']}")
        print(f"\nBreakdown by line type:")
        for lt in self.EXPECTED_LINE_TYPES:
            count = self.summary['line_types'].get(lt, 0)
            print(f"  {lt}: {count}")

    # ===== Export methods =====
    def get_all_rows(self) -> List[PlanningRow]:
        return self.all_rows

    def get_rows_by_type(self, line_type: str) -> List[PlanningRow]:
        return self.results.get(line_type, [])

    def get_summary(self) -> Dict:
        return self.summary

    def to_dataframe(self) -> pd.DataFrame:
        rows_data = []
        for row in self.all_rows:
            row_dict = {
                'Material number': row.material_number,
                'Material name': row.material_name,
                'Product type': row.product_type,
                'Product family': row.product_family,
                'SPC product': row.spc_product,
                'Product cluster': row.product_cluster,
                'Product name': row.product_name,
                'Line type': row.line_type,
                'Aux Column': row.aux_column,
                'Aux 2 Column': row.aux_2_column,
                'Starting stock': row.starting_stock,
            }
            for period, value in row.values.items():
                row_dict[period] = value
            rows_data.append(row_dict)
        return pd.DataFrame(rows_data)

    def to_excel(self, output_path: str):
        df = self.to_dataframe()
        df.to_excel(output_path, sheet_name='Planning Results', index=False)
        print(f"\nResults exported to: {output_path}")
    
    def to_excel_with_values(self, output_path: str):
        """Export both volume planning and value planning to Excel."""
        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            # Volume planning sheet
            df_volumes = self.to_dataframe()
            df_volumes.to_excel(writer, sheet_name='Planning sheet', index=False)
            
            # Value planning sheet
            value_rows = []
            for line_type, rows in self.value_results.items():
                for row in rows:
                    row_dict = {
                        'Material number': row.material_number,
                        'Material name': row.material_name,
                        'Product type': row.product_type,
                        'Product family': row.product_family,
                        'SPC product': row.spc_product,
                        'Product cluster': row.product_cluster,
                        'Product name': row.product_name,
                        'Line type': row.line_type,
                        'Aux Column': row.aux_column,
                        'Aux 2 Column': row.aux_2_column,
                        'Starting stock': row.starting_stock,
                    }
                    for period, value in row.values.items():
                        row_dict[period] = value
                    value_rows.append(row_dict)
            
            df_values = pd.DataFrame(value_rows)
            df_values.to_excel(writer, sheet_name='Values_Planning sheet', index=False)
        
        print(f"\nResults exported to: {output_path}")
        print(f"  - Planning sheet (volumes)")
        print(f"  - Values_Planning sheet (financial)")

    def to_json(self) -> Dict:
        return {
            'summary': self.summary,
            'periods': self.data.periods,
            'results': {
                lt: [row.to_dict() for row in rows]
                for lt, rows in self.results.items()
            }
        }
