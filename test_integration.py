# test_integration.py
# Integration test for S&OP Planning Application
# Created: 06/02/2026

"""
Test script to verify DataLoader and CalculationEngine work together.
This connects the data loading to the calculation engine.

Usage:
    python test_integration.py <excel_file_path>
    
Or without arguments to use sample data:
    python test_integration.py
"""

import sys
from modules.data_loader import DataLoader
from modules.calculation_engine import CalculationEngine, PlanningConfig, Material, BOMItem


def test_with_excel(file_path: str):
    """Test integration with actual Excel file"""
    print("=" * 60)
    print("INTEGRATION TEST - Excel File")
    print("=" * 60)
    print(f"File: {file_path}\n")
    
    # Step 1: Load data from Excel
    print("Step 1: Loading data from Excel...")
    loader = DataLoader(file_path)
    loaded_data = loader.load_all()
    
    print("\nData loaded:")
    print(f"  - Materials: {len(loaded_data.materials) if loaded_data.materials is not None else 0}")
    print(f"  - BOM: {len(loaded_data.bom) if loaded_data.bom is not None else 0}")
    print(f"  - Forecast: {len(loaded_data.forecast) if loaded_data.forecast is not None else 0}")
    
    # Step 2: Initialize calculation engine
    print("\nStep 2: Initializing calculation engine...")
    config = PlanningConfig(initial_date="2025-12-01", site="NLX1")
    engine = CalculationEngine(config)
    
    # Step 3: Load data into engine
    print("\nStep 3: Loading data into engine...")
    engine.load_from_excel(loaded_data)
    
    print("\nEngine status:")
    status = engine.get_status()
    for key, value in status.items():
        print(f"  - {key}: {value}")
    
    # Step 4: Run planning (partial)
    print("\nStep 4: Running planning calculations...")
    results = engine.run_full_planning()
    
    # Show sample results
    print("\nSample Results:")
    for mat_id in list(results['demand_forecast'].keys())[:3]:
        forecast = results['demand_forecast'].get(mat_id, {})
        if forecast:
            print(f"  {mat_id}: {list(forecast.items())[:3]}")
    
    print("\n" + "=" * 60)
    print("INTEGRATION TEST COMPLETED")
    print("=" * 60)


def test_with_sample_data():
    """Test integration with sample data (no Excel file needed)"""
    print("=" * 60)
    print("INTEGRATION TEST - Sample Data")
    print("=" * 60)
    
    # Initialize engine
    config = PlanningConfig(initial_date="2025-12-01")
    engine = CalculationEngine(config)
    
    # Add sample materials
    engine.materials = {
        'FG001': Material('FG001', 'Finished Good A', 'PRODUCED', machine_id='MILL01'),
        'FG002': Material('FG002', 'Finished Good B', 'PRODUCED', machine_id='MILL02'),
        'SF001': Material('SF001', 'Semi-Finished 1', 'PRODUCED', machine_id='MILL01'),
        'RM001': Material('RM001', 'Raw Material 1', 'PURCHASED'),
        'RM002': Material('RM002', 'Raw Material 2', 'PURCHASED'),
        'PKG01': Material('PKG01', 'Packaging', 'PURCHASED'),
    }
    
    # Add sample BOM
    engine.bom = [
        BOMItem('FG001', 'SF001', 2.0),    # FG001 needs 2x SF001
        BOMItem('FG001', 'PKG01', 1.0),    # FG001 needs 1x PKG01
        BOMItem('FG002', 'SF001', 1.5),    # FG002 needs 1.5x SF001
        BOMItem('SF001', 'RM001', 3.0),    # SF001 needs 3x RM001
        BOMItem('SF001', 'RM002', 1.0),    # SF001 needs 1x RM002
    ]
    
    # Add sample forecast
    engine.forecast = {
        'FG001': {'2026-01': 100, '2026-02': 120, '2026-03': 110},
        'FG002': {'2026-01': 80, '2026-02': 90, '2026-03': 85},
    }
    
    print("\nSample data loaded:")
    print(f"  - Materials: {len(engine.materials)}")
    print(f"  - BOM relationships: {len(engine.bom)}")
    print(f"  - Forecast items: {len(engine.forecast)}")
    
    # Run planning
    print("\nRunning planning calculations...")
    results = engine.run_full_planning()
    
    # Display results
    print("\n" + "-" * 40)
    print("DEMAND FORECAST (Line 01):")
    print("-" * 40)
    for mat_id, periods in results['demand_forecast'].items():
        print(f"  {mat_id}: {periods}")
    
    print("\n" + "-" * 40)
    print("DEPENDENT DEMAND (Line 02 - BOM Explosion):")
    print("-" * 40)
    for mat_id, periods in results['dependent_demand'].items():
        if periods:  # Only show if has values
            print(f"  {mat_id}: {periods}")
    
    print("\n" + "-" * 40)
    print("TOTAL DEMAND (Line 03):")
    print("-" * 40)
    for mat_id, periods in results['total_demand'].items():
        if periods:  # Only show if has values
            print(f"  {mat_id}: {periods}")
    
    print("\n" + "=" * 60)
    print("TEST COMPLETED SUCCESSFULLY")
    print("=" * 60)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Test with provided Excel file
        test_with_excel(sys.argv[1])
    else:
        # Test with sample data
        test_with_sample_data()
