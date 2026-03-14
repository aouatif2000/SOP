#!/usr/bin/env python3
"""
S&OP Planning Engine - Validation Script
Compares Python-calculated output against Excel VBA macro ground truth.
Tests ALL line types for ALL materials across ALL periods on both sheets.
"""

import pandas as pd
import numpy as np
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))

# ---- Tolerances ----
VOL_TOL  = 1.0    # volumes, hours, monetary values
RATE_TOL = 0.01   # ratios e.g. Line 10 utilization rate
ROCE_TOL = 0.001  # ROCE / ROI in value planning

# Line types stored as ratios (0.0–1.0+) in period cells
RATE_LINE_TYPES = {'10. Utilization rate', '11. Shift availability'}

MAX_SHOWN = 5  # max mismatches printed per line type


# ---- Helpers ----

def _period_str(col) -> str:
    """Normalise a column header to 'YYYY-MM'."""
    if hasattr(col, 'strftime'):
        return col.strftime('%Y-%m')
    s = str(col).strip()
    if len(s) == 7 and s[4] == '-':
        return s
    try:
        return pd.to_datetime(s).strftime('%Y-%m')
    except Exception:
        return s


def _xl_period_map(df):
    """Return [(df_column, 'YYYY-MM')] for columns at index >= 11 that look like periods."""
    result = []
    for i, col in enumerate(df.columns):
        if i < 11:
            continue
        ps = _period_str(col)
        if len(ps) == 7 and ps[4] == '-':
            result.append((col, ps))
    return result


def _safe_float(v) -> float:
    try:
        f = float(v)
        return 0.0 if (f != f) else f   # NaN → 0
    except (TypeError, ValueError):
        return 0.0


def _compare_sheet(py_df: pd.DataFrame, xl_df: pd.DataFrame,
                   rate_types=None, roce_types=None):
    """
    Compare two planning DataFrames keyed on (material_number, line_type).

    Excel column layout (0-indexed):
      0  Material number | 7  Line type | 8  Aux Column | 9  Aux 2 Column
      10 Starting stock  | 11+ period data

    Returns:
      dict  {line_type: {'matched': int, 'total': int, 'mismatches': [str]}}
    """
    rate_types = rate_types or RATE_LINE_TYPES
    roce_types = roce_types or set()

    xl_pm = _xl_period_map(xl_df)
    py_periods = {_period_str(c): c for c in py_df.columns}

    # Normalise keys
    xl_df = xl_df.copy()
    py_df = py_df.copy()
    xl_df['_mat'] = xl_df.iloc[:, 0].astype(str).str.strip()
    xl_df['_lt']  = xl_df.iloc[:, 7].astype(str).str.strip()
    py_df['_mat'] = py_df['Material number'].astype(str).str.strip()
    py_df['_lt']  = py_df['Line type'].astype(str).str.strip()

    # Index Python rows by (mat, lt)
    py_idx = defaultdict(list)
    for _, row in py_df.iterrows():
        py_idx[(row['_mat'], row['_lt'])].append(row)

    results = defaultdict(lambda: {'matched': 0, 'total': 0, 'mismatches': []})

    for _, xl_row in xl_df.iterrows():
        mat = xl_row['_mat']
        lt  = xl_row['_lt']
        if not lt or lt == 'nan':
            continue

        if lt in roce_types:
            tol = ROCE_TOL
        elif lt in rate_types:
            tol = RATE_TOL
        else:
            tol = VOL_TOL

        py_rows = py_idx.get((mat, lt), [])
        if not py_rows:
            n = len(xl_pm)
            results[lt]['total'] += n
            if len(results[lt]['mismatches']) < MAX_SHOWN:
                results[lt]['mismatches'].append(
                    f"  MISSING in Python: mat={mat}")
            continue

        py_row = py_rows[0]
        for xl_col, period in xl_pm:
            py_col = py_periods.get(period)
            ev = _safe_float(xl_row[xl_col])
            pv = _safe_float(py_row[py_col]) if py_col else 0.0
            results[lt]['total'] += 1
            if abs(ev - pv) <= tol:
                results[lt]['matched'] += 1
            elif len(results[lt]['mismatches']) < MAX_SHOWN:
                results[lt]['mismatches'].append(
                    f"  mat={mat} period={period}: "
                    f"Excel={ev:.4g}  Python={pv:.4g}  diff={abs(ev-pv):.4g}")

    return results


def _compare_aux(py_df: pd.DataFrame, xl_df: pd.DataFrame,
                 line_type: str, aux_col_idx: int, label: str):
    """Compare one AUX column for a given line type. Returns (matched, total)."""
    xl_df = xl_df.copy()
    py_df = py_df.copy()
    xl_df['_mat'] = xl_df.iloc[:, 0].astype(str).str.strip()
    xl_df['_lt']  = xl_df.iloc[:, 7].astype(str).str.strip()
    py_df['_mat'] = py_df['Material number'].astype(str).str.strip()
    py_df['_lt']  = py_df['Line type'].astype(str).str.strip()

    xl_aux_col  = xl_df.columns[aux_col_idx]
    py_aux_name = {8: 'Aux Column', 9: 'Aux 2 Column'}.get(aux_col_idx, 'Aux Column')

    xl_sub = xl_df[xl_df['_lt'] == line_type]
    py_sub = py_df[py_df['_lt'] == line_type]
    py_idx = {row['_mat']: row for _, row in py_sub.iterrows()}

    matched = total = 0
    mismatches = []
    for _, xl_row in xl_sub.iterrows():
        mat = xl_row['_mat']
        ev  = _safe_float(xl_row[xl_aux_col])
        py_row = py_idx.get(mat)
        pv  = _safe_float(py_row[py_aux_name]) if py_row is not None else 0.0
        total += 1
        if abs(ev - pv) <= VOL_TOL:
            matched += 1
        elif len(mismatches) < MAX_SHOWN:
            mismatches.append(
                f"  mat={mat}: Excel={ev:.4g}  Python={pv:.4g}  diff={abs(ev-pv):.4g}")

    if mismatches:
        print(f"    [{label}] first mismatches:")
        for m in mismatches:
            print(m)
    return matched, total


def _print_results(results: dict, title: str):
    """Print per-line-type table and return (line_results_dict, grand_match, grand_total)."""
    W = 72
    print(f"\n{'='*W}")
    print(f"  {title}")
    print(f"{'='*W}")
    print(f"  {'Line type':<42} {'Match':>7}  {'Total':>7}  {'%':>6}  Status")
    print(f"  {'-'*42} {'-'*7}  {'-'*7}  {'-'*6}  ------")

    grand_match = grand_total = 0
    line_results = {}
    for lt in sorted(results.keys()):
        r   = results[lt]
        m, t = r['matched'], r['total']
        pct  = 100.0 * m / t if t else 0.0
        status = 'PASS' if pct >= 90 else 'FAIL'
        print(f"  {lt:<42} {m:>7}  {t:>7}  {pct:>5.1f}%  {status}")
        for msg in r['mismatches']:
            print(msg)
        grand_match += m
        grand_total  += t
        line_results[lt] = {'matched': m, 'total': t, 'pct': pct}

    gpct = 100.0 * grand_match / grand_total if grand_total else 0.0
    print(f"  {'-'*42} {'-'*7}  {'-'*7}  {'-'*6}  ------")
    print(f"  {'OVERALL':<42} {grand_match:>7}  {grand_total:>7}  {gpct:>5.1f}%  "
          f"{'PASS' if gpct >= 90 else 'FAIL'}")
    return line_results, grand_match, grand_total


# ---- Main ----

def validate_scenario(excel_file: str,
                      planning_month=None,
                      months_actuals: int = 0,
                      months_forecast: int = 12,
                      label: str = "Default") -> dict:
    """
    Run the planning engine with given parameters, compare against the VBA
    ground-truth sheets embedded in the same Excel file, and return a results dict.
    """
    W = 72
    print(f"\n{'='*W}")
    print(f"  SCENARIO: {label}")
    print(f"  planning_month={planning_month}  actuals={months_actuals}  forecast={months_forecast}")
    print(f"{'='*W}")

    # 1. Run engine
    print("\n[1] Running Python planning engine...")
    from modules.planning_engine import PlanningEngine
    engine = PlanningEngine(
        excel_file,
        planning_month=planning_month,
        months_actuals=months_actuals,
        months_forecast=months_forecast,
    )
    engine.run()

    py_vol_df = engine.to_dataframe()
    print(f"    Volume rows generated:  {len(py_vol_df)}")

    # 2. Build Python value-planning DataFrame
    value_rows = []
    for lt, rows in engine.value_results.items():
        for row in rows:
            rd = {
                'Material number': row.material_number,
                'Material name':   row.material_name,
                'Product type':    row.product_type,
                'Product family':  row.product_family,
                'SPC product':     row.spc_product,
                'Product cluster': row.product_cluster,
                'Product name':    row.product_name,
                'Line type':       row.line_type,
                'Aux Column':      row.aux_column,
                'Aux 2 Column':    row.aux_2_column,
                'Starting stock':  row.starting_stock,
            }
            for period, val in row.values.items():
                rd[period] = val
            value_rows.append(rd)
    py_val_df = pd.DataFrame(value_rows)
    print(f"    Value rows generated:   {len(py_val_df)}")

    # 3. Load Excel ground truth
    print("\n[2] Loading Excel VBA ground truth...")
    xl = pd.ExcelFile(excel_file)
    xl_vol_df = pd.read_excel(xl, sheet_name='Planning sheet',        header=0)
    xl_val_df = pd.read_excel(xl, sheet_name='Values_Planning sheet', header=0)
    print(f"    Planning sheet rows:        {len(xl_vol_df)}")
    print(f"    Values_Planning sheet rows: {len(xl_val_df)}")

    # 4. AUX column comparisons (Lines 01 and 05)
    print("\n[3] AUX column comparisons...")
    l01_m1, l01_t1 = _compare_aux(py_vol_df, xl_vol_df,
                                   '01. Demand forecast', 8, 'L01 Aux1 avg-actuals')
    l01_m2, l01_t2 = _compare_aux(py_vol_df, xl_vol_df,
                                   '01. Demand forecast', 9, 'L01 Aux2 avg-forecast')
    l05_m,  l05_t  = _compare_aux(py_vol_df, xl_vol_df,
                                   '05. Minimum target stock', 8, 'L05 Aux1 target-stock')
    aux_results = {
        'L01_aux1': {'matched': l01_m1, 'total': l01_t1},
        'L01_aux2': {'matched': l01_m2, 'total': l01_t2},
        'L05_aux1': {'matched': l05_m,  'total': l05_t},
    }
    for k, v in aux_results.items():
        t, m = v['total'], v['matched']
        pct  = 100.0 * m / t if t else 0.0
        print(f"    {k}: {m}/{t} ({pct:.1f}%)")

    # 5. Volume planning comparison
    print("\n[4] Comparing VOLUME PLANNING...")
    vol_res = _compare_sheet(py_vol_df, xl_vol_df)
    vol_line, vol_match, vol_total = _print_results(vol_res, f'VOLUME PLANNING — {label}')

    # 6. Value planning comparison
    print("\n[5] Comparing VALUE PLANNING...")
    roce_types = {lt for lt in py_val_df.get('Line type', pd.Series()).unique()
                  if 'ROCE' in str(lt).upper() or 'ROI' in str(lt).upper()}
    val_res = _compare_sheet(py_val_df, xl_val_df,
                              rate_types=set(), roce_types=roce_types)
    val_line, val_match, val_total = _print_results(val_res, f'VALUE PLANNING — {label}')

    # 7. Combined summary
    grand_match = vol_match + val_match
    grand_total = vol_total + val_total
    grand_pct   = 100.0 * grand_match / grand_total if grand_total else 0.0
    vol_pct     = 100.0 * vol_match / vol_total     if vol_total  else 0.0
    val_pct     = 100.0 * val_match / val_total     if val_total  else 0.0

    W = 72
    print(f"\n{'='*W}")
    print(f"  FINAL SUMMARY — {label}")
    print(f"{'='*W}")
    print(f"  Volume Planning:  {vol_match:>7}/{vol_total:<7}  "
          f"({vol_pct:>5.1f}%)  {'PASS' if vol_pct >= 90 else 'FAIL'}")
    print(f"  Value  Planning:  {val_match:>7}/{val_total:<7}  "
          f"({val_pct:>5.1f}%)  {'PASS' if val_pct >= 90 else 'FAIL'}")
    print(f"  {'─'*60}")
    print(f"  COMBINED:         {grand_match:>7}/{grand_total:<7}  "
          f"({grand_pct:>5.1f}%)  {'PASS' if grand_pct >= 90 else 'FAIL'}")
    print(f"{'='*W}")

    return {
        'label':    label,
        'volume':   {'matched': vol_match,   'total': vol_total,   'pct': vol_pct,   'by_line': vol_line},
        'value':    {'matched': val_match,   'total': val_total,   'pct': val_pct,   'by_line': val_line},
        'aux':      aux_results,
        'combined': {'matched': grand_match, 'total': grand_total, 'pct': grand_pct},
    }


def validate_self_check(excel_file: str,
                        planning_month: str,
                        months_actuals: int,
                        months_forecast: int,
                        label: str = "Self-check") -> dict:
    """
    Run the engine with given parameters and validate internal consistency
    (no VBA ground truth required).

    Checks:
      1. Line 03 = Line 01 + sum(all Line 02 rows) for every material × period
      2. No NaN / None in period columns
      3. Inventory running balance: inv(m) = inv(m-1) - L03(m) + L06_prod(m) + L06_purch(m)
    """
    W = 72
    print(f"\n{'='*W}")
    print(f"  SELF-CONSISTENCY CHECK: {label}")
    print(f"  planning_month={planning_month}  actuals={months_actuals}  forecast={months_forecast}")
    print(f"{'='*W}")

    from modules.planning_engine import PlanningEngine
    engine = PlanningEngine(
        excel_file,
        planning_month=planning_month,
        months_actuals=months_actuals,
        months_forecast=months_forecast,
    )
    engine.run()
    df = engine.to_dataframe()

    # Identify period columns (YYYY-MM format at index >= 11)
    period_cols = [c for c in df.columns if isinstance(c, str) and len(c) == 7 and c[4] == '-']

    total_checks = 0
    failures = []

    # --- Check 1: No NaN in period columns ---
    nan_count = df[period_cols].isna().sum().sum()
    total_checks += len(df) * len(period_cols)
    if nan_count > 0:
        failures.append(f"  CHECK 1 FAIL: {nan_count} NaN values in period columns")
    else:
        print(f"  CHECK 1 PASS: no NaN values in {len(df) * len(period_cols)} period cells")

    # --- Check 2: L03 = L01 + sum(L02) per material per period ---
    l01 = df[df['Line type'] == '01. Demand forecast'].set_index('Material number')
    l02 = df[df['Line type'] == '02. Dependent demand']
    l03 = df[df['Line type'] == '03. Total demand'].set_index('Material number')

    l03_fail = l03_match = 0
    for mat in l03.index.unique():
        if mat not in l01.index:
            continue
        l02_mat = l02[l02['Material number'] == mat]
        for p in period_cols:
            if p not in l03.columns:
                continue
            expected = _safe_float(l01.at[mat, p] if p in l01.columns else 0.0)
            expected += sum(_safe_float(r[p]) for _, r in l02_mat.iterrows() if p in l02_mat.columns)
            actual = _safe_float(l03.at[mat, p])
            total_checks += 1
            if abs(actual - expected) <= VOL_TOL:
                l03_match += 1
            else:
                l03_fail += 1
                if len(failures) < MAX_SHOWN:
                    failures.append(
                        f"  CHECK 2 FAIL: L03 mat={mat} period={p}: "
                        f"expected={expected:.4g} actual={actual:.4g}")

    pct2 = 100.0 * l03_match / (l03_match + l03_fail) if (l03_match + l03_fail) else 100.0
    status2 = 'PASS' if pct2 >= 99 else 'FAIL'
    print(f"  CHECK 2 {status2}: L03=L01+L02  {l03_match}/{l03_match+l03_fail} ({pct2:.1f}%)")

    # --- Check 3: Inventory running balance ---
    l03_dict = df[df['Line type'] == '03. Total demand'].set_index('Material number')
    l04 = df[df['Line type'] == '04. Inventory'].set_index('Material number')
    l06_prod  = df[df['Line type'] == '06. Production plan'].set_index('Material number')
    l06_purch = df[df['Line type'] == '06. Purchase receipt'].set_index('Material number')

    inv_match = inv_fail = 0
    for mat in l04.index.unique():
        prev_inv = _safe_float(l04.at[mat, 'Starting stock']) if 'Starting stock' in l04.columns else 0.0
        for p in period_cols:
            if p not in l04.columns:
                continue
            demand = _safe_float(l03_dict.at[mat, p]) if mat in l03_dict.index and p in l03_dict.columns else 0.0
            prod   = _safe_float(l06_prod.at[mat, p])  if mat in l06_prod.index  and p in l06_prod.columns  else 0.0
            purch  = _safe_float(l06_purch.at[mat, p]) if mat in l06_purch.index and p in l06_purch.columns else 0.0
            expected_inv = prev_inv - demand + prod + purch
            actual_inv   = _safe_float(l04.at[mat, p])
            total_checks += 1
            if abs(actual_inv - expected_inv) <= VOL_TOL:
                inv_match += 1
            else:
                inv_fail += 1
                if len(failures) < MAX_SHOWN:
                    failures.append(
                        f"  CHECK 3 FAIL: L04 mat={mat} period={p}: "
                        f"expected={expected_inv:.4g} actual={actual_inv:.4g}")
            prev_inv = actual_inv

    pct3 = 100.0 * inv_match / (inv_match + inv_fail) if (inv_match + inv_fail) else 100.0
    status3 = 'PASS' if pct3 >= 99 else 'FAIL'
    print(f"  CHECK 3 {status3}: Inventory balance  {inv_match}/{inv_match+inv_fail} ({pct3:.1f}%)")

    if failures:
        print(f"\n  Sample failures:")
        for f in failures[:MAX_SHOWN]:
            print(f)

    overall_pass = (nan_count == 0) and (pct2 >= 99) and (pct3 >= 99)
    print(f"\n  OVERALL: {'PASS' if overall_pass else 'FAIL'}")
    print(f"{'='*W}")

    return {
        'label':         label,
        'nan_count':     nan_count,
        'l03_check':     {'matched': l03_match, 'failed': l03_fail, 'pct': pct2},
        'inv_check':     {'matched': inv_match,  'failed': inv_fail,  'pct': pct3},
        'overall_pass':  overall_pass,
    }


def validate_multi(excel_file: str):
    """
    Run two validation scenarios:
      A — Default (reads Config sheet, compares against VBA ground truth, threshold 90%)
      B — Jan 2026, 12 actuals + 12 forecast (self-consistency check, threshold 99%)
    """
    W = 72
    print("=" * W)
    print("  S&OP PLANNING ENGINE — MULTI-SCENARIO VALIDATION")
    print("=" * W)

    # Scenario A: default config, compare against VBA
    result_a = validate_scenario(
        excel_file,
        planning_month=None,
        months_actuals=0,
        months_forecast=12,
        label="Scenario A — Default (VBA ground-truth comparison)",
    )

    # Scenario B: Jan 2026, 12+12, self-consistency (no VBA ground truth for non-default month)
    result_b = validate_self_check(
        excel_file,
        planning_month='2026/01',
        months_actuals=12,
        months_forecast=12,
        label="Scenario B — Jan 2026 12+12 (self-consistency check)",
    )

    # Final multi-scenario summary
    print(f"\n{'='*W}")
    print("  MULTI-SCENARIO SUMMARY")
    print(f"{'='*W}")
    a_pct  = result_a['combined']['pct']
    a_pass = a_pct >= 90
    b_pass = result_b['overall_pass']
    print(f"  Scenario A (VBA compare):      {a_pct:>5.1f}%  {'PASS' if a_pass else 'FAIL'}")
    print(f"  Scenario B (self-consistency): {'PASS' if b_pass else 'FAIL'}")
    print(f"  {'─'*60}")
    print(f"  OVERALL: {'PASS' if (a_pass and b_pass) else 'FAIL'}")
    print(f"{'='*W}")

    return {'scenario_a': result_a, 'scenario_b': result_b}


def validate(excel_file: str) -> dict:
    """Backward-compatible single-scenario validation (default config vs VBA ground truth)."""
    W = 72
    print("=" * W)
    print("  S&OP PLANNING ENGINE — FULL VALIDATION")
    print("=" * W)
    return validate_scenario(excel_file, label="Default (VBA ground-truth comparison)")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python validate.py <excel_file> [--multi]")
        sys.exit(1)
    if '--multi' in sys.argv:
        validate_multi(sys.argv[1])
    else:
        validate(sys.argv[1])

