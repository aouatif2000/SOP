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
from modules.cycle_manager import CycleManager
from modules.mom_comparison_engine import MoMComparisonEngine
from modules.database_exporter import DatabaseExporter

app = Flask(__name__, 
            template_folder=str(Path(__file__).parent / 'templates'),
            static_folder=str(Path(__file__).parent / 'static'))

import uuid as _uuid

sessions: dict = {}           # session_id -> session dict
active_session_id: str = None  # currently selected session
scenarios: dict = {}          # scenario_id -> scenario snapshot

# Shared CycleManager — stores previous-cycle snapshots in the exports/ folder
_CYCLE_STORAGE_DIR = Path(__file__).parent.parent / 'exports'
_cycle_manager = CycleManager(str(_CYCLE_STORAGE_DIR))

# Line types that users are permitted to edit directly.
# Computed lines (03, 04, 07-12) are intentionally excluded.
EDITABLE_LINE_TYPES = {
    '01. Demand forecast',
    '05. Minimum target stock',
    '06. Production plan',
    '06. Purchase receipt',
}

SESSIONS_STORE = Path(__file__).parent.parent / 'sessions_store.json'


def _save_sessions_to_disk():
    """Persist session metadata (no engine objects) to sessions_store.json."""
    try:
        serializable = {}
        for sid, sess in sessions.items():
            serializable[sid] = {
                'id':           sess.get('id', sid),
                'file_path':    sess.get('file_path', ''),
                'extract_files': sess.get('extract_files'),
                'filename':     sess.get('filename', ''),
                'custom_name':  sess.get('custom_name'),
                'metadata':     sess.get('metadata', {}),
                'uploaded_at':  sess.get('uploaded_at', ''),
                'parameters':   sess.get('parameters'),
                'pending_edits': sess.get('pending_edits', {}),
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
                'id':            data.get('id', sid),
                'file_path':     data.get('file_path', ''),
                'extract_files': data.get('extract_files'),
                'filename':      data.get('filename', ''),
                'custom_name':   data.get('custom_name'),
                'engine':        None,   # must re-calculate after restart
                'value_results': {},
                'metadata':      data.get('metadata', {}),
                'uploaded_at':   data.get('uploaded_at', ''),
                'parameters':    data.get('parameters'),
                'pending_edits': data.get('pending_edits', {}),
                'undo_stack':    [],
                'redo_stack':    [],
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


def _replay_pending_edits(sess, engine):
    """Re-apply saved pending_edits onto a freshly-run engine.

    pending_edits keys are "line_type||material_number||aux_column||period".
    Only value rows need to be updated here (no cascade); value planning is
    re-calculated once after all edits are applied.
    """
    pending = sess.get('pending_edits', {})
    if not pending:
        return
    applied = 0
    for key, edit in pending.items():
        try:
            parts = key.split('||')
            if len(parts) != 4:
                continue
            lt, mat, _aux, period = parts
            new_val = float(edit.get('new_value', 0))
            original = float(edit.get('original', 0))
            rows = engine.results.get(lt, [])
            target = next((r for r in rows if r.material_number == mat), None)
            if target is None:
                continue
            target.manual_edits[period] = {'original': original, 'new': new_val}
            target.set_value(period, new_val)
            applied += 1
        except Exception:
            pass
    if applied:
        from modules.value_planning_engine import ValuePlanningEngine
        engine.value_engine = ValuePlanningEngine(engine.data, engine.results)
        engine.value_results = engine.value_engine.calculate()
        print(f'[autorun]   replayed {applied} pending edit(s)')


def _autorun_sessions():
    """Background thread: re-execute the planning engine for every session that
    was previously run (identified by a non-None 'parameters' field).

    Runs silently and does not block server startup.
    """
    import threading

    def _worker():
        candidates = [
            (sid, sess) for sid, sess in sessions.items()
            if sess.get('parameters') is not None and (
                sess.get('extract_files') or Path(sess.get('file_path', '')).exists()
            )
        ]
        if not candidates:
            print('[autorun] No sessions to restore.')
            return
        print(f'[autorun] Restoring {len(candidates)} session(s) in the background…')
        for sid, sess in candidates:
            label = sess.get('custom_name') or sess.get('filename', sid)
            try:
                params = sess['parameters']
                planning_month  = params.get('planning_month')
                months_actuals  = int(params.get('months_actuals', 0) or 0)
                months_forecast = int(params.get('months_forecast', 12) or 12)
                print(f'[autorun] Running "{label}" '
                      f'(pm={planning_month}, act={months_actuals}, fc={months_forecast})…')
                engine = PlanningEngine(
                    sess['file_path'],
                    planning_month=planning_month,
                    months_actuals=months_actuals,
                    months_forecast=months_forecast,
                    extract_files=sess.get('extract_files'),
                )
                engine.run()
                _replay_pending_edits(sess, engine)
                sess['engine'] = engine
                print(f'[autorun] ✓ "{label}" restored '
                      f'({sum(len(v) for v in engine.results.values())} rows)')
            except Exception as exc:
                print(f'[autorun] ✗ "{label}" failed: {exc}')

    t = threading.Thread(target=_worker, name='autorun-sessions', daemon=True)
    t.start()


_autorun_sessions()


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

    upload_dir = Path(__file__).parent.parent / 'uploads'
    upload_dir.mkdir(exist_ok=True)

    # Detect multi-file mode vs single-file mode
    extract_keys = ['bom_file', 'routing_file', 'stock_file', 'forecast_file']
    is_multi = all(k in request.files for k in extract_keys)

    if is_multi:
        # --- Multi-file upload mode ---
        saved_paths = {}
        key_map = {'bom_file': 'bom', 'routing_file': 'routing',
                    'stock_file': 'stock', 'forecast_file': 'forecast'}
        for form_key, dict_key in key_map.items():
            f = request.files[form_key]
            if f.filename == '':
                return jsonify({'error': f'No file selected for {form_key}'}), 400
            fp = upload_dir / f.filename
            f.save(str(fp))
            saved_paths[dict_key] = str(fp)

        try:
            from modules.data_loader import DataLoader
            loader = DataLoader(extract_files=saved_paths)
            loader.load_all()

            site = getattr(loader.config, 'site', '') or ''
            _idate = getattr(loader.config, 'initial_date', None)
            planning_month = _idate.strftime('%Y-%m') if _idate else ''
            months_actuals = getattr(loader, 'forecast_actuals_months', 12)
            months_forecast = getattr(loader.config, 'forecast_months', 12)

            bom_filename = request.files['bom_file'].filename
            session_id = str(_uuid.uuid4())
            sessions[session_id] = {
                'id': session_id,
                'file_path': '',
                'extract_files': saved_paths,
                'filename': bom_filename,
                'custom_name': None,
                'engine': None,
                'value_results': {},
                'metadata': {
                    'materials': len(loader.materials),
                    'bom_items': len(loader.bom),
                    'machines': len(loader.machines),
                    'periods': len(loader.periods),
                    'site': site,
                    'planning_month': planning_month,
                },
                'uploaded_at': datetime.now().isoformat(),
            }
            sessions[session_id]['undo_stack'] = []
            sessions[session_id]['redo_stack'] = []
            sessions[session_id]['pending_edits'] = {}
            active_session_id = session_id
            _save_sessions_to_disk()

            return jsonify({
                'success': True,
                'session_id': session_id,
                'filename': bom_filename,
                'planning_month': planning_month,
                'months_actuals': months_actuals,
                'months_forecast': months_forecast,
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

    # --- Single-file upload mode (existing behavior) ---
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    file_path = upload_dir / file.filename
    file.save(str(file_path))

    try:
        from modules.data_loader import DataLoader
        loader = DataLoader(str(file_path))
        loader.load_all()

        site = getattr(loader.config, 'site', '') or ''
        _idate = getattr(loader.config, 'initial_date', None)
        planning_month = _idate.strftime('%Y-%m') if _idate else ''
        months_actuals = getattr(loader, 'forecast_actuals_months', 12)
        months_forecast = getattr(loader.config, 'forecast_months', 12)

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
                'planning_month': planning_month,
            },
            'uploaded_at': datetime.now().isoformat(),
        }
        sessions[session_id]['undo_stack'] = []
        sessions[session_id]['redo_stack'] = []
        sessions[session_id]['pending_edits'] = {}
        active_session_id = session_id
        _save_sessions_to_disk()

        return jsonify({
            'success': True,
            'session_id': session_id,
            'filename': file.filename,
            'planning_month': planning_month,
            'months_actuals': months_actuals,
            'months_forecast': months_forecast,
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

        # --- MoM: preserve the previous results BEFORE running the new calculation ---
        # If the active session already has an engine, save its results as the
        # "previous cycle" snapshot so MoM compares new vs. old (not new vs. new).
        # If there is no previous snapshot at all (very first calculation ever),
        # bootstrap one after running so the user only needs one calculation to
        # enable MoM on the next run.
        _existing_engine = sess.get('engine')
        _bootstrap_snapshot = (_existing_engine is None and not _cycle_manager.has_previous_cycle())
        if _existing_engine is not None:
            try:
                _cycle_manager.save_current_as_previous(_existing_engine.to_dataframe())
                print('[cycle_manager] pre-run: saved existing engine as previous cycle snapshot')
            except Exception as _cm_exc:
                print(f'[cycle_manager] pre-run snapshot warning: {_cm_exc}')

        engine = PlanningEngine(
            sess['file_path'],
            planning_month=planning_month,
            months_actuals=months_actuals,
            months_forecast=months_forecast,
            extract_files=sess.get('extract_files'),
        )
        engine.run()
        sess['engine'] = engine
        sess['parameters'] = {
            'planning_month':  planning_month,
            'months_actuals':  months_actuals,
            'months_forecast': months_forecast,
        }

        # --- MoM bootstrap: on the very first calculation ever, seed the snapshot ---
        # (so MoM becomes available after the second calculation without needing
        # to calculate twice in the same app session)
        if _bootstrap_snapshot:
            try:
                _cycle_manager.save_current_as_previous(engine.to_dataframe())
                print('[cycle_manager] bootstrap: saved first-ever snapshot')
            except Exception as _cm_exc:
                print(f'[cycle_manager] bootstrap snapshot warning: {_cm_exc}')

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
        # ROCE is a ratio (e.g. 0.042 = 4.2%) — preserve precision so the
        # frontend can multiply by 100.  All other P&L rows are large EUR
        # amounts that round cleanly to 0 decimal places.
        decimals = 6 if key == 'ROCE' else 0
        financials[key] = {p: round(v, decimals) for p, v in row.values.items()}

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

    # --- Load previous cycle for MoM comparison sheet ---
    _prev_df = None
    try:
        if _cycle_manager.has_previous_cycle():
            _prev_df = _cycle_manager.load_previous_cycle()
            if _prev_df.empty:
                _prev_df = None
                print('[export] Previous cycle loaded but empty — MoM sheet skipped')
            else:
                print(f'[export] Previous cycle loaded ({len(_prev_df)} rows) — MoM sheet will be included')
        else:
            print('[export] No previous cycle on disk — MoM sheet skipped (will be available after next calculation)')
    except Exception as _prev_exc:
        print(f'[export] Could not load previous cycle: {_prev_exc}')

    current_engine.to_excel_with_values(
        str(export_path),
        inventory_quality_engine=_iq_engine_export,
        previous_cycle_df=_prev_df,
    )

    # Apply edit highlights and summary sheet if there are any edits
    _apply_edit_highlights(str(export_path), current_engine)

    return send_file(str(export_path), as_attachment=True)


@app.route('/api/export_db', methods=['POST'])
def export_db():
    """Export planning results to a flat DB-ready Excel file via DatabaseExporter."""
    _, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400

    try:
        planning_df = current_engine.to_dataframe()
        site = getattr(current_engine.data.config, 'site', 'NLX1')
        initial_date = current_engine.data.config.initial_date

        exporter = DatabaseExporter(planning_df, site, initial_date)
        db_df = exporter.export_to_dataframe()

        if db_df.empty:
            return jsonify({'error': 'No data to export (no matching line types)'}), 400

        export_dir = Path(__file__).parent.parent / 'exports'
        export_dir.mkdir(exist_ok=True)

        # Allow caller to override filename via JSON body
        req_data = request.get_json(silent=True) or {}
        filename = req_data.get('filename', '').strip()
        if not filename:
            filename = f'SOP_DB_Export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        # Sanitise — keep only safe characters
        safe_name = ''.join(c for c in filename if c.isalnum() or c in '._- ')
        if not safe_name.endswith('.xlsx'):
            safe_name += '.xlsx'

        export_path = export_dir / safe_name
        db_df.to_excel(str(export_path), index=False)
        print(f'[export_db] {len(db_df)} rows written → {export_path}')

        return send_file(str(export_path), as_attachment=True, download_name=safe_name)
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500


@app.route('/api/mom')
def get_mom_comparison():
    """Return MoM comparison data as JSON for the dashboard."""
    _, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400

    if not _cycle_manager.has_previous_cycle():
        return jsonify({
            'available': False,
            'message': 'No previous cycle available — run calculations at least twice',
        })

    try:
        previous_df = _cycle_manager.load_previous_cycle()
        if previous_df.empty:
            return jsonify({'available': False, 'message': 'Previous cycle snapshot is empty'})

        current_df = current_engine.to_dataframe()
        mom_engine = MoMComparisonEngine(current_df, previous_df)
        comparison = mom_engine.calculate()
        scatter = mom_engine.create_scatter_data()

        if comparison.empty:
            return jsonify({
                'available': False,
                'message': 'No overlapping inventory data between cycles',
            })

        return jsonify({
            'available': True,
            'comparison': comparison.to_dict(orient='records'),
            'scatter': scatter,
            'row_count': len(comparison),
            'material_count': len(scatter.get('materials', [])),
        })
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500


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


@app.route('/api/editable_line_types')
def get_editable_line_types():
    return jsonify({'editable': sorted(EDITABLE_LINE_TYPES)})


@app.route('/api/sessions/edits/persist', methods=['POST'])
def persist_session_edit():
    """Save a single cell edit into the session's persistent pending_edits store."""
    req = request.get_json() or {}
    session_id = req.get('session_id', '')
    if not session_id or session_id not in sessions:
        return jsonify({'error': 'Session not found'}), 404
    key = req.get('key', '').strip()
    if not key:
        return jsonify({'error': 'Cell key required'}), 400
    original = float(req.get('original', 0))
    new_value = float(req.get('new_value', 0))
    pending = sessions[session_id].setdefault('pending_edits', {})
    if abs(new_value - original) < 0.0001:
        pending.pop(key, None)   # edit reverted to original — remove entry
    else:
        pending[key] = {'original': original, 'new_value': new_value}
    _save_sessions_to_disk()
    return jsonify({'success': True})


@app.route('/api/sessions/edits/sync', methods=['POST'])
def sync_session_edits():
    """Replace the entire pending_edits store for a session (used after undo/redo/reset/import)."""
    req = request.get_json() or {}
    session_id = req.get('session_id', '')
    if not session_id or session_id not in sessions:
        return jsonify({'error': 'Session not found'}), 404
    edits = req.get('edits', {})
    if not isinstance(edits, dict):
        return jsonify({'error': 'edits must be an object'}), 400
    sessions[session_id]['pending_edits'] = {
        k: {'original': float(v.get('original', 0)), 'new_value': float(v.get('new_value', 0))}
        for k, v in edits.items()
        if isinstance(v, dict)
    }
    _save_sessions_to_disk()
    return jsonify({'success': True})


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
    if line_type not in EDITABLE_LINE_TYPES:
        return jsonify({'error': f'Line type "{line_type}" is not editable'}), 403
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
        _all_line_data = {lt: {r.material_number: r.values for r in rows} for lt, rows in current_engine.results.items() if rows}
        cap_eng = CapacityEngine(current_engine.data, current_engine.all_production_plans, _all_line_data)
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
        _all_line_data = {lt: {r.material_number: r.values for r in rows} for lt, rows in current_engine.results.items() if rows}
        cap_eng = CapacityEngine(current_engine.data, current_engine.all_production_plans, _all_line_data)
        cap_results = cap_eng.calculate()
        for lt, cap_rows in cap_results.items():
            current_engine.results[lt] = cap_rows
        current_engine.value_engine = ValuePlanningEngine(current_engine.data, current_engine.results)
        current_engine.value_results = current_engine.value_engine.calculate()

    elif line_type in (LineType.PRODUCTION_PLAN.value, LineType.PURCHASE_RECEIPT.value):
        from modules.bom_engine import BOMEngine
        from modules.capacity_engine import CapacityEngine
        from modules.value_planning_engine import ValuePlanningEngine

        periods_list = current_engine.data.periods

        # Gather current rows (target_row already has new_value applied)
        prod_row  = next((r for r in current_engine.results.get(LineType.PRODUCTION_PLAN.value, [])
                          if r.material_number == material_number), None)
        purch_row = next((r for r in current_engine.results.get(LineType.PURCHASE_RECEIPT.value, [])
                          if r.material_number == material_number), None)
        l03_row   = next((r for r in current_engine.results.get(LineType.TOTAL_DEMAND.value, [])
                          if r.material_number == material_number), None)

        # Directly recompute L04 inventory balance from updated L06 + L07 + L03 + initial stock.
        # We do NOT call calculate_for_material here because it would recalculate L06 from
        # demand/target-stock rules, silently overwriting the user's manual edit.
        inv_row = next((r for r in current_engine.results.get(LineType.INVENTORY.value, [])
                        if r.material_number == material_number), None)
        if inv_row:
            initial_stock = current_engine.data.stock_levels.get(material_number, 0.0)
            running = initial_stock
            for p in periods_list:
                demand = l03_row.values.get(p, 0.0)  if l03_row  else 0.0
                prod   = prod_row.values.get(p, 0.0)  if prod_row  else 0.0
                purch  = purch_row.values.get(p, 0.0) if purch_row else 0.0
                running = running - demand + prod + purch
                inv_row.values[p] = running

        # Update in-memory production/purchase tracking dicts
        if prod_row is not None:
            current_engine.all_production_plans[material_number] = dict(prod_row.values)
        if purch_row is not None:
            current_engine.all_purchase_receipts[material_number] = dict(purch_row.values)

        # Rebuild L08 dependent requirements from the (now updated) production plan
        bom_eng = BOMEngine(current_engine.data)
        current_engine.results[LineType.DEPENDENT_REQUIREMENTS.value] = [
            r for r in current_engine.results.get(LineType.DEPENDENT_REQUIREMENTS.value, [])
            if r.material_number != material_number
        ]
        children_demand = {}
        if prod_row is not None:
            children_demand = bom_eng.compute_dependent_requirements(
                material_number, dict(prod_row.values)
            )
            if children_demand:
                dr_rows = bom_eng.create_dependent_requirements_rows(material_number, children_demand)
                current_engine.results[LineType.DEPENDENT_REQUIREMENTS.value].extend(dr_rows)

        # Propagate to child materials: update their L02 dependent demand and L03 total demand
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
                    fc_val  = child_l01_row.values.get(p, 0.0) if child_l01_row else 0.0
                    dep_val = sum(r.values.get(p, 0.0) for r in child_all_l02)
                    child_l03_row.values[p] = fc_val + dep_val

        # Re-run capacity engine (L09 machine requirements, L10 utilization, L11 FTE, L12)
        _all_line_data = {lt: {r.material_number: r.values for r in rows} for lt, rows in current_engine.results.items() if rows}
        cap_eng = CapacityEngine(current_engine.data, current_engine.all_production_plans, _all_line_data)
        cap_results = cap_eng.calculate()
        for lt, cap_rows in cap_results.items():
            current_engine.results[lt] = cap_rows

        # Re-run value planning
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
    _all_line_data = {lt: {r.material_number: r.values for r in rows} for lt, rows in current_engine.results.items() if rows}
    cap_eng = CapacityEngine(current_engine.data, current_engine.all_production_plans, _all_line_data)
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


# ---- Prod/Purch Split endpoints ----

def _recalc_one_material(current_engine, mat, inv_eng, bom_eng, periods_list, override_forecast=False):
    """Recalculate inventory + BOM for one material. Updates results in-place.
    Returns {child_mat: child_period_demand} so the caller can cascade further."""
    fc_row = next(
        (r for r in current_engine.results.get(LineType.DEMAND_FORECAST.value, [])
         if r.material_number == mat), None
    )
    forecast_vals = dict(fc_row.values) if fc_row else {p: 0.0 for p in periods_list}

    mat_l02 = [r for r in current_engine.results.get(LineType.DEPENDENT_DEMAND.value, [])
               if r.material_number == mat]
    dep_demand_agg = {p: 0.0 for p in periods_list}
    dep_demand_by_parent = {}
    for r in mat_l02:
        parent = r.aux_column
        if parent:
            dep_demand_by_parent[parent] = dict(r.values)
            for p in periods_list:
                dep_demand_agg[p] = dep_demand_agg.get(p, 0.0) + r.values.get(p, 0.0)

    l05_row = next(
        (r for r in current_engine.results.get(LineType.MIN_TARGET_STOCK.value, [])
         if r.material_number == mat), None
    )
    l05_saved_values = dict(l05_row.values) if l05_row else {}
    l05_saved_edits = dict(l05_row.manual_edits) if l05_row else {}

    kwargs = {'override_forecast': forecast_vals} if override_forecast else {}
    inv_result = inv_eng.calculate_for_material(
        mat, forecast_vals, dep_demand_agg, dep_demand_by_parent, **kwargs
    )

    inv_line_types = [
        LineType.TOTAL_DEMAND.value, LineType.INVENTORY.value,
        LineType.MIN_TARGET_STOCK.value, LineType.PRODUCTION_PLAN.value,
        LineType.PURCHASE_RECEIPT.value, LineType.PURCHASE_PLAN.value,
    ]
    for lt in inv_line_types:
        current_engine.results[lt] = [
            r for r in current_engine.results.get(lt, []) if r.material_number != mat
        ]
    for row in inv_result['rows']:
        if row.line_type in current_engine.results:
            current_engine.results[row.line_type].append(row)

    new_l05 = next(
        (r for r in current_engine.results.get(LineType.MIN_TARGET_STOCK.value, [])
         if r.material_number == mat), None
    )
    if new_l05:
        new_l05.values = l05_saved_values
        new_l05.manual_edits = l05_saved_edits

    if inv_result['production_plan'] is not None:
        current_engine.all_production_plans[mat] = inv_result['production_plan']
    else:
        current_engine.all_production_plans.pop(mat, None)
    if inv_result['purchase_receipt'] is not None:
        current_engine.all_purchase_receipts[mat] = inv_result['purchase_receipt']
    else:
        current_engine.all_purchase_receipts.pop(mat, None)

    # Compute dependent requirements and push updated L02/L03 to children
    current_engine.results[LineType.DEPENDENT_REQUIREMENTS.value] = [
        r for r in current_engine.results.get(LineType.DEPENDENT_REQUIREMENTS.value, [])
        if r.material_number != mat
    ]
    children_demand = {}
    if inv_result['production_plan'] is not None:
        children_demand = bom_eng.compute_dependent_requirements(mat, inv_result['production_plan'])
        if children_demand:
            dr_rows = bom_eng.create_dependent_requirements_rows(mat, children_demand)
            current_engine.results[LineType.DEPENDENT_REQUIREMENTS.value].extend(dr_rows)

    for child_mat, child_period_demand in children_demand.items():
        current_engine.results[LineType.DEPENDENT_DEMAND.value] = [
            r for r in current_engine.results.get(LineType.DEPENDENT_DEMAND.value, [])
            if not (r.material_number == child_mat and r.aux_column == mat)
        ]
        child_l02_new = bom_eng.create_dependent_demand_rows(
            child_mat, {mat: child_period_demand}
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

    return children_demand


def _recalc_pap_material(current_engine, material_number):
    """Re-run inventory + full BOM cascade for a PAP material change.
    Uses BFS so every child (and grandchild, etc.) gets its inventory recalculated
    after its dependent demand is updated — not just L02/L03."""
    from modules.inventory_engine import InventoryEngine
    from modules.bom_engine import BOMEngine

    inv_eng = InventoryEngine(current_engine.data)
    bom_eng = BOMEngine(current_engine.data)
    periods_list = current_engine.data.periods

    # Recalculate the PAP material itself (override_forecast keeps the PAP split intact)
    children_demand = _recalc_one_material(
        current_engine, material_number, inv_eng, bom_eng, periods_list,
        override_forecast=True,
    )

    # BFS: recalculate every affected child's inventory so the cascade is complete
    queue = list(children_demand.keys())
    visited = {material_number}
    while queue:
        child_mat = queue.pop(0)
        if child_mat in visited:
            continue
        visited.add(child_mat)
        grandchildren_demand = _recalc_one_material(
            current_engine, child_mat, inv_eng, bom_eng, periods_list,
            override_forecast=False,
        )
        queue.extend(gc for gc in grandchildren_demand if gc not in visited)


def _finish_pap_recalc(current_engine):
    """Run capacity + value engines after a PAP fraction change."""
    from modules.capacity_engine import CapacityEngine
    from modules.value_planning_engine import ValuePlanningEngine

    _all_line_data = {lt: {r.material_number: r.values for r in rows} for lt, rows in current_engine.results.items() if rows}
    cap_eng = CapacityEngine(current_engine.data, current_engine.all_production_plans, _all_line_data)
    cap_results = cap_eng.calculate()
    for lt, cap_rows in cap_results.items():
        current_engine.results[lt] = cap_rows
    current_engine.value_engine = ValuePlanningEngine(current_engine.data, current_engine.results)
    current_engine.value_results = current_engine.value_engine.calculate()


@app.route('/api/pap', methods=['GET'])
def get_pap():
    _, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400
    return jsonify({'pap': dict(current_engine.data.purchased_and_produced)})


@app.route('/api/pap', methods=['POST'])
def set_pap():
    _, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400
    req = request.get_json() or {}
    mat = req.get('material_number', '').strip()
    fraction = req.get('fraction')
    if not mat:
        return jsonify({'error': 'material_number is required'}), 400
    try:
        fraction = float(fraction)
    except (TypeError, ValueError):
        return jsonify({'error': 'fraction must be a number'}), 400
    current_engine.data.purchased_and_produced[mat] = fraction
    _recalc_pap_material(current_engine, mat)
    _finish_pap_recalc(current_engine)
    results_dict = {lt: [r.to_dict() for r in rs] for lt, rs in current_engine.results.items()}
    value_results_dict = {lt: [r.to_dict() for r in rs] for lt, rs in current_engine.value_results.items()}
    consolidation = [r.to_dict() for r in current_engine.value_results.get(LineType.CONSOLIDATION.value, [])]
    return jsonify({'success': True, 'results': results_dict, 'value_results': value_results_dict,
                    'consolidation': consolidation})


@app.route('/api/pap/<material_number>', methods=['DELETE'])
def delete_pap(material_number):
    _, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400
    current_engine.data.purchased_and_produced.pop(material_number, None)
    _recalc_pap_material(current_engine, material_number)
    _finish_pap_recalc(current_engine)
    results_dict = {lt: [r.to_dict() for r in rs] for lt, rs in current_engine.results.items()}
    value_results_dict = {lt: [r.to_dict() for r in rs] for lt, rs in current_engine.value_results.items()}
    consolidation = [r.to_dict() for r in current_engine.value_results.get(LineType.CONSOLIDATION.value, [])]
    return jsonify({'success': True, 'results': results_dict, 'value_results': value_results_dict,
                    'consolidation': consolidation})


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


@app.route('/api/scenarios/compare', methods=['POST'])
def compare_scenarios():
    req = request.get_json() or {}
    id_a = req.get('scenario_a_id', '')
    id_b = req.get('scenario_b_id', '')
    if id_a not in scenarios or id_b not in scenarios:
        return jsonify({'error': 'Scenario not found'}), 404
    sc_a = scenarios[id_a]
    sc_b = scenarios[id_b]
    if sc_a['session_id'] != active_session_id or sc_b['session_id'] != active_session_id:
        return jsonify({'error': 'Scenarios belong to a different session'}), 403

    diff_rows = []
    for lt, rows_a in sc_a['results'].items():
        rows_b_map = {r['material_number']: r for r in sc_b['results'].get(lt, [])}
        for row_a in rows_a:
            mat = row_a['material_number']
            row_b = rows_b_map.get(mat)
            if not row_b:
                continue
            diff = {p: round(row_a['values'].get(p, 0) - row_b['values'].get(p, 0), 4)
                    for p in row_a['values']}
            if any(abs(v) > 0.01 for v in diff.values()):
                diff_rows.append({
                    'material_number': mat,
                    'line_type': lt,
                    'values_a': row_a['values'],
                    'values_b': row_b['values'],
                    'diff': diff,
                })

    def _sum_diff(lt_key):
        rows = [r for r in diff_rows if r['line_type'] == lt_key]
        first = sc_a['results'].get(lt_key, [])
        periods = list(first[0].get('values', {}).keys()) if first else []
        return {p: round(sum(r['diff'].get(p, 0) for r in rows), 2) for p in periods}

    summary = {
        'scenario_a_name': sc_a['name'],
        'scenario_b_name': sc_b['name'],
        'total_demand_diff': _sum_diff('03. Total demand'),
        'inventory_diff':    _sum_diff('04. Inventory'),
        'changed_rows':      len(diff_rows),
    }
    return jsonify({'summary': summary, 'rows': diff_rows})


@app.route('/api/scenarios/compare/export')
def export_scenario_comparison():
    id_a = request.args.get('a', '')
    id_b = request.args.get('b', '')
    if id_a not in scenarios or id_b not in scenarios:
        return jsonify({'error': 'Scenario not found'}), 404
    sc_a = scenarios[id_a]
    sc_b = scenarios[id_b]
    if sc_a['session_id'] != active_session_id or sc_b['session_id'] != active_session_id:
        return jsonify({'error': 'Scenarios belong to a different session'}), 403

    # ── Reuse compare logic ─────────────────────────────────────────────────
    def _build_diff_rows(res_a, res_b):
        rows = []
        for lt, rows_a in res_a.items():
            rows_b_map = {r['material_number']: r for r in res_b.get(lt, [])}
            for row_a in rows_a:
                mat = row_a['material_number']
                row_b = rows_b_map.get(mat)
                if not row_b:
                    continue
                diff = {p: round(row_a['values'].get(p, 0) - row_b['values'].get(p, 0), 4)
                        for p in row_a['values']}
                if any(abs(v) > 0.01 for v in diff.values()):
                    rows.append({
                        'material_number': mat,
                        'material_name':   row_a.get('material_name', ''),
                        'line_type':       lt,
                        'values_a':        row_a['values'],
                        'values_b':        row_b['values'],
                        'diff':            diff,
                    })
        return rows

    import openpyxl
    from openpyxl.styles import PatternFill, Font, Alignment
    from openpyxl.utils import get_column_letter

    GREEN_FILL = PatternFill(start_color='C6EFCE', end_color='C6EFCE', fill_type='solid')
    RED_FILL   = PatternFill(start_color='FFC7CE', end_color='FFC7CE', fill_type='solid')
    GREY_FILL  = PatternFill(start_color='D9D9D9', end_color='D9D9D9', fill_type='solid')
    HDR_FILL   = PatternFill(start_color='1F3864', end_color='1F3864', fill_type='solid')
    ROW_A_FILL = PatternFill(start_color='EBF3FB', end_color='EBF3FB', fill_type='solid')
    ROW_B_FILL = PatternFill(start_color='FEF9EE', end_color='FEF9EE', fill_type='solid')
    HDR_FONT   = Font(bold=True, color='FFFFFF')
    BOLD_FONT  = Font(bold=True)

    def _write_sheet(ws, diff_rows):
        if not diff_rows:
            ws.append(['No differences found.'])
            return
        periods = list(diff_rows[0]['values_a'].keys())
        # Header row
        headers = ['Material Number', 'Material Name', 'Line Type', 'Row'] + periods + ['Total Diff']
        ws.append(headers)
        for cell in ws[1]:
            cell.fill = HDR_FILL
            cell.font = HDR_FONT
            cell.alignment = Alignment(horizontal='center')

        for dr in diff_rows:
            mat  = dr['material_number']
            name = dr['material_name']
            lt   = dr['line_type']
            va   = dr['values_a']
            vb   = dr['values_b']
            dv   = dr['diff']
            # Row A (Scenario A values)
            row_a_data = [mat, name, lt, sc_a['name']] + [va.get(p, 0) for p in periods] + [round(sum(va.get(p,0) for p in periods), 2)]
            ws.append(row_a_data)
            for cell in ws[ws.max_row]:
                cell.fill = ROW_A_FILL
            # Row B (Scenario B values)
            row_b_data = [mat, name, lt, sc_b['name']] + [vb.get(p, 0) for p in periods] + [round(sum(vb.get(p,0) for p in periods), 2)]
            ws.append(row_b_data)
            for cell in ws[ws.max_row]:
                cell.fill = ROW_B_FILL
            # Diff row
            total_diff = round(sum(dv.get(p, 0) for p in periods), 2)
            diff_data  = [mat, name, lt, 'Diff (A−B)'] + [dv.get(p, 0) for p in periods] + [total_diff]
            ws.append(diff_data)
            diff_excel_row = ws.max_row
            period_start_col = 5  # columns 1-4 are fixed metadata
            for col_idx, p in enumerate(periods, start=period_start_col):
                cell = ws.cell(row=diff_excel_row, column=col_idx)
                cell.font = BOLD_FONT
                v = dv.get(p, 0)
                if v > 0.01:
                    cell.fill = GREEN_FILL
                elif v < -0.01:
                    cell.fill = RED_FILL
            # Total diff cell
            total_cell = ws.cell(row=diff_excel_row, column=len(headers))
            total_cell.font = BOLD_FONT
            if total_diff > 0.01:
                total_cell.fill = GREEN_FILL
            elif total_diff < -0.01:
                total_cell.fill = RED_FILL
            # Grey separator row
            ws.append([''] * len(headers))
            for cell in ws[ws.max_row]:
                cell.fill = GREY_FILL

        # Auto-width for first 4 columns
        for col_idx in range(1, 5):
            max_len = max((len(str(ws.cell(r, col_idx).value or '')) for r in range(1, ws.max_row + 1)), default=10)
            ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 2, 40)

    wb = openpyxl.Workbook()
    # Volume sheet
    ws_vol = wb.active
    ws_vol.title = 'Volume Comparison'
    vol_diff = _build_diff_rows(sc_a['results'], sc_b['results'])
    _write_sheet(ws_vol, vol_diff)

    # Value sheet
    ws_val = wb.create_sheet('Value Comparison')
    val_diff = _build_diff_rows(sc_a.get('value_results', {}), sc_b.get('value_results', {}))
    _write_sheet(ws_val, val_diff)

    export_dir = Path(__file__).parent.parent / 'exports'
    export_dir.mkdir(exist_ok=True)
    safe_a = ''.join(c for c in sc_a['name'] if c.isalnum() or c in ' _-')[:30]
    safe_b = ''.join(c for c in sc_b['name'] if c.isalnum() or c in ' _-')[:30]
    filename = f'Comparison_{safe_a}_vs_{safe_b}.xlsx'
    export_path = export_dir / filename
    wb.save(str(export_path))
    return send_file(str(export_path), as_attachment=True, download_name=filename)


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
        'pending_edits': sess.get('pending_edits', {}),
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
    '/api/sessions/edits/persist',
    '/api/sessions/edits/sync',
    '/api/upload',
    '/api/calculate',
    '/api/update_volume',
    '/api/undo',
    '/api/redo',
    '/api/reset_edits',
    '/api/scenarios/save',
    '/api/scenarios/load',
    '/api/export_db',
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
