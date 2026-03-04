"""S&OP Planning Engine - Flask Web UI"""

from flask import Flask, render_template, request, jsonify, send_file
import pandas as pd
from pathlib import Path
from datetime import datetime
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.planning_engine import PlanningEngine

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
    current_engine.to_excel(str(export_path))
    
    return send_file(str(export_path), as_attachment=True)


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
