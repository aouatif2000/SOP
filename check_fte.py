from modules.data_loader import DataLoader
from modules.planning_engine import PlanningEngine

data = DataLoader('uploads/03_2025_December_SOP consolidation_MS_RECONC.xlsm').load_all()
engine = PlanningEngine(data, planning_month='2026-01', months_actuals=12, months_forecast=12)
results = engine.calculate()

from modules.models import LineType
fte_rows = results.get(LineType.FTE_REQUIREMENTS.value, [])
print(f"FTE requirement rows: {len(fte_rows)}")
direct_fte_cost = data.valuation_params.direct_fte_cost_per_month
print(f"direct_fte_cost_per_month: {direct_fte_cost:.2f}")
print()

# Show FTE values for each machine group/machine
periods = data.periods
total_per_period = {p: 0.0 for p in periods}
for row in fte_rows[:10]:
    vals = {p: row.get_value(p) for p in periods}
    total = sum(vals.values())
    cost_total = sum(vals.values()) * direct_fte_cost
    print(f"  {row.material_number}/{row.material_name[:30]}: FTE sum={total:.4f}, cost_total={cost_total:.2f}")
    for p in periods:
        total_per_period[p] += vals[p]

print(f"\nTotal FTE per period:")
for p in periods:
    print(f"  {p}: FTE={total_per_period[p]:.4f}, cost={total_per_period[p]*direct_fte_cost:.2f}")
