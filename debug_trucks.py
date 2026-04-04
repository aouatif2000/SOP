import sys
sys.path.insert(0, '.')
from modules.planning_engine import PlanningEngine

# Run the full engine
engine = PlanningEngine('uploads/03_2025_December_SOP consolidation_MS_RECONC.xlsm')
engine.run()

print("\n=== TRUCK MATERIAL ATTRIBUTES ===")
for mat_id, mat in engine.data.materials.items():
    if 'TRUCK' in mat_id:
        print(f"Material: {mat_id}")
        print(f"  product_type: {mat.product_type}")
        print(f"  product_type_raw: {mat.product_type_raw}")
        print(f"  ton_per_truck: {mat.ton_per_truck}")
        print(f"  time_per_truck: {mat.time_per_truck}")
        print(f"  fte_requirements: {mat.fte_requirements}")
        print(f"  truck_operation: {mat.truck_operation}")

print(f"\nfte_hours_per_year: {engine.data.fte_hours_per_year}")
print(f"periods: {engine.data.periods[:6]}")

print("\n=== FTE rows (12. FTE requirements) for TRUCK ===")
fte_rows = engine.results.get('12. FTE requirements', [])
for r in fte_rows:
    if 'TRUCK' in str(r.material_number):
        vals = list(r.values.items())[:6]
        print(f"  {r.material_number}: product_type={r.product_type}, aux={r.aux_column}")
        print(f"    Values: {vals}")

print("\n=== VALUE rows (12. FTE requirements) for TRUCK ===")
val_fte_rows = engine.value_results.get('12. FTE requirements', [])
for r in val_fte_rows:
    if 'TRUCK' in str(r.material_number):
        vals = list(r.values.items())[:6]
        print(f"  {r.material_number}: aux={r.aux_column}")
        print(f"    Values: {vals}")

print("\nDONE")

