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

current_file_path = None
current_engine = None


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/upload', methods=['POST'])
def upload_file():
    global current_file_path, current_engine
    
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    upload_dir = Path(__file__).parent.parent / 'uploads'
    upload_dir.mkdir(exist_ok=True)
    
    file_path = upload_dir / file.filename
    file.save(str(file_path))
    current_file_path = str(file_path)
    current_engine = None
    
    try:
        # Just load to get metadata - don't calculate yet
        from modules.data_loader import DataLoader
        data = DataLoader(current_file_path)
        data.load_all()
        
        return jsonify({
            'success': True,
            'filename': file.filename,
            'summary': {
                'materials': len(data.materials),
                'bom_items': len(data.bom),
                'machines': len(data.machines),
                'periods': len(data.periods),
            }
        })
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500


@app.route('/api/calculate', methods=['POST'])
def run_calculations():
    global current_file_path, current_engine
    
    if current_file_path is None:
        return jsonify({'error': 'No file uploaded'}), 400
    
    try:
        # Get user input parameters from request (handle both JSON and form data)
        if request.is_json:
            data = request.get_json() or {}
        else:
            data = request.form.to_dict() or {}
        
        planning_month = data.get('planning_month', None)
        months_actuals = int(data.get('months_actuals', 0) or 0)
        months_forecast = int(data.get('months_forecast', 12) or 12)
        
        print(f"\nUser Input Parameters:")
        print(f"  Planning Month: {planning_month}")
        print(f"  Months of Actuals: {months_actuals}")
        print(f"  Months of Forecast: {months_forecast}")
        
        current_engine = PlanningEngine(
            current_file_path,
            planning_month=planning_month,
            months_actuals=months_actuals,
            months_forecast=months_forecast
        )
        current_engine.run()
        
        return jsonify({
            'success': True,
            'summary': current_engine.get_summary(),
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
    global current_engine
    
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
    global current_engine
    
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


@app.route('/api/capacity')
def get_capacity():
    global current_engine
    
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
    global current_engine
    
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


@app.route('/api/export')
def export():
    global current_engine

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
    global current_engine

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
        if inv_result['production_plan'] is not None:
            children_demand = bom_eng.compute_dependent_requirements(
                material_number, inv_result['production_plan']
            )
            if children_demand:
                dr_rows = bom_eng.create_dependent_requirements_rows(material_number, children_demand)
                current_engine.results[LineType.DEPENDENT_REQUIREMENTS.value].extend(dr_rows)

        # Steps 6/8/9/10: Re-run capacity engine (Lines 07cap, 09, 10, 11, 12)
        cap_eng = CapacityEngine(current_engine.data, current_engine.all_production_plans)
        cap_results = cap_eng.calculate()
        for lt, cap_rows in cap_results.items():
            current_engine.results[lt] = cap_rows

        # Re-run value planning
        current_engine.value_engine = ValuePlanningEngine(current_engine.data, current_engine.results)
        current_engine.value_results = current_engine.value_engine.calculate()

    else:
        # Line 01 / Line 06: value already set above; just refresh value planning
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
    global current_engine

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
    global current_engine

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


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
