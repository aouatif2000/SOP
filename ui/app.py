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
        current_engine = PlanningEngine(current_file_path)
        current_engine.run()
        
        return jsonify({
            'success': True,
            'summary': current_engine.get_summary()
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


@app.route('/api/overstocks')
def get_overstocks():
    """Get Top 10 Overstocks by value (€) from Inventory quality sheet
    
    The Excel chart shows top 10 materials ranked by their STARTING STOCK overstock value,
    not by total overstock across all periods.
    """
    global current_engine, current_file_path
    
    if current_file_path is None:
        return jsonify({'error': 'No file loaded'}), 400
    
    try:
        import pandas as pd
        
        # Read the Inventory quality sheet
        xl = pd.ExcelFile(current_file_path)
        df = pd.read_excel(xl, sheet_name='Inventory quality')
        
        # Get overstock columns
        overstock_cols = [c for c in df.columns if 'Overstock' in str(c)]
        
        # Filter out rows with NaN material number
        df = df[df['Material number'].notna()]
        df = df[df['Material name'].notna()]
        
        # Get the Starting stock column
        starting_col = [c for c in overstock_cols if 'Starting' in str(c)][0]
        
        # Top 10 by STARTING STOCK overstock value (this matches Excel chart)
        df_positive = df[df[starting_col] > 0]
        top10_df = df_positive.nlargest(10, starting_col)
        
        # Build period labels
        periods = []
        for col in overstock_cols:
            col_str = str(col).replace('Overstock ', '')
            if 'Starting' in col_str:
                periods.append('Overstock Starting stock')
            elif hasattr(col, 'strftime'):
                periods.append('Overstock 01-' + col.strftime('%b-%y'))
            else:
                periods.append('Overstock ' + col_str)
        
        top10 = []
        for _, row in top10_df.iterrows():
            period_values = []
            for col in overstock_cols:
                val = row[col]
                period_values.append(float(val) if pd.notna(val) else 0.0)
            
            total = sum(period_values)
            
            top10.append({
                'material': str(int(row['Material number'])) if pd.notna(row['Material number']) else '',
                'name': str(row['Material name']) if pd.notna(row['Material name']) else '',
                'period_values': period_values,
                'total_value': total
            })
        
        # Calculate period totals for the chart (sum of top 10 only)
        period_totals = []
        for col in overstock_cols:
            total = top10_df[col].sum()
            period_totals.append(float(total) if pd.notna(total) else 0.0)
        
        return jsonify({
            'periods': periods,
            'top10': top10,
            'period_totals': period_totals,
            'total_materials_with_overstock': len(df_positive)
        })
        
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500


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
