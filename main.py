#!/usr/bin/env python3
"""
S&OP Planning Engine - Main Entry Point

This application replicates Excel VBA macros in Python.
It reads raw input sheets and calculates all 14 planning line types.

Usage:
    python main.py              # Start web server
    python main.py --cli FILE   # Run calculations from command line
    python main.py --test       # Run test with validation
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def run_cli(file_path: str, output_path: str = None):
    """Run calculations in CLI mode."""
    from modules.planning_engine import PlanningEngine
    
    engine = PlanningEngine(file_path)
    engine.run()
    
    if output_path:
        engine.to_excel(output_path)
    else:
        # Default output
        output_path = str(Path(file_path).stem) + '_Python_Results.xlsx'
        engine.to_excel(output_path)
    
    return engine


def run_web():
    """Run Flask web server."""
    from ui.app import app
    
    print("\n" + "=" * 60)
    print("S&OP Planning Engine - Web Server")
    print("=" * 60)
    print("Open http://localhost:5000 in your browser")
    print("=" * 60 + "\n")
    
    app.run(debug=True, host='0.0.0.0', port=5000)


def run_test():
    """Run test with sample file."""
    test_file = "/mnt/user-data/uploads/03_2025_December_SOP_consolidation_MS_RECONC.xlsm"
    
    if not Path(test_file).exists():
        print("Test file not found")
        return
    
    print("Running test...")
    engine = run_cli(test_file, "/mnt/user-data/outputs/SOP_Python_Calculated.xlsx")
    
    # Validate
    print("\n" + "=" * 60)
    print("VALIDATION")
    print("=" * 60)
    
    line_types = list(engine.results.keys())
    print(f"Line types generated: {len(line_types)}")
    
    assert len(line_types) >= 12, f"Expected at least 12 line types, got {len(line_types)}"
    print("✓ Line type count validation passed")
    
    total_rows = sum(len(rows) for rows in engine.results.values())
    print(f"Total rows: {total_rows}")
    
    print("\n✓ All tests passed!")


def main():
    parser = argparse.ArgumentParser(description='S&OP Planning Engine')
    parser.add_argument('--cli', type=str, help='Run in CLI mode with Excel file')
    parser.add_argument('--output', '-o', type=str, help='Output Excel file path')
    parser.add_argument('--test', action='store_true', help='Run test')
    parser.add_argument('--web', action='store_true', help='Start web server')
    
    args = parser.parse_args()
    
    if args.test:
        run_test()
    elif args.cli:
        run_cli(args.cli, args.output)
    else:
        run_web()


if __name__ == '__main__':
    main()
