from modules.data_loader import DataLoader
from modules.capacity_engine import CapacityEngine
from modules.planning_engine import PlanningEngine

# Check fte_requirements for machine groups
data = DataLoader('uploads/03_2025_December_SOP consolidation_MS_RECONC.xlsm').load_all()

print("Machine group fte_requirements:")
for gid, group in data.machine_groups.items():
    mat = data.materials.get(gid)
    fte_req = mat.fte_requirements if mat else 0.0
    print(f"  Group {gid}: fte_requirements={fte_req}")

# FTE config
print(f"\nfte_hours_per_year={data.fte_hours_per_year}")
print(f"shift_hours={data.shift_hours}")
print(f"direct_fte_cost_per_month={data.valuation_params.direct_fte_cost_per_month:.2f}")
