#!/usr/bin/env python3
"""
S&OP Planning Engine - Main Entry Point

Usage:
    python main.py              # Start web server
    python main.py --cli FILE   # Run calculations from command line
    python main.py --test       # Run test with validation

User Input Parameters:
    --planning-month    Which month the planning is based on (e.g., 2025-04)
    --months-actuals    How many months of actuals are in the forecast sheet
    --months-forecast   Planning horizon in months (default 12)
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def run_cli(file_path: str, output_path: str = None, planning_month: str = None,
            months_actuals: int = 0, months_forecast: int = 12,
            export_db: bool = False, db_path: str = None):
    from modules.planning_engine import PlanningEngine
    from modules.cycle_manager import CycleManager
    from modules.mom_comparison_engine import MoMComparisonEngine
    from modules.database_exporter import DatabaseExporter

    engine = PlanningEngine(
        file_path,
        planning_month=planning_month,
        months_actuals=months_actuals,
        months_forecast=months_forecast
    )
    engine.run()

    if not output_path:
        output_path = str(Path(file_path).stem) + '_Python_Results.xlsx'

    # --- Cycle Manager: load previous, save current ---
    storage_dir = str(Path(output_path).parent or ".")
    cycle_mgr = CycleManager(storage_dir)
    previous_df = cycle_mgr.load_previous_cycle()

    current_df = engine.to_dataframe()

    # Save current results as the new "previous" for next run
    cycle_mgr.save_current_as_previous(current_df)

    # --- MoM Comparison ---
    mom_df = None
    if not previous_df.empty:
        mom_engine = MoMComparisonEngine(current_df, previous_df)
        mom_df = mom_engine.calculate()
        scatter_data = mom_engine.create_scatter_data()
        if not mom_df.empty:
            print(f"  MoM comparison: {len(mom_df)} delta rows, {len(scatter_data['materials'])} materials in scatter")
        else:
            print("  MoM comparison: no overlapping inventory data")
    else:
        print("  No previous cycle found — MoM comparison skipped (will be available next run)")

    # --- Export to Excel (with MoM sheet if available) ---
    engine.to_excel_with_values(output_path, previous_cycle_df=previous_df if not previous_df.empty else None)

    # --- Database Export ---
    if export_db:
        db_exporter = DatabaseExporter(
            current_df,
            site=engine.data.config.site,
            initial_date=engine.data.config.initial_date,
        )
        if db_path:
            db_export_df = db_exporter.export_to_dataframe()
            db_export_df.to_excel(db_path, index=False)
            print(f"  DB export written to: {db_path}")
        else:
            db_default = str(Path(output_path).stem) + '_DB_Export.xlsx'
            db_export_df = db_exporter.export_to_dataframe()
            db_export_df.to_excel(db_default, index=False)
            print(f"  DB export written to: {db_default}")

    return engine


def run_web():
    from ui.app import app

    print("\n" + "=" * 60)
    print("S&OP Planning Engine - Web Server")
    print("=" * 60)
    print("Open http://localhost:5000 in your browser")
    print("=" * 60 + "\n")

    app.run(debug=True, host='0.0.0.0', port=5000)


def run_test():
    test_file = "/mnt/user-data/uploads/03_2025_December_SOP_consolidation_MS_RECONC.xlsm"

    if not Path(test_file).exists():
        print("Test file not found")
        return

    print("Running test...")
    engine = run_cli(
        test_file,
        "/mnt/user-data/outputs/SOP_Python_Calculated.xlsx",
        planning_month="2025-12",
        months_actuals=11,
        months_forecast=12
    )

    print("\n" + "=" * 60)
    print("VALIDATION")
    print("=" * 60)

    line_types = list(engine.results.keys())
    print(f"Line types generated: {len(line_types)}")

    assert len(line_types) >= 12, f"Expected at least 12 line types, got {len(line_types)}"
    print("Line type count validation passed")

    total_rows = sum(len(rows) for rows in engine.results.values())
    print(f"Total rows: {total_rows}")

    line_01_rows = engine.results.get('01. Demand forecast', [])
    if line_01_rows:
        sample = line_01_rows[0]
        print(f"\nLine 01 Sample (first material):")
        print(f"  Material: {sample.material_number}")
        print(f"  Aux 1 (Avg Actuals): {sample.aux_column}")
        print(f"  Aux 2 (Avg Forecast): {sample.aux_2_column}")

    print("\nAll tests passed!")


def main():
    parser = argparse.ArgumentParser(description='S&OP Planning Engine')
    parser.add_argument('--cli', type=str, help='Run in CLI mode with Excel file')
    parser.add_argument('--output', '-o', type=str, help='Output Excel file path')
    parser.add_argument('--test', action='store_true', help='Run test')
    parser.add_argument('--web', action='store_true', help='Start web server')
    parser.add_argument('--planning-month', type=str, help='Planning month (e.g., 2025-04)')
    parser.add_argument('--months-actuals', type=int, default=0, help='Months of actuals in forecast')
    parser.add_argument('--months-forecast', type=int, default=12, help='Forecast horizon months')
    parser.add_argument('--export-db', action='store_true', help='Export planning results to DB-ready flat table')
    parser.add_argument('--db-path', type=str, help='Output path for DB export Excel file')

    args = parser.parse_args()

    if args.test:
        run_test()
    elif args.cli:
        run_cli(
            args.cli,
            args.output,
            planning_month=args.planning_month,
            months_actuals=args.months_actuals,
            months_forecast=args.months_forecast,
            export_db=args.export_db,
            db_path=args.db_path,
        )
    else:
        run_web()


if __name__ == '__main__':
    main()
