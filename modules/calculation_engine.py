# calculation_engine.py
# S&OP Planning Calculation Engine
# Started: 03/02/2026
# Updated: 06/02/2026 - Added Excel integration

"""
Core calculation engine for S&OP Planning Application.
Replicates logic from Excel VBA macros (mdl_Build_formulas, mdl_Build_VolumePlanningSheet)

Status: IN PROGRESS
- [x] Data classes defined
- [x] Basic demand forecast calculation
- [x] BOM explosion (dependent demand)
- [x] Excel data integration - NEW
- [ ] Inventory calculation - WIP
- [ ] Production planning heuristic - TODO
- [ ] Capacity utilization - TODO
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional
from enum import Enum
import pandas as pd


# ============================================================================
# ENUMS
# ============================================================================

class LineType(Enum):
    """Line types matching Excel volume planning sheet"""
    DEMAND_FORECAST = "01. Demand forecast"
    DEPENDENT_DEMAND = "02. Dependent demand"
    TOTAL_DEMAND = "03. Total demand"
    INVENTORY = "04. Inventory"
    TARGET_STOCK = "05. Target stock"
    PRODUCTION_PLAN = "06. Production plan"
    # TODO: Add remaining line types (07-13)


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class PlanningConfig:
    """Configuration for planning run"""
    initial_date: str
    forecast_months: int = 12
    actuals_months: int = 3
    frozen_months: int = 1
    site: str = "ALL"
    unlimited_capacity_machines: List[str] = None
    default_safety_stock_months: float = 1.5
    
    def __post_init__(self):
        if self.unlimited_capacity_machines is None:
            self.unlimited_capacity_machines = []


@dataclass
class Material:
    """Material master data"""
    material_id: str
    name: str
    material_type: str  # PRODUCED, PURCHASED, MAKE_OR_BUY
    uom: str = "EA"
    lot_size: float = 1.0
    min_lot: float = 0.0
    max_lot: float = float('inf')
    lead_time_days: int = 0
    safety_stock_days: int = 0
    machine_id: str = None


@dataclass 
class BOMItem:
    """Bill of Materials relationship"""
    parent_id: str
    child_id: str
    qty_per: float
    uom: str = "EA"


@dataclass
class Machine:
    """Machine/Resource master data"""
    machine_id: str
    name: str
    capacity_hours: float
    oee: float = 0.85
    site: str = "MAIN"


# ============================================================================
# CALCULATION ENGINE CLASS
# ============================================================================

class CalculationEngine:
    """
    Main calculation engine for S&OP planning.
    Replicates Excel VBA logic from mdl_Build_formulas
    """
    
    def __init__(self, config: PlanningConfig):
        self.config = config
        
        # Master data (to be loaded)
        self.materials: Dict[str, Material] = {}
        self.bom: List[BOMItem] = []
        self.machines: Dict[str, Machine] = {}
        
        # Input data
        self.forecast: Dict[str, Dict[str, float]] = {}  # material_id -> period -> qty
        self.stock_levels: Dict[str, float] = {}  # material_id -> qty
        self.safety_stock: Dict[str, float] = {}  # material_id -> months
        
        # Results
        self.results = pd.DataFrame()
        self.calculation_log: List[str] = []
    
    def _log(self, message: str):
        """Add to calculation log for traceability"""
        self.calculation_log.append(message)
        print(f"[CALC] {message}")
    
    # ========================================================================
    # DATA LOADING METHODS
    # ========================================================================
    
    def load_from_excel(self, loaded_data) -> None:
        """
        Load data from DataLoader output into the engine
        This connects the data_loader module to the calculation engine
        
        Args:
            loaded_data: LoadedData object from DataLoader
        
        Added: 06/02/2026
        """
        self._log("Loading data from Excel into engine...")
        
        # Load materials
        if loaded_data.materials is not None and len(loaded_data.materials) > 0:
            self._load_materials_from_df(loaded_data.materials)
        
        # Load BOM
        if loaded_data.bom is not None and len(loaded_data.bom) > 0:
            self._load_bom_from_df(loaded_data.bom)
        
        # Load forecast
        if loaded_data.forecast is not None and len(loaded_data.forecast) > 0:
            self._load_forecast_from_df(loaded_data.forecast)
        
        # Load stock levels
        if loaded_data.stock_levels is not None and len(loaded_data.stock_levels) > 0:
            self._load_stock_from_df(loaded_data.stock_levels)
        
        # Load safety stock
        if loaded_data.safety_stock is not None and len(loaded_data.safety_stock) > 0:
            self._load_safety_stock_from_df(loaded_data.safety_stock)
        
        self._log(f"Loaded: {len(self.materials)} materials, {len(self.bom)} BOM items")
    
    def _load_materials_from_df(self, df: pd.DataFrame):
        """Convert materials DataFrame to Material objects"""
        self._log(f"Processing {len(df)} materials...")
        
        for _, row in df.iterrows():
            mat_id = str(row.get('material_id', row.get('Material number (SKU)', '')))
            if not mat_id or mat_id == 'nan':
                continue
                
            # Determine material type
            raw_type = str(row.get('material_type', row.get('Product type', 'PRODUCED')))
            if 'raw' in raw_type.lower() or 'packaging' in raw_type.lower():
                mat_type = 'PURCHASED'
            elif 'bulk' in raw_type.lower():
                mat_type = 'PRODUCED'
            else:
                mat_type = 'PRODUCED'
            
            mat = Material(
                material_id=mat_id,
                name=str(row.get('name', row.get('Material name (SKU)', ''))),
                material_type=mat_type,
                machine_id=row.get('mill_machine', row.get('Mill machine group', None))
            )
            self.materials[mat.material_id] = mat
        
        self._log(f"Loaded {len(self.materials)} materials")
    
    def _load_bom_from_df(self, df: pd.DataFrame):
        """Convert BOM DataFrame to BOMItem objects"""
        self._log(f"Processing {len(df)} BOM relationships...")
        
        for _, row in df.iterrows():
            parent_id = str(row.get('parent_id', row.get('Material', '')))
            child_id = str(row.get('child_id', row.get('Component', '')))
            
            if not parent_id or not child_id or parent_id == 'nan' or child_id == 'nan':
                continue
            
            qty = float(row.get('qty_per', row.get('BILLOFMATERIALITEMQUANTITY', 1.0)) or 1.0)
            
            bom_item = BOMItem(
                parent_id=parent_id,
                child_id=child_id,
                qty_per=qty
            )
            self.bom.append(bom_item)
        
        self._log(f"Loaded {len(self.bom)} BOM relationships")
    
    def _load_forecast_from_df(self, df: pd.DataFrame):
        """Convert forecast DataFrame to forecast dict"""
        self._log("Processing forecast data...")
        
        # Get material ID column
        id_col = 'material_id' if 'material_id' in df.columns else 'Material number'
        
        # Find period columns (format: YYYY/MM or YYYY-MM)
        period_cols = [col for col in df.columns if '/' in str(col) or 
                       (len(str(col)) == 7 and '-' in str(col))]
        
        for _, row in df.iterrows():
            mat_id = str(row.get(id_col, ''))
            if not mat_id or mat_id == 'nan':
                continue
            
            self.forecast[mat_id] = {}
            for period_col in period_cols:
                val = row.get(period_col, 0)
                if pd.notna(val) and val != 0:
                    # Standardize period format to YYYY-MM
                    period_str = str(period_col).replace('/', '-').strip()
                    self.forecast[mat_id][period_str] = float(val)
        
        self._log(f"Loaded forecast for {len(self.forecast)} materials")
    
    def _load_stock_from_df(self, df: pd.DataFrame):
        """Convert stock levels DataFrame to stock dict"""
        self._log("Processing stock levels...")
        
        id_col = 'material_id' if 'material_id' in df.columns else 'Material'
        qty_col = 'quantity' if 'quantity' in df.columns else 'Total Stock'
        
        for _, row in df.iterrows():
            mat_id = str(row.get(id_col, ''))
            if not mat_id or mat_id == 'nan':
                continue
            
            qty = float(row.get(qty_col, 0) or 0)
            self.stock_levels[mat_id] = qty
        
        self._log(f"Loaded stock for {len(self.stock_levels)} materials")
    
    def _load_safety_stock_from_df(self, df: pd.DataFrame):
        """Convert safety stock DataFrame to safety stock dict"""
        self._log("Processing safety stock...")
        
        id_col = 'material_id' if 'material_id' in df.columns else 'Material number'
        ss_col = 'safety_stock_qty' if 'safety_stock_qty' in df.columns else 'Final stafety stock'
        
        for _, row in df.iterrows():
            mat_id = str(row.get(id_col, ''))
            if not mat_id or mat_id == 'nan':
                continue
            
            ss = float(row.get(ss_col, 0) or 0)
            self.safety_stock[mat_id] = ss
        
        self._log(f"Loaded safety stock for {len(self.safety_stock)} materials")
    
    def load_materials(self, materials_df: pd.DataFrame):
        """Load materials from DataFrame - legacy method"""
        self._load_materials_from_df(materials_df)
    
    def load_bom(self, bom_df: pd.DataFrame):
        """Load BOM relationships from DataFrame - legacy method"""
        self._load_bom_from_df(bom_df)
    
    def load_forecast(self, forecast_df: pd.DataFrame):
        """Load demand forecast from DataFrame - legacy method"""
        self._load_forecast_from_df(forecast_df)
    
    # ========================================================================
    # CALCULATION METHODS
    # ========================================================================
    
    def calculate_demand_forecast(self) -> Dict[str, Dict[str, float]]:
        """
        Step 1: Get demand forecast values
        Source: Forecast sheet in Excel
        """
        self._log("Calculating demand forecast...")
        demand_forecast = {}
        
        for mat_id, mat in self.materials.items():
            if mat.material_type == 'PRODUCED':
                demand_forecast[mat_id] = self.forecast.get(mat_id, {})
        
        return demand_forecast
    
    def calculate_dependent_demand(self, production_plan: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, float]]:
        """
        Step 2: Calculate dependent demand via BOM explosion
        Formula: Dependent Demand = Sum(Parent Production * Qty Per)
        
        This replicates the BOM explosion logic from mdl_Build_formulas
        """
        self._log("Calculating dependent demand (BOM explosion)...")
        dependent_demand = {}
        
        # Initialize
        for mat_id in self.materials:
            dependent_demand[mat_id] = {}
        
        # BOM explosion
        for bom_item in self.bom:
            parent_id = bom_item.parent_id
            child_id = bom_item.child_id
            qty_per = bom_item.qty_per
            
            if parent_id in production_plan:
                for period, parent_qty in production_plan[parent_id].items():
                    child_demand = parent_qty * qty_per
                    
                    if child_id not in dependent_demand:
                        dependent_demand[child_id] = {}
                    if period not in dependent_demand[child_id]:
                        dependent_demand[child_id][period] = 0
                    dependent_demand[child_id][period] += child_demand
        
        return dependent_demand
    
    def calculate_total_demand(self, demand_forecast: Dict, dependent_demand: Dict) -> Dict[str, Dict[str, float]]:
        """
        Step 3: Total Demand = Demand Forecast + Dependent Demand
        """
        self._log("Calculating total demand...")
        total_demand = {}
        
        all_materials = set(demand_forecast.keys()) | set(dependent_demand.keys())
        
        for mat_id in all_materials:
            total_demand[mat_id] = {}
            forecast = demand_forecast.get(mat_id, {})
            dependent = dependent_demand.get(mat_id, {})
            
            all_periods = set(forecast.keys()) | set(dependent.keys())
            
            for period in all_periods:
                total_demand[mat_id][period] = forecast.get(period, 0) + dependent.get(period, 0)
        
        return total_demand
    
    # TODO: Implement remaining calculations
    # - calculate_inventory()
    # - calculate_target_stock()
    # - run_production_heuristic()
    # - calculate_capacity_utilization()
    
    def run_full_planning(self):
        """
        Main orchestration method - runs all calculations
        Status: IN PROGRESS
        """
        self._log("Starting full planning run...")
        self._log("WARNING: Not all calculations implemented yet")
        
        # Step 1: Demand forecast
        demand_forecast = self.calculate_demand_forecast()
        
        # Step 2-3: Will need production plan first (chicken-egg problem)
        # For now, use forecast as initial production estimate
        dependent_demand = self.calculate_dependent_demand(demand_forecast)
        total_demand = self.calculate_total_demand(demand_forecast, dependent_demand)
        
        self._log("Planning run completed (partial)")
        
        return {
            'demand_forecast': demand_forecast,
            'dependent_demand': dependent_demand,
            'total_demand': total_demand
        }
    
    def get_status(self) -> Dict:
        """Get current engine status"""
        return {
            'materials_loaded': len(self.materials),
            'bom_items': len(self.bom),
            'forecast_items': len(self.forecast),
            'stock_levels': len(self.stock_levels),
            'safety_stock_items': len(self.safety_stock),
        }


# ============================================================================
# TESTING / DEVELOPMENT
# ============================================================================

if __name__ == "__main__":
    # Quick test with sample data
    config = PlanningConfig(initial_date="2025-12-01")
    engine = CalculationEngine(config)
    
    # Test materials
    engine.materials = {
        'FG001': Material('FG001', 'Finished Good 1', 'PRODUCED'),
        'SF001': Material('SF001', 'Semi-Finished 1', 'PRODUCED'),
        'RM001': Material('RM001', 'Raw Material 1', 'PURCHASED'),
    }
    
    # Test BOM
    engine.bom = [
        BOMItem('FG001', 'SF001', 2.0),
        BOMItem('SF001', 'RM001', 3.0),
    ]
    
    # Test forecast
    engine.forecast = {
        'FG001': {'2026-01': 100, '2026-02': 120, '2026-03': 110}
    }
    
    # Run
    results = engine.run_full_planning()
    print("\nResults:")
    print(f"Dependent demand for SF001: {results['dependent_demand'].get('SF001', {})}")
    print(f"Dependent demand for RM001: {results['dependent_demand'].get('RM001', {})}")
