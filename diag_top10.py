#!/usr/bin/env python3
"""Diagnostic: Top 10 Overstocks verification against VBA formula."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from modules.planning_engine import PlanningEngine
from modules.inventory_quality_engine import InventoryQualityEngine

FILE = str(Path(__file__).parent / "uploads" / "03_2025_December_SOP consolidation_MS_RECONC.xlsm")

print("=== Loading & running PlanningEngine ===")
engine = PlanningEngine(FILE, planning_month='2026-01', months_actuals=12, months_forecast=12)
engine.run()

print("=== Running InventoryQualityEngine ===")
iq = InventoryQualityEngine(engine.data, engine.results, engine.value_results)
iq_data = iq.calculate()

top10 = iq_data['top_10_overstocks']
periods = iq_data['periods']

print(f"\nTotal overstock (all materials): {iq_data['total_overstock']:,.2f}")
print(f"Number of materials processed: {len(iq_data['per_material'])}")
print(f"Periods: {periods}")

print("\n=== TOP 10 MATERIALS BY TOTAL OVERSTOCK ===")
for i, m in enumerate(top10, 1):
    print(f"\n#{i}  {m['material_number']}  {m['material_name']}")
    print(f"     Unit value: {m['unit_value']:.4f}  |  Total overstock: {m['total_overstock']:,.2f}  |  Total inventory: {m['total_inventory']:,.2f}")

# Per-period breakdown for the #1 material
if top10:
    top1 = top10[0]
    print(f"\n=== PER-PERIOD BREAKDOWN FOR #{1} ({top1['material_number']}) ===")
    print(f"{'Period':<12} {'Inventory':>14} {'Under':>12} {'Safety':>12} {'Strategic':>12} {'Normal':>12} {'Overstock':>14}")
    for p in periods:
        pd = top1['periods'].get(p, {})
        print(f"{p:<12} {pd.get('inventory',0):>14,.2f} {pd.get('under',0):>12,.2f} {pd.get('safety',0):>12,.2f} "
              f"{pd.get('strategic',0):>12,.2f} {pd.get('normal',0):>12,.2f} {pd.get('overstock',0):>14,.2f}")

# Formula verification: pick a few samples
print("\n=== FORMULA VERIFICATION (sample checks) ===")
print("VBA: IF inv >= (safety + strategic + lot) THEN overstock = inv - safety - strategic - lot ELSE 0")
for m in top10[:3]:
    mat_num = m['material_number']
    ss_cfg = engine.data.safety_stock.get(mat_num)
    unit_val = m['unit_value']
    safety_vol = ss_cfg.safety_stock if ss_cfg else 0.0
    strategic_vol = ss_cfg.strategic_stock if ss_cfg else 0.0
    lot_vol = max(ss_cfg.lot_size, 1.0) if ss_cfg else 1.0
    safety_val = safety_vol * unit_val
    strategic_val = strategic_vol * unit_val
    lot_val = lot_vol * unit_val
    target = safety_val + strategic_val
    threshold = target + lot_val

    print(f"\nMaterial {mat_num}:")
    print(f"  safety_vol={safety_vol}, strategic_vol={strategic_vol}, lot_vol={lot_vol}, unit_val={unit_val:.4f}")
    print(f"  safety_val={safety_val:,.2f}, strategic_val={strategic_val:,.2f}, lot_val={lot_val:,.2f}")
    print(f"  target (ss+strat)={target:,.2f}, threshold (target+lot)={threshold:,.2f}")
    
    # Check first 3 periods
    for p in periods[:3]:
        pd = m['periods'].get(p, {})
        inv = pd.get('inventory', 0)
        ov_engine = pd.get('overstock', 0)
        # VBA formula
        if inv >= threshold:
            ov_vba = inv - threshold
        else:
            ov_vba = 0.0
        match = "OK" if abs(ov_engine - ov_vba) < 0.02 else f"MISMATCH (engine={ov_engine:.2f}, vba={ov_vba:.2f})"
        print(f"  {p}: inv={inv:,.2f}  overstock_engine={ov_engine:,.2f}  overstock_vba={ov_vba:,.2f}  -> {match}")

# Show period totals
print("\n=== PERIOD TOTALS ===")
pt = iq_data['period_totals']
print(f"{'Period':<12} {'Inventory':>14} {'Overstock':>14} {'Safety':>12} {'Strategic':>12} {'Normal':>12} {'Under':>12}")
for p in periods:
    t = pt[p]
    print(f"{p:<12} {t['inventory']:>14,.2f} {t['overstock']:>14,.2f} {t['safety']:>12,.2f} {t['strategic']:>12,.2f} {t['normal']:>12,.2f} {t['under']:>12,.2f}")
