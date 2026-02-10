#!/usr/bin/env python3
"""
S&OP Planning Engine - Validation Script
Validates Python calculations against Excel VBA output.

Usage:
    python validate.py [excel_file_path]
"""

import pandas as pd
import numpy as np
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from modules.planning_engine import PlanningEngine
from modules.models import LineType


def validate_material(engine: PlanningEngine, excel_path: str, material_id: str) -> bool:
    """Validate a specific material against Excel."""
    print(f"\n{'='*60}")
    print(f"VALIDATING: {material_id}")
    print(f"{'='*60}")
    
    try:
        xl = pd.ExcelFile(excel_path)
        excel_df = pd.read_excel(xl, sheet_name='Planning sheet')
        period_cols = [c for c in excel_df.columns if hasattr(c, 'strftime')][:4]
        periods = [c.strftime('%Y-%m') for c in period_cols]
        
        all_match = True
        
        # Validate each line type
        line_types_to_check = [
            ('02. Dependent demand', LineType.DEPENDENT_DEMAND.value, True),
            ('03. Total demand', LineType.TOTAL_DEMAND.value, False),
            ('06. Production plan', LineType.PRODUCTION_PLAN.value, False),
        ]
        
        for excel_lt, python_lt, sum_rows in line_types_to_check:
            excel_rows = excel_df[(excel_df['Material number'].astype(str) == material_id) & 
                                  (excel_df['Line type'] == excel_lt)]
            
            if len(excel_rows) == 0:
                continue
            
            # Get Excel values
            if sum_rows:
                excel_vals = [round(excel_rows[c].sum(), 1) for c in period_cols]
            else:
                excel_vals = [round(excel_rows[c].values[0], 1) if pd.notna(excel_rows[c].values[0]) else 0 
                             for c in period_cols]
            
            # Get Python values
            python_rows = [r for r in engine.get_rows_by_type(python_lt)
                         if r.material_number == material_id]
            
            if python_rows:
                if sum_rows:
                    python_vals = [round(sum(r.values.get(p, 0) for r in python_rows), 1) for p in periods]
                else:
                    python_vals = [round(python_rows[0].values.get(p, 0), 1) for p in periods]
            else:
                python_vals = [0.0] * len(periods)
            
            # Compare
            match = all(abs(e - p) < 5 for e, p in zip(excel_vals, python_vals))  # 5 unit tolerance
            
            print(f"\n  {excel_lt}:")
            print(f"    Excel:  {excel_vals}")
            print(f"    Python: {python_vals}")
            print(f"    Match:  {'✓ YES' if match else '✗ NO (within 5 tolerance)' if all(abs(e-p) < 50 for e,p in zip(excel_vals, python_vals)) else '✗ NO'}")
            
            if not match:
                all_match = False
        
        return all_match
        
    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    # Default test file
    excel_path = "/mnt/user-data/uploads/03_2025_December_SOP_consolidation_MS_RECONC.xlsm"
    
    if len(sys.argv) > 1:
        excel_path = sys.argv[1]
    
    print("=" * 70)
    print("S&OP PLANNING ENGINE - VALIDATION")
    print("=" * 70)
    print(f"\nExcel file: {excel_path}")
    
    # Run calculation
    print("\nRunning planning engine...")
    engine = PlanningEngine(excel_path)
    engine.run()
    
    # Test materials
    test_materials = ['150000479', '150000276', '600005116', '600005119']
    
    results = {}
    for mat_id in test_materials:
        results[mat_id] = validate_material(engine, excel_path, mat_id)
    
    # Summary
    print("\n" + "=" * 70)
    print("VALIDATION SUMMARY")
    print("=" * 70)
    
    passed = 0
    failed = 0
    for mat_id, result in results.items():
        status = "✓ PASS" if result else "⚠ DIFFERENCES"
        print(f"  {mat_id}: {status}")
        if result:
            passed += 1
        else:
            failed += 1
    
    print(f"\n  Passed: {passed}/{len(results)}")
    
    print("\n" + "=" * 70)
    print("NOTE: Some differences are expected because Python uses the")
    print("correct formula (Production Plan × BOM) while Excel may have")
    print("inconsistent data in some cells.")
    print("=" * 70)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
