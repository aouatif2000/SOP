# data_loader.py
# Excel Data Loader for S&OP Planning Application
# Started: 04/02/2026
# Updated: 05/02/2026

"""
Handles loading data from Excel S&OP consolidation file.
Maps Excel sheet names and columns to our data structures.

Status: IN PROGRESS
- [x] Basic structure
- [x] Excel reading - DONE
- [x] Column mapping - DONE
- [ ] Validation - TODO
- [ ] Error handling improvements - TODO
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
            'Product family': 'product_family',
            'Mill machine group': 'mill_machine',
            'Packaging machine group': 'pack_machine',
            'Active': 'active',
        },
        'bom': {
            'Material': 'parent_id',
            'Material Name': 'parent_name',
            'Component': 'child_id',
            'Component Description': 'child_name',
            'BILLOFMATERIALITEMQUANTITY': 'qty_per',
            'BOM Header Quantity in Base UoM': 'base_qty',
        },
        'forecast': {
            'Material number': 'material_id',
            'Material name ': 'name',
        },
        'stock_levels': {
            'Material': 'material_id',
            'Material Description': 'name',
            'Total Stock': 'quantity',
            'Unrestricted Stock': 'unrestricted_qty',
        },
        'safety_stock': {
            'Material number': 'material_id',
            'Final stafety stock': 'safety_stock_qty',
            'Target stock': 'target_stock',
            'Lot size': 'lot_size',
        },
        'machines': {
            'MachineID': 'machine_id',
            'Machine code': 'code',
            'Machine name': 'name',
            'OEE (%)': 'oee',
            'Machine group': 'group',
        },
    }
    
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.loaded_data = LoadedData()
        self.excel_file = None
        self.available_sheets = []
    
    def load_all(self) -> LoadedData:
        """Load all data from Excel file"""
        print(f"Loading from {self.file_path}...")
        
        try:
            # Open Excel file
            self.excel_file = pd.ExcelFile(self.file_path)
            self.available_sheets = self.excel_file.sheet_names
            print(f"Found {len(self.available_sheets)} sheets")
            
            # Load each data type
            self.loaded_data.materials = self._load_materials()
            self.loaded_data.bom = self._load_bom()
            self.loaded_data.forecast = self._load_forecast()
            self.loaded_data.stock_levels = self._load_stock_levels()
            self.loaded_data.safety_stock = self._load_safety_stock()
            self.loaded_data.machines = self._load_machines()
            
            print("Data loading completed!")
            return self.loaded_data
            
        except Exception as e:
            print(f"Error loading file: {e}")
            raise
    
    def _read_sheet(self, sheet_names: List[str]) -> Optional[pd.DataFrame]:
        """
        Read a sheet from Excel, trying multiple possible names
        Returns None if sheet not found
        """
        for sheet_name in sheet_names:
            if sheet_name in self.available_sheets:
                try:
                    df = pd.read_excel(self.excel_file, sheet_name=sheet_name)
                    print(f"  Loaded sheet '{sheet_name}': {len(df)} rows")
                    return df
                except Exception as e:
                    print(f"  Error reading '{sheet_name}': {e}")
        
        print(f"  Sheet not found (tried: {sheet_names})")
        return None
    
    def _standardize_columns(self, df: pd.DataFrame, sheet_type: str) -> pd.DataFrame:
        """Standardize column names based on mappings"""
        if df is None:
            return None
        
        column_map = self.COLUMN_MAPPINGS.get(sheet_type, {})
        
        # Rename columns that exist in our mapping
        rename_dict = {}
        for excel_col, std_col in column_map.items():
            if excel_col in df.columns:
                rename_dict[excel_col] = std_col
        
        if rename_dict:
            df = df.rename(columns=rename_dict)
        
        return df
    
    def _load_materials(self) -> Optional[pd.DataFrame]:
        """Load materials master data"""
        print("Loading materials...")
        df = self._read_sheet(['Material master', 'Materials'])
        return self._standardize_columns(df, 'materials')
    
    def _load_bom(self) -> Optional[pd.DataFrame]:
        """Load BOM data"""
        print("Loading BOM...")
        df = self._read_sheet(['BOM', 'Bill of Materials'])
        return self._standardize_columns(df, 'bom')
    
    def _load_forecast(self) -> Optional[pd.DataFrame]:
        """Load forecast data"""
        print("Loading forecast...")
        df = self._read_sheet(['Forecast sheet', 'Demand Forecast'])
        return self._standardize_columns(df, 'forecast')
    
    def _load_stock_levels(self) -> Optional[pd.DataFrame]:
        """Load stock levels"""
        print("Loading stock levels...")
        df = self._read_sheet(['Stock level sheet', 'Stock Levels', 'Inventory'])
        return self._standardize_columns(df, 'stock_levels')
    
    def _load_safety_stock(self) -> Optional[pd.DataFrame]:
        """Load safety stock settings"""
        print("Loading safety stock...")
        df = self._read_sheet(['Safety stock', 'Safety Stock'])
        return self._standardize_columns(df, 'safety_stock')
    
    def _load_machines(self) -> Optional[pd.DataFrame]:
        """Load machine/resource data"""
        print("Loading machines...")
        df = self._read_sheet(['OEE + Machine groups', 'Machines', 'Resources'])
        return self._standardize_columns(df, 'machines')
    
    def get_summary(self) -> Dict:
        """Get summary of loaded data"""
        summary = {}
        
        if self.loaded_data.materials is not None:
            summary['materials'] = len(self.loaded_data.materials)
        if self.loaded_data.bom is not None:
            summary['bom_relationships'] = len(self.loaded_data.bom)
        if self.loaded_data.forecast is not None:
            summary['forecast_items'] = len(self.loaded_data.forecast)
        if self.loaded_data.machines is not None:
            summary['machines'] = len(self.loaded_data.machines)
        
        return summary


# ============================================================================
# TESTING
# ============================================================================

if __name__ == "__main__":
    # Test with sample file path
    import sys
    
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
    else:
        file_path = "data/SOP_consolidation.xlsm"
    
    print(f"Testing DataLoader with: {file_path}")
    print("=" * 50)
    
    try:
        loader = DataLoader(file_path)
        data = loader.load_all()
        
        print("\n" + "=" * 50)
        print("SUMMARY:")
        print(loader.get_summary())
        
    except FileNotFoundError:
        print(f"File not found: {file_path}")
        print("Usage: python data_loader.py <excel_file_path>")
