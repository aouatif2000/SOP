# data_loader.py
# Excel Data Loader for S&OP Planning Application
# Started: 05/02/2026

"""
Handles loading data from Excel S&OP consolidation file.
Maps Excel sheet names and columns to our data structures.

Status: JUST STARTED
- [x] Basic structure
- [ ] Excel reading - TODO
- [ ] Column mapping - TODO
- [ ] Validation - TODO
"""

import pandas as pd
from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class LoadedData:
    """Container for all loaded data"""
    materials: pd.DataFrame = None
    bom: pd.DataFrame = None
    machines: pd.DataFrame = None
    routing: pd.DataFrame = None
    forecast: pd.DataFrame = None
    stock_levels: pd.DataFrame = None
    safety_stock: pd.DataFrame = None


class DataLoader:
    """
    Loads data from Excel S&OP consolidation file.
    
    Expected sheets (based on Excel analysis):
    - Material master
    - BOM
    - Routing / Machine Assignment  
    - Forecast sheet
    - Stock level sheet
    - Safety stock
    - OEE + Machine groups
    """
    
    # Sheet name mappings (Excel name -> standard name)
    SHEET_MAPPINGS = {
        'Material master': 'materials',
        'Materials': 'materials',
        'BOM': 'bom',
        'Bill of Materials': 'bom',
        'Forecast sheet': 'forecast',
        'Demand Forecast': 'forecast',
        'Stock level sheet': 'stock_levels',
        'Safety stock': 'safety_stock',
        'OEE + Machine groups': 'machines',
        'Routing': 'routing',
    }
    
    # Column mappings for each sheet (Excel column -> standard column)
    COLUMN_MAPPINGS = {
        'materials': {
            'Material number (SKU)': 'material_id',
            'Material name (SKU)': 'name',
            'Product type (packaged material/bulk material/raw material/packaging goods/other)': 'material_type',
            'Mill machine group': 'machine_id',
        },
        'bom': {
            'Material': 'parent_id',
            'Component': 'child_id',
            'BILLOFMATERIALITEMQUANTITY': 'qty_per',
        },
        # TODO: Add more mappings
    }
    
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.loaded_data = LoadedData()
    
    def load_all(self) -> LoadedData:
        """Load all data from Excel file"""
        # TODO: Implement
        print(f"Loading from {self.file_path}...")
        print("WARNING: Not fully implemented yet")
        return self.loaded_data
    
    def _read_sheet(self, sheet_name: str) -> pd.DataFrame:
        """Read a single sheet from Excel"""
        # TODO: Implement
        pass
    
    def _standardize_columns(self, df: pd.DataFrame, sheet_type: str) -> pd.DataFrame:
        """Standardize column names based on mappings"""
        # TODO: Implement
        pass


# Quick test
if __name__ == "__main__":
    loader = DataLoader("test.xlsx")
    data = loader.load_all()
