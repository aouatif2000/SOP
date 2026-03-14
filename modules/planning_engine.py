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
try:
    from modules.inventory_quality_engine import InventoryQualityEngine as _IQEngine
except ImportError:
    _IQEngine = None
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
        # NLI1 Exception 2: B15 production plan adjustment (VBA Exceptions_GTB lines 1873+)
        # Must run AFTER all materials processed, BEFORE CapacityEngine.
        if self.data.config and getattr(self.data.config, 'site', None) == 'NLI1':
            B15   = '600004811'
            B4010 = '600004831'
            b4010_prod = self.all_production_plans.get(B4010, {})
            b15_prod   = self.all_production_plans.get(B15)
            if b15_prod is not None and b4010_prod:
                # Subtract B4010 production from B15 production plan (floor at 0)
                for p in self.data.periods:
                    b15_prod[p] = max(0.0, b15_prod[p] - b4010_prod.get(p, 0.0))
                # Update the Line 06 Production plan row for B15
                for row in self.results.get(LineType.PRODUCTION_PLAN.value, []):
                    if row.material_number == B15:
                        row.values = dict(b15_prod)
                        break
                # Recalculate B15 inventory: B4010 production counts as additional supply
                b15_inv_rows = [r for r in self.results.get(LineType.INVENTORY.value, [])
                                if r.material_number == B15]
                if b15_inv_rows:
                    b15_inv    = b15_inv_rows[0]
                    b15_demand = self.all_total_demands.get(B15, {})
                    b15_purch  = self.all_purchase_receipts.get(B15) or {}
                    running    = b15_inv.starting_stock
                    for p in self.data.periods:
                        demand           = b15_demand.get(p, 0.0)
                        prod             = b15_prod.get(p, 0.0)
                        purch            = b15_purch.get(p, 0.0)
                        b4010_contrib    = b4010_prod.get(p, 0.0)
                        running          = running - demand + prod + purch + b4010_contrib
                        b15_inv.values[p] = running
                print(f"[NLI1] Exception 2 applied: B15 production adjusted by B4010 contribution.")

        print("\n[STEP 5] Calculating capacity...")
        # Truck hours use '01. Demand forecast' volumes (VBA TruckOperationsFormulas SUMIFS)
        l01_rows = self.results.get('01. Demand forecast', [])
        l01_forecasts = {r.material_number: r.values for r in l01_rows}
        capacity_engine = CapacityEngine(self.data, self.all_production_plans, l01_forecasts)
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
        df = pd.DataFrame(rows_data)
        # VBA SortPlanningSheet (line 4811): sort by material number ASC, line type ASC, aux1 ASC, aux2 ASC
        if not df.empty:
            df = df.sort_values(
                by=['Material number', 'Line type', 'Aux Column', 'Aux 2 Column'],
                ascending=[True, True, True, True],
                na_position='last',
                key=lambda col: col.astype(str) if col.name in ['Material number', 'Aux Column', 'Aux 2 Column'] else col
            ).reset_index(drop=True)
        # VBA DeleteDoubleProcessRowsPackagedMaterials (line 1757): keep first occurrence of each
        # (material_number, line_type, aux1, aux2) combination — sort above determines which is "first"
        if not df.empty:
            df = df.drop_duplicates(
                subset=['Material number', 'Line type', 'Aux Column', 'Aux 2 Column'],
                keep='first'
            ).reset_index(drop=True)
        return df

    def to_excel(self, output_path: str):
        df = self.to_dataframe()
        df.to_excel(output_path, sheet_name='Planning Results', index=False)
        print(f"\nResults exported to: {output_path}")
    
    def to_excel_with_values(self, output_path: str, inventory_quality_engine=None):
        """Export both volume planning and value planning to Excel with VBA-matching formatting."""
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

            # FTE requirements sheet (Line 12 rows)
            from modules.models import LineType
            fte_line = LineType.FTE_REQUIREMENTS.value
            fte_rows_data = self.results.get(fte_line, [])
            fte_records = []
            for row in fte_rows_data:
                rec = {
                    'Material number': row.material_number,
                    'Group name': row.material_name,
                    'FTE needed': row.aux_column,
                }
                for period, val in row.values.items():
                    rec[period] = round(val, 2)
                fte_records.append(rec)

            if fte_records:
                df_fte = pd.DataFrame(fte_records)
                period_cols = [c for c in df_fte.columns if str(c).count('-') == 1 and len(str(c)) == 7]

                # Add Average column
                if period_cols:
                    df_fte['Average'] = df_fte[period_cols].mean(axis=1).round(2)

                # Add TOTAL row
                total_rec = {'Material number': 'TOTAL', 'Group name': 'TOTAL', 'FTE needed': ''}
                for p in period_cols:
                    total_rec[p] = round(df_fte[p].sum(), 2)
                if period_cols:
                    total_rec['Average'] = round(df_fte['Average'].sum(), 2)
                df_fte = pd.concat([df_fte, pd.DataFrame([total_rec])], ignore_index=True)

                df_fte.to_excel(writer, sheet_name='FTE requirements', index=False)

                # Format FTE sheet
                ws_fte = writer.book['FTE requirements']
                self._apply_fte_formatting(ws_fte, period_cols)

            # Apply VBA-matching formatting to both sheets
            wb = writer.book
            self._apply_excel_formatting(wb['Planning sheet'])
            if not df_values.empty:
                self._apply_excel_formatting(wb['Values_Planning sheet'])

            # ---- High-level overview sheet (VBA CreateHighLevelOverview line 2804) ----
            from openpyxl.chart import BarChart, LineChart, Reference
            from openpyxl.chart.series import SeriesLabel

            ws_overview = wb.create_sheet('High-level overview')
            ws_overview.sheet_properties.tabColor = '8B4513'  # brown tab matching VBA

            # Collect consolidation rows
            consol_rows = []
            for lt, rows in self.value_results.items():
                for row in rows:
                    if row.line_type == LineType.CONSOLIDATION.value:
                        consol_rows.append(row)

            # Write header row: Metric | Summary | period1 | period2 | ...
            ws_overview['A1'] = 'Metric'
            ws_overview['B1'] = 'Summary'
            for i, p in enumerate(self.data.periods):
                ws_overview.cell(row=1, column=3 + i, value=p)

            # Write consolidation data rows
            for r_idx, row in enumerate(consol_rows, start=2):
                ws_overview.cell(row=r_idx, column=1, value=row.material_number.replace('ZZZZZZ_', ''))
                try:
                    ws_overview.cell(row=r_idx, column=2, value=float(row.aux_2_column) if row.aux_2_column else 0)
                except (ValueError, TypeError):
                    ws_overview.cell(row=r_idx, column=2, value=0)
                for i, p in enumerate(self.data.periods):
                    ws_overview.cell(row=r_idx, column=3 + i, value=row.get_value(p))

            n_periods = len(self.data.periods)
            n_consol = len(consol_rows)
            cats = Reference(ws_overview, min_col=3, max_col=2 + n_periods, min_row=1)

            # Helper row for 15% ROCE target (placed just below data)
            target_row = n_consol + 3
            ws_overview.cell(row=target_row, column=1, value='Target (15%)')
            for i in range(n_periods):
                ws_overview.cell(row=target_row, column=3 + i, value=0.15)

            # Placeholder cells for inventory charts (require InventoryQualityEngine)
            placeholder_row = n_consol + 2
            ws_overview.cell(row=placeholder_row, column=1, value='Inventory Quality & Top 10 Overstocks: See web dashboard')

            # Chart 1 — Projected Financial Metrics (VBA step 141)
            chart1 = LineChart()
            chart1.title = 'Projected Financial Metrics'
            chart1.width = 20
            chart1.height = 12
            chart1.y_axis.title = 'Value'
            metrics_chart1 = ['TURNOVER', 'COST OF GOODS', 'GROSS MARGIN', 'INVENTORY VALUE']
            for metric in metrics_chart1:
                for r_idx, row in enumerate(consol_rows, start=2):
                    if row.material_number.replace('ZZZZZZ_', '') == metric:
                        data_ref = Reference(ws_overview, min_col=3, max_col=2 + n_periods, min_row=r_idx)
                        chart1.add_data(data_ref, titles_from_data=False)
                        chart1.series[-1].title = SeriesLabel(v=metric)
                        break
            chart1.set_categories(cats)
            ws_overview.add_chart(chart1, 'A' + str(n_consol + 5))

            # Chart 2 — ROCE Components (VBA step 144)
            chart2 = LineChart()
            chart2.title = 'ROCE Components'
            chart2.width = 20
            chart2.height = 12
            chart2.y_axis.title = 'Value'
            metrics_chart2 = ['EBIT', 'CAPITAL INVESTMENT', 'OPERATIONAL CASHFLOW']
            for metric in metrics_chart2:
                for r_idx, row in enumerate(consol_rows, start=2):
                    if row.material_number.replace('ZZZZZZ_', '') == metric:
                        data_ref = Reference(ws_overview, min_col=3, max_col=2 + n_periods, min_row=r_idx)
                        chart2.add_data(data_ref, titles_from_data=False)
                        chart2.series[-1].title = SeriesLabel(v=metric)
                        break
            chart2.set_categories(cats)
            ws_overview.add_chart(chart2, 'A' + str(n_consol + 27))

            # Chart 3 — ROCE bar chart with dashed 15% target line (VBA step 145)
            chart3 = BarChart()
            chart3.title = 'ROCE'
            chart3.width = 20
            chart3.height = 12
            for r_idx, row in enumerate(consol_rows, start=2):
                if 'ROCE' in row.material_number and 'CAPITAL' not in row.material_number:
                    roce_ref = Reference(ws_overview, min_col=3, max_col=2 + n_periods, min_row=r_idx)
                    chart3.add_data(roce_ref, titles_from_data=False)
                    chart3.series[-1].title = SeriesLabel(v='ROCE')
                    break
            chart3.set_categories(cats)
            target_ref = Reference(ws_overview, min_col=3, max_col=2 + n_periods, min_row=target_row)
            line_overlay = LineChart()
            line_overlay.add_data(target_ref, titles_from_data=False)
            line_overlay.series[0].title = SeriesLabel(v='Target 15%')
            line_overlay.series[0].graphicalProperties.line.dashStyle = 'dash'
            line_overlay.series[0].graphicalProperties.line.solidFill = 'FF0000'
            chart3 += line_overlay
            ws_overview.add_chart(chart3, 'A' + str(n_consol + 49))

            # ---- Top 10 Overstocks sheet (VBA CreateTop10OverstocksChart line 7116) ----
            top10_count = 0
            try:
                # Use passed-in engine; fall back to constructing one from _IQEngine
                _iq_instance = inventory_quality_engine
                if _iq_instance is None and _IQEngine is not None:
                    _iq_instance = _IQEngine(self.data, self.results, self.value_results)
                if _iq_instance is not None:
                    iq_data = _iq_instance.calculate()
                    top10 = iq_data.get('top_10_overstocks', [])
                    t10_periods = iq_data.get('periods', self.data.periods)
                    if top10:
                        ws_t10 = wb.create_sheet('Top 10 overstocks')
                        top10_sorted = sorted(top10, key=lambda x: x.get('total_overstock', 0), reverse=True)
                        num_mats = len(top10_sorted)
                        num_p = len(t10_periods)

                        # Row 1: headers — col A = 'Material', cols B+ = period labels
                        ws_t10.cell(row=1, column=1, value='Material')
                        for ci, p in enumerate(t10_periods, start=2):
                            ws_t10.cell(row=1, column=ci, value=p)

                        # Rows 2-11: one row per material
                        for ri, item in enumerate(top10_sorted, start=2):
                            ws_t10.cell(row=ri, column=1, value=item.get('material_name') or item['material_number'])
                            for ci, p in enumerate(t10_periods, start=2):
                                pdata = item.get('periods', {}).get(p, {})
                                val = pdata.get('overstock', 0) if isinstance(pdata, dict) else 0
                                ws_t10.cell(row=ri, column=ci, value=round(val, 0))
                                ws_t10.cell(row=ri, column=ci).number_format = '#,##0'

                        # Column widths
                        ws_t10.column_dimensions['A'].width = 28
                        for ci in range(2, num_p + 2):
                            from openpyxl.utils import get_column_letter
                            ws_t10.column_dimensions[get_column_letter(ci)].width = 11

                        # Chart: vertical stacked bar, one series per period
                        from openpyxl.chart import BarChart, Reference
                        chart_t10 = BarChart()
                        chart_t10.type = 'col'
                        chart_t10.grouping = 'stacked'
                        chart_t10.overlap = 100
                        chart_t10.title = 'Top 10 Overstocks'
                        chart_t10.y_axis.title = 'Value (EUR)'
                        chart_t10.x_axis.title = 'Material'
                        chart_t10.width = 25
                        chart_t10.height = 15

                        max_row = 1 + num_mats
                        for col_idx in range(2, num_p + 2):
                            data_ref = Reference(ws_t10, min_col=col_idx, min_row=1, max_row=max_row)
                            chart_t10.add_data(data_ref, titles_from_data=True)
                        cats_t10 = Reference(ws_t10, min_col=1, min_row=2, max_row=max_row)
                        chart_t10.set_categories(cats_t10)

                        ws_t10.add_chart(chart_t10, 'A14')
                        top10_count = num_mats
            except Exception as e:
                print(f"  Warning: Top 10 overstocks sheet skipped: {e}")

        print(f"\nResults exported to: {output_path}")
        print(f"  - Planning sheet (volumes)")
        print(f"  - Values_Planning sheet (financial)")
        if fte_records:
            print(f"  - FTE requirements ({len(fte_records)} groups)")
        if consol_rows:
            print(f"  - High-level overview ({len(consol_rows)} consolidation rows, 3 charts)")
        if top10_count:
            print(f"  - Top 10 overstocks ({top10_count} materials)")

    def _apply_excel_formatting(self, ws):
        """Apply VBA-matching cell formatting to a planning worksheet.

        Rules applied:
        • #,##0 number format on period data columns
        • 0.0% format on Line 10 (Utilization rate) data cells
        • Light-blue fill (DAEEF3) on Line 04 (Inventory) rows
        • Red fill (FFC7CE) on Line 04 cells < 0 and Line 10 cells > 100%
        • Orange fill (FFC896) on Line 10 cells < 30%
        • Purple fill (C9B3FF) on Line 09 (Available capacity) cells < 100 h
        • Bold font on Line 03 (Total demand) rows
        • Dotted top border between material-group boundaries
        """
        from openpyxl.styles import PatternFill, Font, Border, Side
        import re

        blue_fill   = PatternFill(start_color='DAEEF3', end_color='DAEEF3', fill_type='solid')
        red_fill    = PatternFill(start_color='FFC7CE', end_color='FFC7CE', fill_type='solid')
        orange_fill = PatternFill(start_color='FFC896', end_color='FFC896', fill_type='solid')
        purple_fill = PatternFill(start_color='C9B3FF', end_color='C9B3FF', fill_type='solid')
        bold_font   = Font(bold=True)
        dotted      = Side(border_style='dotted', color='000000')

        total_cols = ws.max_column
        period_re  = re.compile(r'^\d{4}-\d{2}$')

        # Locate key columns by reading the header row
        line_type_col = mat_num_col = data_col_start = None
        for cell in ws[1]:
            hdr = str(cell.value or '')
            if hdr == 'Line type':
                line_type_col = cell.column
            elif hdr == 'Material number':
                mat_num_col = cell.column
            elif period_re.match(hdr) and data_col_start is None:
                data_col_start = cell.column

        if line_type_col is None:
            return
        if data_col_start is None:
            data_col_start = total_cols + 1  # no period columns found

        prev_mat = None
        for excel_row in range(2, ws.max_row + 1):
            lt  = ws.cell(row=excel_row, column=line_type_col).value
            mat = ws.cell(row=excel_row, column=mat_num_col).value if mat_num_col else None

            is_l03 = lt == '03. Total demand'
            is_l04 = lt == '04. Inventory'
            is_l09 = lt == '09. Available capacity'
            is_l10 = lt == '10. Utilization rate'

            # Dotted top border at material-group boundaries
            if mat is not None and mat != prev_mat and excel_row > 2:
                for col in range(1, total_cols + 1):
                    c = ws.cell(row=excel_row, column=col)
                    b = c.border
                    c.border = Border(top=dotted, bottom=b.bottom,
                                      left=b.left, right=b.right)
            prev_mat = mat

            for col in range(1, total_cols + 1):
                cell = ws.cell(row=excel_row, column=col)
                is_data = col >= data_col_start

                # Bold for Total demand rows (all columns)
                if is_l03:
                    cell.font = bold_font

                if not is_data:
                    continue

                val     = cell.value
                num_val = val if isinstance(val, (int, float)) else None

                # Number format
                if is_l10:
                    cell.number_format = '0.0%'
                else:
                    cell.number_format = '#,##0'

                # Fill: Inventory (Line 04) — blue, overridden by red if negative
                if is_l04:
                    if num_val is not None and num_val < 0:
                        cell.fill = red_fill
                    else:
                        cell.fill = blue_fill

                # Fill: Utilization rate (Line 10) — red >100%, orange <30%
                elif is_l10:
                    if num_val is not None:
                        if num_val > 1.0:
                            cell.fill = red_fill
                        elif num_val < 0.3:
                            cell.fill = orange_fill

                # Fill: Available capacity (Line 09) — purple when < 100 h
                elif is_l09:
                    if num_val is not None and num_val < 100:
                        cell.fill = purple_fill

    def _apply_fte_formatting(self, ws, period_cols):
        """Apply formatting to the FTE requirements sheet."""
        from openpyxl.styles import PatternFill, Font, Alignment

        # Row-prefix colours
        blue_fill   = PatternFill(start_color='E3F2FD', end_color='E3F2FD', fill_type='solid')
        orange_fill = PatternFill(start_color='FFF3E0', end_color='FFF3E0', fill_type='solid')
        purple_fill = PatternFill(start_color='F3E5F5', end_color='F3E5F5', fill_type='solid')
        grey_fill   = PatternFill(start_color='EEEEEE', end_color='EEEEEE', fill_type='solid')
        hdr_fill    = PatternFill(start_color='263238', end_color='263238', fill_type='solid')
        white_font  = Font(color='FFFFFF', bold=True)
        bold_font   = Font(bold=True)

        total_cols = ws.max_column

        # Header row
        for cell in ws[1]:
            cell.fill = hdr_fill
            cell.font = white_font
            cell.alignment = Alignment(horizontal='center')

        # Column widths
        ws.column_dimensions['A'].width = 20  # Material number
        ws.column_dimensions['B'].width = 28  # Group name
        ws.column_dimensions['C'].width = 14  # FTE needed
        col_letter = 'D'
        for i, _ in enumerate(period_cols):
            from openpyxl.utils import get_column_letter
            ws.column_dimensions[get_column_letter(4 + i)].width = 12
        # Average column
        from openpyxl.utils import get_column_letter
        ws.column_dimensions[get_column_letter(total_cols)].width = 12

        # Data rows
        for excel_row in range(2, ws.max_row + 1):
            mat_cell = ws.cell(row=excel_row, column=1)
            mat_val = str(mat_cell.value or '')

            is_total = mat_val == 'TOTAL'

            # Determine row fill by material_number prefix
            if is_total:
                row_fill = grey_fill
                row_font = bold_font
            elif mat_val.startswith('ZZZZZ'):
                row_fill = purple_fill
                row_font = None
            elif mat_val.startswith('ZZZZ'):
                row_fill = orange_fill
                row_font = None
            elif mat_val.startswith('ZZ'):
                row_fill = blue_fill
                row_font = None
            else:
                row_fill = None
                row_font = None

            for col in range(1, total_cols + 1):
                cell = ws.cell(row=excel_row, column=col)
                if row_fill:
                    cell.fill = row_fill
                if row_font:
                    cell.font = row_font
                # Number format for numeric columns (cols 4+)
                if col >= 4 and isinstance(cell.value, (int, float)):
                    cell.number_format = '#,##0.00'
                    cell.alignment = Alignment(horizontal='right')

    def to_json(self) -> Dict:
        return {
            'summary': self.summary,
            'periods': self.data.periods,
            'results': {
                lt: [row.to_dict() for row in rows]
                for lt, rows in self.results.items()
            }
        }
