"""Quick verification: check demand forecast values and FTE requirements after fixes."""
from modules.data_loader import DataLoader
from modules.planning_engine import PlanningEngine
from modules.models import LineType

# Build engine via PlanningEngine.run() to use the same pipeline as Production
engine = PlanningEngine(
    'uploads/03_2025_December_SOP consolidation_MS_RECONC.xlsm',
    planning_month='2026-01',
    months_actuals=12,
    months_forecast=12
)
engine.run()

print("\n" + "="*60)
print("DEMAND FORECAST (mat 600010662) check:")
demand_rows = engine.results.get(LineType.DEMAND_FORECAST.value, [])
for r in demand_rows:
    if r.material_number == '600010662':
        print(f"  Aux1={r.aux_column}, Aux2={r.aux_2_column}")
        for p in list(r.values.keys())[:3]:
            print(f"  {p}: {r.values[p]:.2f}")
        break

print("\nFTE REQUIREMENTS check:")
fte_rows = engine.results.get(LineType.FTE_REQUIREMENTS.value, [])
print(f"  Total FTE rows: {len(fte_rows)}")
total_fte = {}
for r in fte_rows:
    for p, v in r.values.items():
        total_fte[p] = total_fte.get(p, 0.0) + v

periods = engine.data.periods
print("  Total FTE per period:")
for p in periods[:3]:
    cost = total_fte.get(p, 0.0) * engine.data.valuation_params.direct_fte_cost_per_month
    print(f"    {p}: FTE={total_fte.get(p, 0):.2f}, cost={cost:,.0f}")

print("\nVALUE PLANNING (DIRECT FTE COST consolidation):")
vr = engine.value_engine
if vr:
    # Look through the consolidation rows in value_results
    from modules.models import LineType as LT
    consol = engine.value_results.get(LT.CONSOLIDATION.value, [])
    for r in consol:
        if 'DIRECT FTE' in r.material_number or 'DIRECT FTE' in str(r.line_type):
            pass
        name = r.material_number
        if 'DIRECT' in name or 'TURNOVER' in name or 'MACHINE' in name:
            vals = list(r.values.values())[:3]
            print(f"  {name}: {[f'{v:,.0f}' for v in vals]}")
