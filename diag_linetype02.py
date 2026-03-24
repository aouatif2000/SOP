"""
Diagnostic: why are 150000546, 150002727, 150000276 missing at linetype 02?

Part A: Static checks (BOM, Material Master, routing, plant filter)
Part B: Live run — trace actual dep_demand propagation
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import glob as _glob
import pandas as pd

# Find the upload file
uploads = _glob.glob('uploads/*.xlsm') + _glob.glob('uploads/*.xlsx')
if not uploads:
    print("ERROR: No upload file found in uploads/")
    sys.exit(1)
FILE = uploads[0]
print(f"Using file: {FILE}\n")

TARGET_MATS = ['150000546', '150002727', '150000276']

# ── Part A: Static BOM/Material Master checks ──────────────────────────────

from modules.data_loader import DataLoader
data = DataLoader(FILE)
data.load_all()

print("=" * 60)
print("PART A: Static checks")
print("=" * 60)

for mat in TARGET_MATS:
    print(f"\n--- Material {mat} ---")

    m = data.materials.get(mat)
    if m:
        print(f"  Material Master: FOUND — type={m.product_type.value}, active={m.is_active}")
    else:
        print(f"  Material Master: NOT FOUND  *** ROOT CAUSE for missing Line 02 ***")

    level = data.bom_levels.get(mat)
    print(f"  BOM level: {level}")

    bom_df = pd.read_excel(data.file_path, sheet_name='BOM')
    raw_entries = bom_df[bom_df['Component'].astype(str).str.strip() == mat]
    print(f"  Raw BOM rows (before filters): {len(raw_entries)}")
    for _, row in raw_entries.iterrows():
        plant = str(row.get('Plant', '')).strip()
        qty   = row.get('BILLOFMATERIALITEMQUANTITY', 0)
        parent = str(row.get('Material', '')).strip()
        site  = data.config.site
        status = "FILTERED (plant)" if (plant and plant != site) else \
                 "FILTERED (qty=0)" if (pd.isna(qty) or qty == 0) else "KEPT"
        print(f"    Parent={parent}, Plant={plant}, Qty={qty}  [{status}]")

    parents = [b.parent_material for b in data.bom if b.component_material == mat]
    print(f"  Parents in loaded BOM: {parents}")

    for parent_mat in parents:
        p_obj = data.materials.get(parent_mat)
        p_level = data.bom_levels.get(parent_mat)
        has_routing  = len(data.get_all_routings(parent_mat)) > 0
        is_bom_par   = any(b.parent_material == parent_mat for b in data.bom if not b.is_coproduct)
        in_p_and_p   = data.is_purchased_and_produced(parent_mat)
        needs_prod   = in_p_and_p or (is_bom_par and has_routing)
        ok_levels    = (p_level is not None and level is not None and p_level < level)
        issue = "" if (needs_prod and ok_levels) else \
                " *** no routing → production_plan=None ***" if not needs_prod else \
                f" *** level {p_level} >= child level {level} ***"
        print(f"  Parent {parent_mat}: level={p_level}, needs_prod={needs_prod}, level_ok={ok_levels}{issue}")


# ── Part B: Live run — check what Line 02 actually gets generated ───────────

print("\n\n" + "=" * 60)
print("PART B: Live engine run — checking actual Line 02 output")
print("=" * 60)

from modules.planning_engine import PlanningEngine
from modules.models import LineType

engine = PlanningEngine(FILE, planning_month='2026-01', months_actuals=12, months_forecast=12)
engine.run()

line02_rows = engine.results.get(LineType.DEPENDENT_DEMAND.value, [])
print(f"\nTotal Line 02 rows generated: {len(line02_rows)}")

for mat in TARGET_MATS:
    rows = [r for r in line02_rows if r.material_number == mat]
    print(f"\n  Material {mat}: {len(rows)} Line 02 row(s)")
    for r in rows:
        vals = list(r.values.values())
        print(f"    aux(parent)={r.aux_column}, first 3 periods: {vals[:3]}")
    if not rows:
        print(f"    *** CONFIRMED MISSING ***")
        # Check if the material appears in ANY line type
        for lt, lt_rows in engine.results.items():
            mat_rows = [r for r in lt_rows if r.material_number == mat]
            if mat_rows:
                print(f"    (has {len(mat_rows)} row(s) at {lt})")
