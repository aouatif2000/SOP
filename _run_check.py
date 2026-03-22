"""Validation script — run and check _check_result.txt"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

results = []
try:
    import py_compile
    for f in ['ui/app.py','modules/planning_engine.py','modules/cycle_manager.py',
              'modules/mom_comparison_engine.py','modules/database_exporter.py']:
        py_compile.compile(f, doraise=True)
        results.append(f'SYNTAX OK: {f}')
    
    from ui.app import app
    results.append('IMPORT OK: ui.app')
    
    rules = [r.rule for r in app.url_map.iter_rules()]
    for ep in ['/api/export_db', '/api/mom', '/api/calculate', '/api/export']:
        assert ep in rules, f'MISSING: {ep}'
        results.append(f'ROUTE OK: {ep}')
    
    import inspect
    from modules.planning_engine import PlanningEngine
    sig = inspect.signature(PlanningEngine.to_excel_with_values)
    assert 'previous_cycle_df' in sig.parameters
    assert 'inventory_quality_engine' in sig.parameters
    results.append(f'SIGNATURE OK: {list(sig.parameters.keys())}')
    
    from modules.cycle_manager import CycleManager
    from modules.mom_comparison_engine import MoMComparisonEngine
    from modules.database_exporter import DatabaseExporter
    results.append('MODULES OK: all 3 new modules import')
    
    results.append('')
    results.append('=== ALL CHECKS PASSED ===')
except Exception as e:
    results.append(f'FAILED: {e}')

with open('_check_result.txt', 'w') as f:
    f.write('\n'.join(results))
print('\n'.join(results))
