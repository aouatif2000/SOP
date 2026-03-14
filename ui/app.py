"""S&OP Planning Engine - Flask Web UI"""

from flask import Flask, render_template, request, jsonify, send_file
import pandas as pd
from pathlib import Path
from datetime import datetime
import sys
import io
import json

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.planning_engine import PlanningEngine
from modules.models import LineType

app = Flask(__name__, 
            template_folder=str(Path(__file__).parent / 'templates'),
            static_folder=str(Path(__file__).parent / 'static'))

import uuid as _uuid

sessions: dict = {}           # session_id -> session dict
active_session_id: str = None  # currently selected session


def _get_active():
    """Return (session_dict, engine) for the active session, or (None, None)."""
    sess = sessions.get(active_session_id)
    if not sess:
        return None, None
    return sess, sess.get('engine')


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/upload', methods=['POST'])
def upload_file():
    global sessions, active_session_id

    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    upload_dir = Path(__file__).parent.parent / 'uploads'
    upload_dir.mkdir(exist_ok=True)

    file_path = upload_dir / file.filename
    file.save(str(file_path))

    try:
        # Load metadata only — full calculation is triggered by /api/calculate
        from modules.data_loader import DataLoader
        loader = DataLoader(str(file_path))
        loader.load_all()

        site = getattr(loader.config, 'site', '') or ''
        planning_month = getattr(loader.config, 'planning_month', '') or ''

        session_id = str(_uuid.uuid4())
        sessions[session_id] = {
            'id': session_id,
            'file_path': str(file_path),
            'filename': file.filename,
            'engine': None,
            'value_results': {},
            'metadata': {
                'materials': len(loader.materials),
                'bom_items': len(loader.bom),
                'machines': len(loader.machines),
                'periods': len(loader.periods),
                'site': site,
                'planning_month': str(planning_month),
            },
            'uploaded_at': datetime.now().isoformat(),
        }
        active_session_id = session_id

        return jsonify({
            'success': True,
            'session_id': session_id,
            'filename': file.filename,
            'summary': {
                'materials': len(loader.materials),
                'bom_items': len(loader.bom),
                'machines': len(loader.machines),
                'periods': len(loader.periods),
            }
        })
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500


@app.route('/api/calculate', methods=['POST'])
def run_calculations():
    global sessions
    sess, _ = _get_active()
    if sess is None:
        return jsonify({'error': 'No file uploaded'}), 400

    try:
        # Get user input parameters from request (handle both JSON and form data)
        if request.is_json:
            req_data = request.get_json() or {}
        else:
            req_data = request.form.to_dict() or {}

        planning_month = req_data.get('planning_month', None)
        months_actuals = int(req_data.get('months_actuals', 0) or 0)
        months_forecast = int(req_data.get('months_forecast', 12) or 12)

        print(f"\nUser Input Parameters:")
        print(f"  Planning Month: {planning_month}")
        print(f"  Months of Actuals: {months_actuals}")
        print(f"  Months of Forecast: {months_forecast}")

        engine = PlanningEngine(
            sess['file_path'],
            planning_month=planning_month,
            months_actuals=months_actuals,
            months_forecast=months_forecast
        )
        engine.run()
        sess['engine'] = engine

        return jsonify({
            'success': True,
            'summary': engine.get_summary(),
            'parameters': {
                'planning_month': planning_month,
                'months_actuals': months_actuals,
                'months_forecast': months_forecast
            }
        })
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500


@app.route('/api/results')
def get_results():
    _, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400

    results = {}
    for lt, rows in current_engine.results.items():
        results[lt] = [row.to_dict() for row in rows]

    return jsonify({
        'periods': current_engine.data.periods,
        'results': results
    })


@app.route('/api/value_results')
def get_value_results():
    """Return value planning results (financial)."""
    _, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400

    if not current_engine.value_results:
        return jsonify({'error': 'No value planning results available'}), 400
    
    results = {}
    for lt, rows in current_engine.value_results.items():
        results[lt] = [row.to_dict() for row in rows]
    
    # Extract consolidation rows separately for the financial overview
    consolidation = []
    from modules.models import LineType
    for row in current_engine.value_results.get(LineType.CONSOLIDATION.value, []):
        consolidation.append(row.to_dict())
    
    return jsonify({
        'periods': current_engine.data.periods,
        'results': results,
        'consolidation': consolidation
    })


@app.route('/api/dashboard')
def get_dashboard():
    """Aggregated dashboard endpoint — single call returns all KPIs + chart data."""
    _, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400

    from modules.models import LineType

    periods = current_engine.data.periods

    # ── KPI: materials count ────────────────────────────────────────────────
    materials_count = len(current_engine.data.materials)

    # ── KPI: avg utilization from Line 10 ──────────────────────────────────
    util_rows = current_engine.results.get(LineType.UTILIZATION_RATE.value, [])
    all_util_vals = [v * 100 for row in util_rows for v in row.values.values() if v is not None]
    avg_utilization = round(sum(all_util_vals) / len(all_util_vals), 1) if all_util_vals else 0.0

    # ── KPI: total FTE from Line 12, sum across all groups for latest period ─
    fte_rows = current_engine.results.get(LineType.FTE_REQUIREMENTS.value, [])
    latest_period = periods[-1] if periods else None
    total_fte = round(
        sum(row.values.get(latest_period, 0.0) for row in fte_rows), 2
    ) if latest_period else 0.0

    # ── utilization_by_machine (Line 10 rows, values as %) ─────────────────
    utilization_by_machine = []
    for row in util_rows:
        utilization_by_machine.append({
            'machine': row.material_name,
            'group': row.aux_column or '',
            'values': {p: round(v * 100, 1) for p, v in row.values.items()},
        })

    # ── fte_by_group (Line 12 rows) ─────────────────────────────────────────
    fte_by_group = []
    for row in fte_rows:
        fte_by_group.append({
            'group': row.material_name,
            'values': {p: round(v, 2) for p, v in row.values.items()},
        })

    # ── financials from consolidation rows ──────────────────────────────────
    financials = {}
    for row in current_engine.value_results.get(LineType.CONSOLIDATION.value, []):
        key = row.material_number.replace('ZZZZZZ_', '')
        financials[key] = {p: round(v, 0) for p, v in row.values.items()}

    # ── inventory quality (graceful degradation if fix #10 not applied) ─────
    inventory_quality: list = []
    top_10_overstocks: list = []
    total_overstock = 0.0
    try:
        from modules.inventory_quality_engine import InventoryQualityEngine
        iq_engine = InventoryQualityEngine(
            current_engine.data,
            current_engine.results,
            current_engine.value_results,
        )
        iq_result = iq_engine.calculate()
        inventory_quality = iq_result.get('per_material', [])
        top_10_overstocks = iq_result.get('top_10_overstocks', [])
        total_overstock = iq_result.get('total_overstock', 0.0)
    except (ImportError, Exception):
        pass

    return jsonify({
        'periods': periods,
        'kpis': {
            'materials': materials_count,
            'avg_utilization': avg_utilization,
            'total_fte': total_fte,
            'total_overstock': total_overstock,
        },
        'utilization_by_machine': utilization_by_machine,
        'fte_by_group': fte_by_group,
        'financials': financials,
        'inventory_quality': inventory_quality,
        'top_10_overstocks': top_10_overstocks,
    })


@app.route('/api/capacity')
def get_capacity():
    _, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400
    
    from modules.models import LineType
    
    utilization = []
    for row in current_engine.results.get(LineType.UTILIZATION_RATE.value, []):
        utilization.append({
            'machine': row.material_name,
            'values': {p: round(v * 100, 1) for p, v in row.values.items()}
        })
    
    return jsonify({
        'periods': current_engine.data.periods,
        'utilization': utilization
    })


@app.route('/api/inventory')
def get_inventory():
    _, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400
    
    from modules.models import LineType
    
    inv_rows = current_engine.results.get(LineType.INVENTORY.value, [])
    tgt_rows = current_engine.results.get(LineType.MIN_TARGET_STOCK.value, [])
    
    target_lookup = {r.material_number: r.values for r in tgt_rows}
    periods = current_engine.data.periods[:6]
    
    data = []
    ok, low, high = 0, 0, 0
    
    for row in inv_rows:
        target = target_lookup.get(row.material_number, {})
        avg_inv = sum(row.values.get(p, 0) for p in periods) / len(periods) if periods else 0
        avg_tgt = sum(target.get(p, 0) for p in periods) / len(periods) if target and periods else 0
        
        status = 'OK'
        if avg_tgt > 0:
            if avg_inv < avg_tgt * 0.5:
                status = 'LOW'
                low += 1
            elif avg_inv > avg_tgt * 2:
                status = 'HIGH'
                high += 1
            else:
                ok += 1
        else:
            ok += 1
        
        data.append({
            'material_number': row.material_number,
            'material_name': row.material_name,
            'status': status,
            'values': row.values
        })
    
    return jsonify({
        'periods': current_engine.data.periods,
        'summary': {'healthy': ok, 'low': low, 'high': high},
        'data': data
    })


@app.route('/api/inventory_quality')
def get_inventory_quality():
    _, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400

    from modules.inventory_quality_engine import InventoryQualityEngine
    engine = InventoryQualityEngine(
        current_engine.data,
        current_engine.results,
        current_engine.value_results,
    )
    return jsonify(engine.calculate())


@app.route('/api/export')
def export():
    _, current_engine = _get_active()

    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400

    export_dir = Path(__file__).parent.parent / 'exports'
    export_dir.mkdir(exist_ok=True)

    export_path = export_dir / f'SOP_Python_Results_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
    current_engine.to_excel_with_values(str(export_path))

    # Apply edit highlights and summary sheet if there are any edits
    _apply_edit_highlights(str(export_path), current_engine)

    return send_file(str(export_path), as_attachment=True)


def _apply_edit_highlights(path: str, engine):
    """Open the exported workbook and apply edit highlights + summary sheet."""
    import openpyxl
    from openpyxl.styles import PatternFill, Font
    from openpyxl.comments import Comment

    # Collect all edit
    all_edits = []
    for lt, rows in engine.results.items():
        for row in rows:
            if row.manual_edits:
                for period, edit_data in row.manual_edits.items():
                    original = edit_data.get('original', 0.0)
                    new_val = edit_data.get('new', 0.0)
                    delta_pct = round((new_val - original) / abs(original) * 100, 2) if original != 0 else 0.0
                    all_edits.append({
                        'line_type': row.line_type,
                        'material_number': row.material_number,
                        'material_name': row.material_name,
                        'period': period,
                        'original': original,
                        'new': new_val,
                        'delta_pct': delta_pct,
                    })

    if not all_edits:
        return

    wb = openpyxl.load_workbook(path)
    ws = wb['Planning sheet']

    # Build column lookups from header row
    header = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
    period_col = {}
    mat_col_idx = None
    lt_col_idx = None
    for i, val in enumerate(header, start=1):
        if val is None:
            continue
        s = str(val)
        period_col[s] = i
        if s == 'Material number':
            mat_col_idx = i
        elif s == 'Line type':
            lt_col_idx = i

    # Build row lookup: (material_number, line_type) -> row_idx
    row_lookup = {}
    if mat_col_idx and lt_col_idx:
        for row_idx, row_data in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            mat_val = row_data[mat_col_idx - 1]
            lt_val = row_data[lt_col_idx - 1]
            if mat_val and lt_val:
                row_lookup[(str(mat_val), str(lt_val))] = row_idx

    # Fill styles
    yellow_fill = PatternFill(start_color='FFEB3B', end_color='FFEB3B', fill_type='solid')
    green_fill = PatternFill(start_color='C8E6C9', end_color='C8E6C9', fill_type='solid')
    red_fill = PatternFill(start_color='FFCDD2', end_color='FFCDD2', fill_type='solid')
    bold_font = Font(bold=True)

    for edit in all_edits:
        row_idx = row_lookup.get((edit['material_number'], edit['line_type']))
        col_idx = period_col.get(edit['period'])
        if row_idx is None or col_idx is None:
            continue
        cell = ws.cell(row=row_idx, column=col_idx)
        original = edit['original']
        new_val = edit['new']
        delta_pct = edit['delta_pct']
        if new_val > original:
            cell.fill = green_fill
            cell.font = bold_font
        elif new_val < original:
            cell.fill = red_fill
            cell.font = bold_font
        else:
            cell.fill = yellow_fill
        cell.comment = Comment(f"Original: {original}\nNew: {new_val}\nDelta: {delta_pct}%", 'SOP Engine')

    # Edits Summary sheet
    if 'Edits Summary' in wb.sheetnames:
        del wb['Edits Summary']
    ws_edits = wb.create_sheet('Edits Summary')
    ws_edits.append(['Line Type', 'Material Number', 'Material Name', 'Period',
                     'Original Value', 'New Value', 'Delta %'])
    for edit in all_edits:
        ws_edits.append([edit['line_type'], edit['material_number'], edit['material_name'],
                         edit['period'], edit['original'], edit['new'], edit['delta_pct']])

    wb.save(path)


@app.route('/api/update_volume', methods=['POST'])
def update_volume():
    _, current_engine = _get_active()

    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No JSON body'}), 400

    line_type = data.get('line_type')
    material_number = data.get('material_number')
    period = data.get('period')
    new_value = float(data.get('new_value', 0))

    rows = current_engine.results.get(line_type, [])
    target_row = next((r for r in rows if r.material_number == material_number), None)
    if target_row is None:
        return jsonify({'error': 'Row not found'}), 404

    old_value = target_row.get_value(period)

    # Preserve the very first original value
    if period not in target_row.manual_edits:
        target_row.manual_edits[period] = {'original': old_value, 'new': new_value}
    else:
        target_row.manual_edits[period]['new'] = new_value

    target_row.set_value(period, new_value)

    if line_type == LineType.MIN_TARGET_STOCK.value:
        # Full cascade: Line 05 → Line 06 (prod+purch) → Line 04 (inventory) →
        #   Line 07 (purchase plan) → Line 08 (dep req) → Lines 07cap/09/10/11/12 → values
        from modules.inventory_engine import InventoryEngine
        from modules.capacity_engine import CapacityEngine
        from modules.bom_engine import BOMEngine
        from modules.value_planning_engine import ValuePlanningEngine

        preserved_edits = dict(target_row.manual_edits)
        periods_list = current_engine.data.periods

        # Reconstruct forecast (Line 01) for this material
        l01_rows = current_engine.results.get(LineType.DEMAND_FORECAST.value, [])
        fc_row = next((r for r in l01_rows if r.material_number == material_number), None)
        forecast_vals = dict(fc_row.values) if fc_row else {p: 0.0 for p in periods_list}

        # Reconstruct aggregated + per-parent dependent demand (Line 02)
        l02_rows = current_engine.results.get(LineType.DEPENDENT_DEMAND.value, [])
        mat_l02 = [r for r in l02_rows if r.material_number == material_number]
        dep_demand_agg = {p: 0.0 for p in periods_list}
        dep_demand_by_parent = {}
        for r in mat_l02:
            parent = r.aux_column
            if parent:
                dep_demand_by_parent[parent] = dict(r.values)
                for p in periods_list:
                    dep_demand_agg[p] = dep_demand_agg.get(p, 0.0) + r.values.get(p, 0.0)

        # Step 1-4: re-run inventory engine with overridden target stock
        inv_eng = InventoryEngine(current_engine.data)
        inv_result = inv_eng.calculate_for_material(
            material_number, forecast_vals, dep_demand_agg, dep_demand_by_parent,
            override_target_stock=new_value
        )

        # Replace Lines 03/04/05/06/07-purchplan for this material
        inv_line_types = [
            LineType.TOTAL_DEMAND.value, LineType.INVENTORY.value,
            LineType.MIN_TARGET_STOCK.value, LineType.PRODUCTION_PLAN.value,
            LineType.PURCHASE_RECEIPT.value, LineType.PURCHASE_PLAN.value,
        ]
        for lt in inv_line_types:
            current_engine.results[lt] = [
                r for r in current_engine.results.get(lt, []) if r.material_number != material_number
            ]
        for row in inv_result['rows']:
            if row.line_type in current_engine.results:
                current_engine.results[row.line_type].append(row)

        # Restore manual_edits onto the newly created Line 05 row
        new_l05 = next(
            (r for r in current_engine.results.get(LineType.MIN_TARGET_STOCK.value, [])
             if r.material_number == material_number), None
        )
        if new_l05:
            new_l05.manual_edits = preserved_edits

        # Update cross-material production tracking dicts
        if inv_result['production_plan'] is not None:
            current_engine.all_production_plans[material_number] = inv_result['production_plan']
        else:
            current_engine.all_production_plans.pop(material_number, None)
        if inv_result['purchase_receipt'] is not None:
            current_engine.all_purchase_receipts[material_number] = inv_result['purchase_receipt']
        else:
            current_engine.all_purchase_receipts.pop(material_number, None)

        # Step 7: Rebuild Line 08 (Dependent Requirements) for this material
        bom_eng = BOMEngine(current_engine.data)
        current_engine.results[LineType.DEPENDENT_REQUIREMENTS.value] = [
            r for r in current_engine.results.get(LineType.DEPENDENT_REQUIREMENTS.value, [])
            if r.material_number != material_number
        ]
        children_demand = {}
        if inv_result['production_plan'] is not None:
            children_demand = bom_eng.compute_dependent_requirements(
                material_number, inv_result['production_plan']
            )
            if children_demand:
                dr_rows = bom_eng.create_dependent_requirements_rows(material_number, children_demand)
                current_engine.results[LineType.DEPENDENT_REQUIREMENTS.value].extend(dr_rows)

        # Steps 10/11: Update child Line 02 and recalculate child Line 03
        for child_mat, child_period_demand in children_demand.items():
            # Remove the stale contribution of this parent from child's Line 02
            current_engine.results[LineType.DEPENDENT_DEMAND.value] = [
                r for r in current_engine.results.get(LineType.DEPENDENT_DEMAND.value, [])
                if not (r.material_number == child_mat and r.aux_column == material_number)
            ]
            child_l02_new = bom_eng.create_dependent_demand_rows(
                child_mat, {material_number: child_period_demand}
            )
            current_engine.results[LineType.DEPENDENT_DEMAND.value].extend(child_l02_new)

            # Recompute child Line 03 = child L01 + all child L02 rows
            child_l01_row = next(
                (r for r in current_engine.results.get(LineType.DEMAND_FORECAST.value, [])
                 if r.material_number == child_mat), None
            )
            child_l03_row = next(
                (r for r in current_engine.results.get(LineType.TOTAL_DEMAND.value, [])
                 if r.material_number == child_mat), None
            )
            if child_l03_row:
                child_all_l02 = [
                    r for r in current_engine.results.get(LineType.DEPENDENT_DEMAND.value, [])
                    if r.material_number == child_mat
                ]
                for p in periods_list:
                    fc_val = child_l01_row.values.get(p, 0.0) if child_l01_row else 0.0
                    dep_val = sum(r.values.get(p, 0.0) for r in child_all_l02)
                    child_l03_row.values[p] = fc_val + dep_val

        # Steps 6/8/9/10: Re-run capacity engine (Lines 07cap, 09, 10, 11, 12)
        l01_forecasts = {r.material_number: r.values for r in current_engine.results.get(LineType.DEMAND_FORECAST.value, [])}
        cap_eng = CapacityEngine(current_engine.data, current_engine.all_production_plans, l01_forecasts)
        cap_results = cap_eng.calculate()
        for lt, cap_rows in cap_results.items():
            current_engine.results[lt] = cap_rows

        # Re-run value planning
        current_engine.value_engine = ValuePlanningEngine(current_engine.data, current_engine.results)
        current_engine.value_results = current_engine.value_engine.calculate()

    elif line_type == LineType.DEMAND_FORECAST.value:
        # Full cascade: Line 01 → Lines 03-07 → Line 08 → child Lines 02/03
        #   → Lines 07cap/09/10/11/12 → values
        from modules.inventory_engine import InventoryEngine
        from modules.capacity_engine import CapacityEngine
        from modules.bom_engine import BOMEngine
        from modules.value_planning_engine import ValuePlanningEngine

        periods_list = current_engine.data.periods

        # Reconstruct forecast (Line 01) — already has new_value applied via set_value above
        l01_rows = current_engine.results.get(LineType.DEMAND_FORECAST.value, [])
        fc_row = next((r for r in l01_rows if r.material_number == material_number), None)
        forecast_vals = dict(fc_row.values) if fc_row else {p: 0.0 for p in periods_list}

        # Reconstruct aggregated + per-parent dependent demand (Line 02)
        l02_rows = current_engine.results.get(LineType.DEPENDENT_DEMAND.value, [])
        mat_l02 = [r for r in l02_rows if r.material_number == material_number]
        dep_demand_agg = {p: 0.0 for p in periods_list}
        dep_demand_by_parent = {}
        for r in mat_l02:
            parent = r.aux_column
            if parent:
                dep_demand_by_parent[parent] = dict(r.values)
                for p in periods_list:
                    dep_demand_agg[p] = dep_demand_agg.get(p, 0.0) + r.values.get(p, 0.0)

        # Save current Line 05 — VBA step 3: target stock stays unchanged
        l05_rows = current_engine.results.get(LineType.MIN_TARGET_STOCK.value, [])
        l05_row = next((r for r in l05_rows if r.material_number == material_number), None)
        l05_saved_values = dict(l05_row.values) if l05_row else {}
        l05_saved_edits = dict(l05_row.manual_edits) if l05_row else {}

        # Steps 1/2/4/5/6: re-run inventory engine with updated forecast
        inv_eng = InventoryEngine(current_engine.data)
        inv_result = inv_eng.calculate_for_material(
            material_number, forecast_vals, dep_demand_agg, dep_demand_by_parent,
            override_forecast=forecast_vals,
        )

        # Replace Lines 03/04/05/06/07-purchplan for this material
        inv_line_types = [
            LineType.TOTAL_DEMAND.value, LineType.INVENTORY.value,
            LineType.MIN_TARGET_STOCK.value, LineType.PRODUCTION_PLAN.value,
            LineType.PURCHASE_RECEIPT.value, LineType.PURCHASE_PLAN.value,
        ]
        for lt in inv_line_types:
            current_engine.results[lt] = [
                r for r in current_engine.results.get(lt, []) if r.material_number != material_number
            ]
        for row in inv_result['rows']:
            if row.line_type in current_engine.results:
                current_engine.results[row.line_type].append(row)

        # Step 3: Restore Line 05 to pre-cascade values (stays unchanged per VBA)
        new_l05 = next(
            (r for r in current_engine.results.get(LineType.MIN_TARGET_STOCK.value, [])
             if r.material_number == material_number), None
        )
        if new_l05:
            new_l05.values = l05_saved_values
            new_l05.manual_edits = l05_saved_edits

        # Update cross-material production tracking dicts
        if inv_result['production_plan'] is not None:
            current_engine.all_production_plans[material_number] = inv_result['production_plan']
        else:
            current_engine.all_production_plans.pop(material_number, None)
        if inv_result['purchase_receipt'] is not None:
            current_engine.all_purchase_receipts[material_number] = inv_result['purchase_receipt']
        else:
            current_engine.all_purchase_receipts.pop(material_number, None)

        # Step 9: Rebuild Line 08 (Dependent Requirements) for this material
        bom_eng = BOMEngine(current_engine.data)
        current_engine.results[LineType.DEPENDENT_REQUIREMENTS.value] = [
            r for r in current_engine.results.get(LineType.DEPENDENT_REQUIREMENTS.value, [])
            if r.material_number != material_number
        ]
        children_demand = {}
        if inv_result['production_plan'] is not None:
            children_demand = bom_eng.compute_dependent_requirements(
                material_number, inv_result['production_plan']
            )
            if children_demand:
                dr_rows = bom_eng.create_dependent_requirements_rows(material_number, children_demand)
                current_engine.results[LineType.DEPENDENT_REQUIREMENTS.value].extend(dr_rows)

        # Steps 10/11: Update child Line 02 and recalculate child Line 03
        for child_mat, child_period_demand in children_demand.items():
            # Remove the stale contribution of this parent from child's Line 02
            current_engine.results[LineType.DEPENDENT_DEMAND.value] = [
                r for r in current_engine.results.get(LineType.DEPENDENT_DEMAND.value, [])
                if not (r.material_number == child_mat and r.aux_column == material_number)
            ]
            child_l02_new = bom_eng.create_dependent_demand_rows(
                child_mat, {material_number: child_period_demand}
            )
            current_engine.results[LineType.DEPENDENT_DEMAND.value].extend(child_l02_new)

            # Recompute child Line 03 = child L01 + all child L02 rows
            child_l01_row = next(
                (r for r in current_engine.results.get(LineType.DEMAND_FORECAST.value, [])
                 if r.material_number == child_mat), None
            )
            child_l03_row = next(
                (r for r in current_engine.results.get(LineType.TOTAL_DEMAND.value, [])
                 if r.material_number == child_mat), None
            )
            if child_l03_row:
                child_all_l02 = [
                    r for r in current_engine.results.get(LineType.DEPENDENT_DEMAND.value, [])
                    if r.material_number == child_mat
                ]
                for p in periods_list:
                    fc_val = child_l01_row.values.get(p, 0.0) if child_l01_row else 0.0
                    dep_val = sum(r.values.get(p, 0.0) for r in child_all_l02)
                    child_l03_row.values[p] = fc_val + dep_val

        # Steps 8/12/13: Re-run capacity engine (Lines 07cap, 09, 10, 11, 12)
        l01_forecasts = {r.material_number: r.values for r in current_engine.results.get(LineType.DEMAND_FORECAST.value, [])}
        cap_eng = CapacityEngine(current_engine.data, current_engine.all_production_plans, l01_forecasts)
        cap_results = cap_eng.calculate()
        for lt, cap_rows in cap_results.items():
            current_engine.results[lt] = cap_rows

        # Step 14: Re-run value planning
        current_engine.value_engine = ValuePlanningEngine(current_engine.data, current_engine.results)
        current_engine.value_results = current_engine.value_engine.calculate()

    else:
        # Line 06: value already set above; just refresh value planning
        from modules.value_planning_engine import ValuePlanningEngine
        current_engine.value_engine = ValuePlanningEngine(current_engine.data, current_engine.results)
        current_engine.value_results = current_engine.value_engine.calculate()

    delta_pct = round((new_value - old_value) / abs(old_value) * 100, 2) if old_value != 0 else 0.0

    results_dict = {lt: [r.to_dict() for r in rs] for lt, rs in current_engine.results.items()}
    value_results_dict = {lt: [r.to_dict() for r in rs] for lt, rs in current_engine.value_results.items()}
    consolidation = [r.to_dict() for r in current_engine.value_results.get(LineType.CONSOLIDATION.value, [])]

    return jsonify({
        'success': True,
        'results': results_dict,
        'value_results': value_results_dict,
        'consolidation': consolidation,
        'edit_meta': {'old_value': old_value, 'new_value': new_value, 'delta_pct': delta_pct},
    })


@app.route('/api/edits/export')
def export_edits():
    _, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400

    edits = []
    for lt, rows in current_engine.results.items():
        for row in rows:
            if row.manual_edits:
                for period, edit_data in row.manual_edits.items():
                    original = edit_data.get('original', 0.0)
                    new_val = edit_data.get('new', 0.0)
                    delta_pct = round((new_val - original) / abs(original) * 100, 2) if original != 0 else 0.0
                    edits.append({
                        'line_type': row.line_type,
                        'material_number': row.material_number,
                        'period': period,
                        'original': original,
                        'new': new_val,
                        'delta_pct': delta_pct,
                    })

    export_data = {'exported_at': datetime.now().isoformat(), 'edits': edits}
    buf = io.BytesIO(json.dumps(export_data, indent=2).encode('utf-8'))
    buf.seek(0)
    return send_file(buf, mimetype='application/json', as_attachment=True,
                     download_name='edits.json')


@app.route('/api/edits/import', methods=['POST'])
def import_edits():
    _, current_engine = _get_active()

    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400

    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No JSON body'}), 400

        edits = data.get('edits', [])
        for edit in edits:
            line_type = edit.get('line_type')
            material_number = edit.get('material_number')
            period = edit.get('period')
            new_value = float(edit.get('new', 0))
            original = float(edit.get('original', 0))

            rows = current_engine.results.get(line_type, [])
            target_row = next((r for r in rows if r.material_number == material_number), None)
            if target_row is not None:
                target_row.manual_edits[period] = {'original': original, 'new': new_value}
                target_row.set_value(period, new_value)

        # Re-run value planning
        from modules.value_planning_engine import ValuePlanningEngine
        current_engine.value_engine = ValuePlanningEngine(current_engine.data, current_engine.results)
        current_engine.value_results = current_engine.value_engine.calculate()

        results_dict = {lt: [r.to_dict() for r in rs] for lt, rs in current_engine.results.items()}
        value_results_dict = {lt: [r.to_dict() for r in rs] for lt, rs in current_engine.value_results.items()}
        consolidation = [r.to_dict() for r in current_engine.value_results.get(LineType.CONSOLIDATION.value, [])]

        return jsonify({
            'success': True,
            'results': results_dict,
            'value_results': value_results_dict,
            'consolidation': consolidation,
        })
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500


# ---- Session management endpoints ----

@app.route('/api/sessions')
def list_sessions():
    """Return all sessions grouped by year/month/site."""
    grouped: dict = {}
    for sid, sess in sessions.items():
        meta = sess.get('metadata', {})
        site = meta.get('site', 'Unknown')
        pm   = str(meta.get('planning_month', '')) or 'Unknown'
        year = pm[:4] if len(pm) >= 4 else 'Unknown'
        month = pm[5:7] if len(pm) >= 7 else 'Unknown'
        key = f"{year}/{month}/{site}"
        grouped.setdefault(key, [])
        grouped[key].append({
            'id':             sid,
            'filename':       sess.get('filename', ''),
            'site':           site,
            'planning_month': pm,
            'uploaded_at':    sess.get('uploaded_at', ''),
            'calculated':     sess.get('engine') is not None,
            'active':         sid == active_session_id,
            'metadata':       meta,
        })
    return jsonify({'active_session_id': active_session_id, 'groups': grouped})


@app.route('/api/sessions/switch', methods=['POST'])
def switch_session():
    """Set a different session as active. Returns session metadata."""
    global active_session_id
    req = request.get_json() or {}
    sid = req.get('session_id')
    if not sid or sid not in sessions:
        return jsonify({'error': 'Session not found'}), 404
    active_session_id = sid
    sess = sessions[sid]
    return jsonify({
        'success': True,
        'active_session_id': sid,
        'filename': sess.get('filename', ''),
        'metadata': sess.get('metadata', {}),
        'calculated': sess.get('engine') is not None,
    })


@app.route('/api/sessions/<session_id>', methods=['DELETE'])
def delete_session(session_id):
    """Remove a session. Activates the next available session if deleted was active."""
    global active_session_id, sessions
    if session_id not in sessions:
        return jsonify({'error': 'Session not found'}), 404
    del sessions[session_id]
    if active_session_id == session_id:
        active_session_id = next(iter(sessions), None)
    return jsonify({'success': True, 'active_session_id': active_session_id})


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
