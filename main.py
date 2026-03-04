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
            months_actuals: int = 0, months_forecast: int = 12):
    from modules.planning_engine import PlanningEngine

    engine = PlanningEngine(
        file_path,
        planning_month=planning_month,
        months_actuals=months_actuals,
        months_forecast=months_forecast
    )
    engine.run()

    if output_path:
        engine.to_excel(output_path)
    else:
        output_path = str(Path(file_path).stem) + '_Python_Results.xlsx'
        engine.to_excel(output_path)

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

    args = parser.parse_args()

    if args.test:
        run_test()
    elif args.cli:
        run_cli(
            args.cli,
            args.output,
            planning_month=args.planning_month,
            months_actuals=args.months_actuals,
            months_forecast=args.months_forecast
        )
    else:
        run_web()


if __name__ == '__main__':
    main()
