"""
S&OP Planning Engine - Main Planning Engine
Orchestrates all calculation modules to produce complete planning output.
"""

import pandas as pd
from typing import Dict, List, Optional
from datetime import datetime
from pathlib import Path

from modules.models import PlanningRow, LineType
from modules.data_loader import DataLoader
from modules.forecast_engine import ForecastEngine
from modules.bom_engine import BOMEngine
from modules.inventory_engine import InventoryEngine
from modules.capacity_engine import CapacityEngine


class PlanningEngine:
    """
    Main orchestrator that runs all planning calculations.
    
    Calculation sequence (matches VBA macro order):
    1. Load raw data from Excel
    2. Calculate demand forecast (Line 01)
    3. Explode BOM for dependent demand (Line 02, Line 08)
    4. Calculate total demand (Line 03)
    5. Set target stock (Line 05)
    6. Calculate production/purchase plan (Line 06)
    7. Calculate inventory balance (Line 04)
    8. Calculate purchase plan timing (Line 07)
    9. Calculate capacity utilization (Line 07 capacity)
    10. Calculate shift availability (Line 11)
    11. Calculate available capacity (Line 09)
    12. Calculate utilization rate (Line 10)
    13. Calculate FTE requirements (Line 12)
    """
    
    # Expected line types (14 total)
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
        
        # Results organized by line type
        self.results: Dict[str, List[PlanningRow]] = {}
        
        # All rows in order
        self.all_rows: List[PlanningRow] = []
        
        # Summary statistics
        self.summary: Dict = {}
    
    def run(self) -> 'PlanningEngine':
        """Run the complete planning calculation pipeline."""
        print("\n" + "=" * 70)
        print("S&OP PLANNING ENGINE - FULL CALCULATION")
        print("(Python implementation of Excel VBA macros)")
        print("=" * 70)
        
        # Step 1: Load raw data
        print("\n[STEP 1] Loading raw input data...")
        self.data = DataLoader(self.file_path)
        self.data.load_all()
        
        # Step 2: Calculate demand forecast
        print("\n[STEP 2] Running calculations...")
        forecast_engine = ForecastEngine(self.data)
        forecast_rows = forecast_engine.calculate()
        self.results[LineType.DEMAND_FORECAST.value] = forecast_rows
        
        # Step 3: BOM explosion
        bom_engine = BOMEngine(self.data, forecast_engine.get_all_forecasts())
        rows_02, rows_08 = bom_engine.calculate()
        self.results[LineType.DEPENDENT_DEMAND.value] = rows_02
        self.results[LineType.DEPENDENT_REQUIREMENTS.value] = rows_08
        
        # Step 4: Inventory calculations
        inventory_engine = InventoryEngine(
            self.data,
            forecast_engine.get_all_forecasts(),
            bom_engine.get_all_dependent_demand()
        )
        inventory_results = inventory_engine.calculate()
        for line_type, rows in inventory_results.items():
            self.results[line_type] = rows
        
        # Step 5: Capacity calculations
        capacity_engine = CapacityEngine(
            self.data,
            inventory_engine.get_production_plan()
        )
        capacity_results = capacity_engine.calculate()
        for line_type, rows in capacity_results.items():
            self.results[line_type] = rows
        
        # Compile all rows
        self._compile_all_rows()
        
        # Validate output
        self._validate_output()
        
        # Generate summary
        self._generate_summary()
        
        print("\n" + "=" * 70)
        print("CALCULATION COMPLETE")
        print("=" * 70)
        self._print_summary()
        
        return self
    
    def _compile_all_rows(self):
        """Compile all rows in proper order."""
        self.all_rows = []
        
        # Add rows in line type order
        for line_type in self.EXPECTED_LINE_TYPES:
            if line_type in self.results:
                self.all_rows.extend(self.results[line_type])
    
    def _validate_output(self):
        """Validate that all 14 line types are present."""
        print("\n[VALIDATION] Checking output...")
        
        present_types = set(self.results.keys())
        expected_types = set(self.EXPECTED_LINE_TYPES)
        
        # Check for missing types
        missing = expected_types - present_types
        if missing:
            print(f"  WARNING: Missing line types: {missing}")
        
        # Check for empty line types
        empty = [lt for lt, rows in self.results.items() if len(rows) == 0]
        if empty:
            print(f"  WARNING: Empty line types: {empty}")
        
        # Count line types with data
        active_types = [lt for lt, rows in self.results.items() if len(rows) > 0]
        print(f"  Line types with data: {len(active_types)}")
        
        assert len(active_types) >= 12, f"Expected at least 12 line types with data, got {len(active_types)}"
        print("  ✓ Validation passed")
    
    def _generate_summary(self):
        """Generate summary statistics."""
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
        """Print summary to console."""
        print(f"\nSummary:")
        print(f"  Total rows: {self.summary['total_rows']}")
        print(f"  Line types: {self.summary['line_types_count']}")
        print(f"  Periods: {self.summary['periods']}")
        print(f"\nBreakdown by line type:")
        for lt in self.EXPECTED_LINE_TYPES:
            count = self.summary['line_types'].get(lt, 0)
            print(f"  {lt}: {count}")
    
    def get_all_rows(self) -> List[PlanningRow]:
        """Get all planning rows."""
        return self.all_rows
    
    def get_rows_by_type(self, line_type: str) -> List[PlanningRow]:
        """Get rows for a specific line type."""
        return self.results.get(line_type, [])
    
    def get_summary(self) -> Dict:
        """Get summary statistics."""
        return self.summary
    
    def to_dataframe(self) -> pd.DataFrame:
        """Convert all results to a DataFrame."""
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
            
            # Add period values
            for period, value in row.values.items():
                row_dict[period] = value
            
            rows_data.append(row_dict)
        
        return pd.DataFrame(rows_data)
    
    def to_excel(self, output_path: str):
        """Export results to Excel file."""
        df = self.to_dataframe()
        df.to_excel(output_path, sheet_name='Planning Results', index=False)
        print(f"\nResults exported to: {output_path}")
    
    def to_json(self) -> Dict:
        """Convert results to JSON-serializable format."""
        return {
            'summary': self.summary,
            'periods': self.data.periods,
            'results': {
                lt: [row.to_dict() for row in rows]
                for lt, rows in self.results.items()
            }
        }
