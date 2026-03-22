"""Quick validation of the app.py integration."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# 1. Check app imports cleanly
from ui.app import app

# 2. Check routes are registered
rules = [r.rule for r in app.url_map.iter_rules()]
assert '/api/export_db' in rules, "Missing /api/export_db route"
assert '/api/mom' in rules, "Missing /api/mom route"

# 3. Check the 3 new modules import
from modules.cycle_manager import CycleManager
from modules.mom_comparison_engine import MoMComparisonEngine
from modules.database_exporter import DatabaseExporter
from modules.planning_engine import PlanningEngine

# 4. Check planning_engine accepts new param
import inspect
sig = inspect.signature(PlanningEngine.to_excel_with_values)
assert 'previous_cycle_df' in sig.parameters, "Missing previous_cycle_df param"

print("=== ALL CHECKS PASSED ===")
print(f"  /api/export_db registered: True")
print(f"  /api/mom registered: True")
print(f"  CycleManager imported: True")
print(f"  MoMComparisonEngine imported: True")
print(f"  DatabaseExporter imported: True")
print(f"  previous_cycle_df param exists: True")

# Also write to file for verification
with open('_vresult.txt', 'w') as f:
    f.write("PASS\n")

