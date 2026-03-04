#!/usr/bin/env python3
"""
S&OP Planning Engine - Validation Script
Compares Python-calculated output with Excel VBA macro output.
"""

import pandas as pd
import numpy as np
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def validate(excel_file: str):
    print("=" * 70)
    print("S&OP PLANNING ENGINE - VALIDATION")
    print("=" * 70)

    # Run Python calculations
    print("\n[1] Running Python calculations...")
    from modules.planning_engine import PlanningEngine

    engine = PlanningEngine(excel_file)
    engine.run()
    python_df = engine.to_dataframe()

    # Load Excel VBA output
    print("\n[2] Loading Excel VBA output (Planning sheet)...")
    xl = pd.ExcelFile(excel_file)
    excel_df = pd.read_excel(xl, sheet_name='Planning sheet')

    print(f"\n[3] Comparing outputs...")
    print(f"    Python rows: {len(python_df)}")
    print(f"    Excel rows:  {len(excel_df)}")

    # Compare line types
    print("\n[4] Line Type Comparison:")
    python_types = set(python_df['Line type'].dropna().unique())
    excel_types = set(excel_df['Line type'].dropna().unique())
    print(f"    Python line types: {len(python_types)}")
    print(f"    Excel line types:  {len(excel_types)}")
    missing = excel_types - python_types
    extra = python_types - excel_types
    if missing:
        print(f"    Missing in Python: {missing}")
    if extra:
        print(f"    Extra in Python: {extra}")

    # Row count comparison
    print("\n[5] Row Count by Line Type:")
    print("-" * 60)
    all_types = sorted(python_types | excel_types)
    matches = differences = 0
    for lt in all_types:
        py_count = len(python_df[python_df['Line type'] == lt])
        ex_count = len(excel_df[excel_df['Line type'] == lt])
        status = "=" if py_count == ex_count else "!="
        if py_count == ex_count:
            matches += 1
        else:
            differences += 1
        print(f"    {status} {lt}: Python={py_count}, Excel={ex_count}")
    print("-" * 60)
    print(f"    Matches: {matches}, Differences: {differences}")

    # Value comparison for key materials
    print("\n[6] Value Comparison (key materials):")

    # Get period columns from Excel
    pcols_e = [c for c in excel_df.columns if hasattr(c, 'strftime')]
    pcols_o = [c for c in python_df.columns if '-' in str(c) and len(str(c)) == 7]

    if pcols_e and pcols_o:
        test_materials = ['600003822', '500000932', '600003728', '150000276']
        test_line_types = [
            '01. Demand forecast', '03. Total demand', '04. Inventory',
            '06. Production plan', '06. Purchase receipt'
        ]

        total_checks = 0
        total_matches = 0

        for mat in test_materials:
            for lt in test_line_types:
                e = excel_df[(excel_df['Material number'].astype(str).str.strip() == mat) &
                             (excel_df['Line type'] == lt)]
                o = python_df[(python_df['Material number'].astype(str).str.strip() == mat) &
                              (python_df['Line type'] == lt)]

                if e.empty or o.empty:
                    continue

                er = e.iloc[0]
                or_ = o.iloc[0]

                match_count = 0
                check_count = 0
                for ec, oc in zip(pcols_e[:6], pcols_o[:6]):
                    ev = float(er[ec]) if pd.notna(er[ec]) else 0
                    ov = float(or_[oc]) if pd.notna(or_[oc]) else 0
                    check_count += 1
                    if abs(ev - ov) < 1.0:
                        match_count += 1

                total_checks += check_count
                total_matches += match_count

                if match_count < check_count:
                    print(f"    {mat} | {lt}: {match_count}/{check_count} periods match")

        if total_checks > 0:
            pct = 100.0 * total_matches / total_checks
            print(f"\n    Overall accuracy: {total_matches}/{total_checks} ({pct:.1f}%)")

    # Final verdict
    print("\n" + "=" * 70)
    print("VALIDATION SUMMARY")
    print("=" * 70)

    if len(python_types) >= 12:
        print(f"  PASS: Python generates {len(python_types)} line types (>= 12)")
    else:
        print(f"  FAIL: Python only generates {len(python_types)} line types")

    if len(python_df) > 1000:
        print(f"  PASS: Python generates {len(python_df)} rows (substantial)")
    else:
        print(f"  WARN: Python output seems low ({len(python_df)} rows)")

    return engine


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python validate.py <excel_file>")
        sys.exit(1)
    validate(sys.argv[1])
