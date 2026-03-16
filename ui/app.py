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
scenarios: dict = {}          # scenario_id -> scenario snapshot

SESSIONS_STORE = Path(__file__).parent.parent / 'sessions_store.json'


def _save_sessions_to_disk():
    """Persist session metadata (no engine objects) to sessions_store.json."""
    try:
        serializable = {}
        for sid, sess in sessions.items():
            serializable[sid] = {
                'id':          sess.get('id', sid),
                'file_path':   sess.get('file_path', ''),
                'filename':    sess.get('filename', ''),
                'custom_name': sess.get('custom_name'),
                'metadata':    sess.get('metadata', {}),
                'uploaded_at': sess.get('uploaded_at', ''),
                'parameters':  sess.get('parameters'),
            }
        store = {
            'active_session_id': active_session_id,
            'sessions':          serializable,
        }
        with open(SESSIONS_STORE, 'w', encoding='utf-8') as f:
            json.dump(store, f, indent=2, default=str)
    except Exception as exc:
        print(f'[sessions] save error: {exc}')


def _load_sessions_from_disk():
    """Restore session metadata from sessions_store.json on startup."""
    global sessions, active_session_id
    if not SESSIONS_STORE.exists():
        return
    try:
        with open(SESSIONS_STORE, 'r', encoding='utf-8') as f:
            store = json.load(f)
        for sid, data in store.get('sessions', {}).items():
            sessions[sid] = {
                'id':           data.get('id', sid),
                'file_path':    data.get('file_path', ''),
                'filename':     data.get('filename', ''),
                'custom_name':  data.get('custom_name'),
                'engine':       None,   # must re-calculate after restart
                'value_results': {},
                'metadata':     data.get('metadata', {}),
                'uploaded_at':  data.get('uploaded_at', ''),
                'parameters':   data.get('parameters'),
                'undo_stack':   [],
                'redo_stack':   [],
            }
        saved_active = store.get('active_session_id')
        if saved_active and saved_active in sessions:
            active_session_id = saved_active
        elif sessions:
            active_session_id = next(iter(sessions))
        print(f'[sessions] loaded {len(sessions)} session(s) from disk')
    except Exception as exc:
        print(f'[sessions] load error: {exc}')


_load_sessions_from_disk()


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
            'custom_name': None,
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
        sessions[session_id]['undo_stack'] = []
        sessions[session_id]['redo_stack'] = []
        active_session_id = session_id
        _save_sessions_to_disk()

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
        sess['parameters'] = {
            'planning_month':  planning_month,
            'months_actuals':  months_actuals,
            'months_forecast': months_forecast,
        }
        _save_sessions_to_disk()

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

    # Aggregate total demand (Line 03) across all materials per period
    demand_trend = {}
    for row in current_engine.results.get('03. Total demand', []):
        for p in periods:
            demand_trend[p] = round(demand_trend.get(p, 0.0) + row.values.get(p, 0.0), 1)

    # Aggregate inventory (Line 04) and target stock (Line 05) per period
    inventory_trend = {}
    target_trend = {}
    for row in current_engine.results.get('04. Inventory', []):
        for p in periods:
            inventory_trend[p] = round(inventory_trend.get(p, 0.0) + row.values.get(p, 0.0), 1)
    for row in current_engine.results.get('05. Minimum target stock', []):
        for p in periods:
            target_trend[p] = round(target_trend.get(p, 0.0) + row.values.get(p, 0.0), 1)

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
        'demand_trend': demand_trend,
        'inventory_trend': inventory_trend,
        'target_trend': target_trend,
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
    periods = current_engine.data.periods
    
    data = []
    ok, low, high = 0, 0, 0
    
    for row in inv_rows:
        target = target_lookup.get(row.material_number, {})
        avg_inv = sum(row.values.get(p, 0) for p in periods) / len(periods) if periods else 0
        avg_tgt = sum(target.get(p, 0) for p in periods) / len(periods) if target and periods else 0
        
        status = 'OK'
        if avg_inv <= 0:
            status = 'LOW'
            low += 1
        elif avg_tgt > 0:
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

    # Build inventory quality engine to pass for Top 10 sheet
    _iq_engine_export = None
    try:
        from modules.inventory_quality_engine import InventoryQualityEngine
        _iq_engine_export = InventoryQualityEngine(
            current_engine.data,
            current_engine.results,
            current_engine.value_results,
        )
    except Exception:
        pass

    current_engine.to_excel_with_values(str(export_path), inventory_quality_engine=_iq_engine_export)

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
    sess, current_engine = _get_active()

    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No JSON body'}), 400

    line_type = data.get('line_type')
    material_number = data.get('material_number')
    period = data.get('period')
    new_value = float(data.get('new_value', 0))

    return _apply_volume_change(sess, current_engine, line_type, material_number, period, new_value,
                                 push_undo=True)


@app.route('/api/undo', methods=['POST'])
def undo_edit():
    sess, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400
    undo_stack = sess.get('undo_stack', [])
    redo_stack = sess.setdefault('redo_stack', [])
    if not undo_stack:
        return jsonify({'error': 'Nothing to undo'}), 400

    entry = undo_stack.pop()
    line_type = entry['line_type']
    material_number = entry['material_number']
    period = entry['period']
    restore_value = entry['old_value']

    # Push onto redo stack before restoring
    redo_stack.append(entry)
    if len(redo_stack) > 50:
        redo_stack.pop(0)

    # Apply restored value by delegating to update_volume logic (via internal helper)
    return _apply_volume_change(sess, current_engine, line_type, material_number, period, restore_value,
                                 push_undo=False)


@app.route('/api/redo', methods=['POST'])
def redo_edit():
    sess, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400
    undo_stack = sess.setdefault('undo_stack', [])
    redo_stack = sess.get('redo_stack', [])
    if not redo_stack:
        return jsonify({'error': 'Nothing to redo'}), 400

    entry = redo_stack.pop()
    line_type = entry['line_type']
    material_number = entry['material_number']
    period = entry['period']
    redo_value = entry['new_value']

    undo_stack.append(entry)
    if len(undo_stack) > 50:
        undo_stack.pop(0)

    return _apply_volume_change(sess, current_engine, line_type, material_number, period, redo_value,
                                 push_undo=False)


def _apply_volume_change(sess, current_engine, line_type, material_number, period, new_value,
                          push_undo=True):
    """Internal helper: apply a volume change + cascade and return jsonify result.

    Used by /api/update_volume (via direct code), /api/undo, /api/redo.
    """
    rows = current_engine.results.get(line_type, [])
    target_row = next((r for r in rows if r.material_number == material_number), None)
    if target_row is None:
        return jsonify({'error': 'Row not found'}), 404

    old_value = target_row.get_value(period)

    if push_undo:
        undo_stack = sess.setdefault('undo_stack', [])
        sess.setdefault('redo_stack', []).clear()
        undo_stack.append({'line_type': line_type, 'material_number': material_number,
                           'period': period, 'old_value': old_value, 'new_value': new_value})
        if len(undo_stack) > 50:
            undo_stack.pop(0)

    # Update manual_edits tracking
    if period not in target_row.manual_edits:
        target_row.manual_edits[period] = {'original': old_value, 'new': new_value}
    else:
        target_row.manual_edits[period]['new'] = new_value
    # If restored to original, remove the edit tracking entry
    original_val = target_row.manual_edits[period].get('original', old_value)
    if new_value == original_val:
        target_row.manual_edits.pop(period, None)

    target_row.set_value(period, new_value)

    if line_type == LineType.MIN_TARGET_STOCK.value:
        from modules.inventory_engine import InventoryEngine
        from modules.capacity_engine import CapacityEngine
        from modules.bom_engine import BOMEngine
        from modules.value_planning_engine import ValuePlanningEngine

        preserved_edits = dict(target_row.manual_edits)
        periods_list = current_engine.data.periods
        l01_rows = current_engine.results.get(LineType.DEMAND_FORECAST.value, [])
        fc_row = next((r for r in l01_rows if r.material_number == material_number), None)
        forecast_vals = dict(fc_row.values) if fc_row else {p: 0.0 for p in periods_list}
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
        inv_eng = InventoryEngine(current_engine.data)
        inv_result = inv_eng.calculate_for_material(
            material_number, forecast_vals, dep_demand_agg, dep_demand_by_parent,
            override_target_stock=new_value
        )
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
        new_l05 = next(
            (r for r in current_engine.results.get(LineType.MIN_TARGET_STOCK.value, [])
             if r.material_number == material_number), None
        )
        if new_l05:
            new_l05.manual_edits = preserved_edits
        if inv_result['production_plan'] is not None:
            current_engine.all_production_plans[material_number] = inv_result['production_plan']
        else:
            current_engine.all_production_plans.pop(material_number, None)
        if inv_result['purchase_receipt'] is not None:
            current_engine.all_purchase_receipts[material_number] = inv_result['purchase_receipt']
        else:
            current_engine.all_purchase_receipts.pop(material_number, None)
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
        for child_mat, child_period_demand in children_demand.items():
            current_engine.results[LineType.DEPENDENT_DEMAND.value] = [
                r for r in current_engine.results.get(LineType.DEPENDENT_DEMAND.value, [])
                if not (r.material_number == child_mat and r.aux_column == material_number)
            ]
            child_l02_new = bom_eng.create_dependent_demand_rows(
                child_mat, {material_number: child_period_demand}
            )
            current_engine.results[LineType.DEPENDENT_DEMAND.value].extend(child_l02_new)
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
        l01_forecasts = {r.material_number: r.values for r in current_engine.results.get(LineType.DEMAND_FORECAST.value, [])}
        cap_eng = CapacityEngine(current_engine.data, current_engine.all_production_plans, l01_forecasts)
        cap_results = cap_eng.calculate()
        for lt, cap_rows in cap_results.items():
            current_engine.results[lt] = cap_rows
        current_engine.value_engine = ValuePlanningEngine(current_engine.data, current_engine.results)
        current_engine.value_results = current_engine.value_engine.calculate()

    elif line_type == LineType.DEMAND_FORECAST.value:
        from modules.inventory_engine import InventoryEngine
        from modules.capacity_engine import CapacityEngine
        from modules.bom_engine import BOMEngine
        from modules.value_planning_engine import ValuePlanningEngine

        periods_list = current_engine.data.periods
        l01_rows = current_engine.results.get(LineType.DEMAND_FORECAST.value, [])
        fc_row = next((r for r in l01_rows if r.material_number == material_number), None)
        forecast_vals = dict(fc_row.values) if fc_row else {p: 0.0 for p in periods_list}
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
        l05_rows = current_engine.results.get(LineType.MIN_TARGET_STOCK.value, [])
        l05_row = next((r for r in l05_rows if r.material_number == material_number), None)
        l05_saved_values = dict(l05_row.values) if l05_row else {}
        l05_saved_edits = dict(l05_row.manual_edits) if l05_row else {}
        inv_eng = InventoryEngine(current_engine.data)
        inv_result = inv_eng.calculate_for_material(
            material_number, forecast_vals, dep_demand_agg, dep_demand_by_parent,
            override_forecast=forecast_vals,
        )
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
        new_l05 = next(
            (r for r in current_engine.results.get(LineType.MIN_TARGET_STOCK.value, [])
             if r.material_number == material_number), None
        )
        if new_l05:
            new_l05.values = l05_saved_values
            new_l05.manual_edits = l05_saved_edits
        if inv_result['production_plan'] is not None:
            current_engine.all_production_plans[material_number] = inv_result['production_plan']
        else:
            current_engine.all_production_plans.pop(material_number, None)
        if inv_result['purchase_receipt'] is not None:
            current_engine.all_purchase_receipts[material_number] = inv_result['purchase_receipt']
        else:
            current_engine.all_purchase_receipts.pop(material_number, None)
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
        for child_mat, child_period_demand in children_demand.items():
            current_engine.results[LineType.DEPENDENT_DEMAND.value] = [
                r for r in current_engine.results.get(LineType.DEPENDENT_DEMAND.value, [])
                if not (r.material_number == child_mat and r.aux_column == material_number)
            ]
            child_l02_new = bom_eng.create_dependent_demand_rows(
                child_mat, {material_number: child_period_demand}
            )
            current_engine.results[LineType.DEPENDENT_DEMAND.value].extend(child_l02_new)
            child_l01_row = next(
                (r for r in current_engine.results.get(LineType.DEMAND_FORECAST.value, [])
                 if r.material_number == child_mat), None
            )
            child_l03_row = next(
                (r for r in current_engine.results.get(LineType.TOTAL_DEMAND.value, [])
                 if r.material_number == child_mat), None
            )
            child_all_l02 = [
                r for r in current_engine.results.get(LineType.DEPENDENT_DEMAND.value, [])
                if r.material_number == child_mat
            ]
            if child_l03_row:
                for p in periods_list:
                    fc_val = child_l01_row.values.get(p, 0.0) if child_l01_row else 0.0
                    dep_val = sum(r.values.get(p, 0.0) for r in child_all_l02)
                    child_l03_row.values[p] = fc_val + dep_val
            child_forecast_row = child_l01_row
            child_forecast_vals = dict(child_forecast_row.values) if child_forecast_row else {p: 0.0 for p in periods_list}
            child_dep_agg = {p: 0.0 for p in periods_list}
            child_dep_by_parent = {}
            for r in child_all_l02:
                parent = r.aux_column
                if parent:
                    child_dep_by_parent[parent] = dict(r.values)
                    for p in periods_list:
                        child_dep_agg[p] += r.values.get(p, 0.0)
            child_l05_rows = current_engine.results.get(LineType.MIN_TARGET_STOCK.value, [])
            child_l05 = next((r for r in child_l05_rows if r.material_number == child_mat), None)
            child_l05_saved = dict(child_l05.values) if child_l05 else {}
            child_l05_edits = dict(child_l05.manual_edits) if child_l05 else {}
            child_inv_result = inv_eng.calculate_for_material(
                child_mat, child_forecast_vals, child_dep_agg, child_dep_by_parent,
                override_forecast=child_forecast_vals,
            )
            child_inv_types = [
                LineType.TOTAL_DEMAND.value, LineType.INVENTORY.value,
                LineType.MIN_TARGET_STOCK.value, LineType.PRODUCTION_PLAN.value,
                LineType.PURCHASE_RECEIPT.value, LineType.PURCHASE_PLAN.value,
            ]
            for lt in child_inv_types:
                current_engine.results[lt] = [
                    r for r in current_engine.results.get(lt, []) if r.material_number != child_mat
                ]
            for row in child_inv_result['rows']:
                if row.line_type in current_engine.results:
                    current_engine.results[row.line_type].append(row)
            new_child_l05 = next(
                (r for r in current_engine.results.get(LineType.MIN_TARGET_STOCK.value, [])
                 if r.material_number == child_mat), None
            )
            if new_child_l05:
                new_child_l05.values = child_l05_saved
                new_child_l05.manual_edits = child_l05_edits
            if child_inv_result['production_plan'] is not None:
                current_engine.all_production_plans[child_mat] = child_inv_result['production_plan']
            else:
                current_engine.all_production_plans.pop(child_mat, None)
            if child_inv_result['purchase_receipt'] is not None:
                current_engine.all_purchase_receipts[child_mat] = child_inv_result['purchase_receipt']
            else:
                current_engine.all_purchase_receipts.pop(child_mat, None)
        l01_forecasts = {r.material_number: r.values for r in current_engine.results.get(LineType.DEMAND_FORECAST.value, [])}
        cap_eng = CapacityEngine(current_engine.data, current_engine.all_production_plans, l01_forecasts)
        cap_results = cap_eng.calculate()
        for lt, cap_rows in cap_results.items():
            current_engine.results[lt] = cap_rows
        current_engine.value_engine = ValuePlanningEngine(current_engine.data, current_engine.results)
        current_engine.value_results = current_engine.value_engine.calculate()

    else:
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


@app.route('/api/reset_edits', methods=['POST'])
def reset_edits():
    _, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400

    from modules.capacity_engine import CapacityEngine
    from modules.value_planning_engine import ValuePlanningEngine

    # Restore all manually edited rows to their original values
    for lt, rows in current_engine.results.items():
        for row in rows:
            if row.manual_edits:
                for period, edit_data in row.manual_edits.items():
                    row.set_value(period, edit_data['original'])
                row.manual_edits = {}

    # Recalculate capacity and value planning to restore cascade
    l01_forecasts = {r.material_number: r.values for r in current_engine.results.get(LineType.DEMAND_FORECAST.value, [])}
    cap_eng = CapacityEngine(current_engine.data, current_engine.all_production_plans, l01_forecasts)
    cap_results = cap_eng.calculate()
    for lt, cap_rows in cap_results.items():
        current_engine.results[lt] = cap_rows

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


# ---- Scenario endpoints ----

@app.route('/api/scenarios', methods=['GET'])
def list_scenarios():
    """List saved scenarios for the active session."""
    result = [
        {
            'id':         sid,
            'name':       sc['name'],
            'session_id': sc['session_id'],
            'timestamp':  sc['timestamp'],
            'edit_count': sc['edit_count'],
        }
        for sid, sc in scenarios.items()
        if sc['session_id'] == active_session_id
    ]
    result.sort(key=lambda x: x['timestamp'])
    return jsonify({'scenarios': result})


@app.route('/api/scenarios/save', methods=['POST'])
def save_scenario():
    """Deep-copy current volumes + cascaded values into a named scenario."""
    _, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400

    req = request.get_json() or {}
    name = req.get('name', '').strip()
    if not name:
        return jsonify({'error': 'Scenario name is required'}), 400

    # Snapshot results: {lt: [{material_number, line_type, values, manual_edits, ...}]}
    results_snapshot = {}
    total_edits = 0
    for lt, rows in current_engine.results.items():
        results_snapshot[lt] = []
        for row in rows:
            results_snapshot[lt].append({
                'material_number': row.material_number,
                'material_name':   row.material_name,
                'line_type':       row.line_type,
                'aux_column':      row.aux_column,
                'values':          dict(row.values),
                'manual_edits':    {p: dict(e) for p, e in row.manual_edits.items()},
            })
            total_edits += len(row.manual_edits)

    # Snapshot value_results
    value_snapshot = {}
    for lt, rows in current_engine.value_results.items():
        value_snapshot[lt] = []
        for row in rows:
            value_snapshot[lt].append({
                'material_number': row.material_number,
                'material_name':   row.material_name,
                'line_type':       row.line_type,
                'aux_column':      row.aux_column,
                'values':          dict(row.values),
                'manual_edits':    {},
            })

    scenario_id = str(_uuid.uuid4())
    scenarios[scenario_id] = {
        'id':             scenario_id,
        'name':           name,
        'session_id':     active_session_id,
        'timestamp':      datetime.now().isoformat(),
        'edit_count':     total_edits,
        'results':        results_snapshot,
        'value_results':  value_snapshot,
    }
    return jsonify({'success': True, 'scenario_id': scenario_id, 'name': name, 'edit_count': total_edits})


@app.route('/api/scenarios/load', methods=['POST'])
def load_scenario():
    """Restore a saved scenario into the active engine."""
    _, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400

    req = request.get_json() or {}
    scenario_id = req.get('scenario_id', '')
    if not scenario_id or scenario_id not in scenarios:
        return jsonify({'error': 'Scenario not found'}), 404

    sc = scenarios[scenario_id]
    if sc['session_id'] != active_session_id:
        return jsonify({'error': 'Scenario belongs to a different session'}), 403

    # Restore values and manual_edits on each PlanningRow
    for lt, snap_rows in sc['results'].items():
        live_rows = current_engine.results.get(lt, [])
        for snap in snap_rows:
            target = next(
                (r for r in live_rows
                 if r.material_number == snap['material_number'] and r.line_type == snap['line_type']),
                None
            )
            if target is None:
                continue
            for p, v in snap['values'].items():
                target.set_value(p, v)
            target.manual_edits = {p: dict(e) for p, e in snap['manual_edits'].items()}

    # Restore value_results
    for lt, snap_rows in sc['value_results'].items():
        live_rows = current_engine.value_results.get(lt, [])
        for snap in snap_rows:
            target = next(
                (r for r in live_rows
                 if r.material_number == snap['material_number'] and r.line_type == snap['line_type']),
                None
            )
            if target is None:
                continue
            for p, v in snap['values'].items():
                target.set_value(p, v)

    results_dict = {lt: [r.to_dict() for r in rs] for lt, rs in current_engine.results.items()}
    value_results_dict = {lt: [r.to_dict() for r in rs] for lt, rs in current_engine.value_results.items()}
    consolidation = [r.to_dict() for r in current_engine.value_results.get(LineType.CONSOLIDATION.value, [])]

    return jsonify({
        'success':       True,
        'scenario_id':   scenario_id,
        'name':          sc['name'],
        'results':       results_dict,
        'value_results': value_results_dict,
        'consolidation': consolidation,
    })


@app.route('/api/scenarios/<scenario_id>', methods=['DELETE'])
def delete_scenario(scenario_id):
    if scenario_id not in scenarios:
        return jsonify({'error': 'Scenario not found'}), 404
    if scenarios[scenario_id]['session_id'] != active_session_id:
        return jsonify({'error': 'Scenario belongs to a different session'}), 403
    del scenarios[scenario_id]
    return jsonify({'success': True})


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
            'custom_name':    sess.get('custom_name'),
            'site':           site,
            'planning_month': pm,
            'uploaded_at':    sess.get('uploaded_at', ''),
            'calculated':     sess.get('engine') is not None,
            'active':         sid == active_session_id,
            'metadata':       meta,
        })
    return jsonify({'active_session_id': active_session_id, 'groups': grouped})


@app.route('/api/sessions/rename', methods=['POST'])
def rename_session():
    data = request.get_json() or {}
    session_id = data.get('session_id', '')
    new_name = data.get('name', '').strip()
    if not session_id or session_id not in sessions:
        return jsonify({'error': 'Session not found'}), 404
    if not new_name:
        return jsonify({'error': 'Name cannot be empty'}), 400
    sessions[session_id]['custom_name'] = new_name
    _save_sessions_to_disk()
    return jsonify({'success': True, 'session_id': session_id, 'custom_name': new_name})


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
        'custom_name': sess.get('custom_name'),
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
    _save_sessions_to_disk()
    return jsonify({'success': True, 'active_session_id': active_session_id})


_SESSION_SAVE_PATHS = {
    '/api/sessions/rename',
    '/api/sessions/switch',
    '/api/upload',
    '/api/calculate',
    '/api/update_volume',
    '/api/undo',
    '/api/redo',
    '/api/reset_edits',
    '/api/scenarios/save',
    '/api/scenarios/load',
}

_SESSION_SAVE_METHODS = {'POST', 'DELETE'}


@app.after_request
def _after_request_save(response):
    """Auto-save sessions to disk after any mutating request."""
    if request.method in _SESSION_SAVE_METHODS and (
        request.path in _SESSION_SAVE_PATHS
        or (request.method == 'DELETE' and request.path.startswith('/api/sessions/'))
        or (request.method == 'DELETE' and request.path.startswith('/api/scenarios/'))
    ):
        if response.status_code < 500:
            _save_sessions_to_disk()
    return response


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
