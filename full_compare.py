"""Full consolidation row comparison Python vs Excel reference values."""
from modules.data_loader import DataLoader
from modules.planning_engine import PlanningEngine
from modules.models import LineType

engine = PlanningEngine(
    'uploads/03_2025_December_SOP consolidation_MS_RECONC.xlsm',
    planning_month='2026-01',
    months_actuals=12,
    months_forecast=12
)
engine.run()

consol = engine.value_results.get(LineType.CONSOLIDATION.value, [])
periods = engine.data.periods

print(f"\n{'Row':<40} {'01/2026':>12} {'02/2026':>12} {'03/2026':>12}  {'Annual Aux':>14}")
print("-" * 85)
for r in consol:
    vals = [r.values.get(p, 0.0) for p in periods]
    annual = sum(vals)
    name = r.material_number.replace('ZZZZZZ_', '')
    print(f"{name:<40} {vals[0]:>12,.0f} {vals[1]:>12,.0f} {vals[2]:>12,.0f}  {annual:>14,.0f}")
