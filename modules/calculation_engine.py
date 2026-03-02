# calculation_engine.py
# S&OP Planning Calculation Engine
# Started: 03/02/2026
# Updated: 06/02/2026 - Added Excel integration

"""
Core calculation engine for S&OP Planning Application.
Replicates logic from Excel VBA macros (mdl_Build_formulas, mdl_Build_VolumePlanningSheet)

Note: This module is a legacy/alternative implementation.
The main pipeline uses planning_engine.py -> forecast_engine.py -> bom_engine.py
    -> inventory_engine.py -> capacity_engine.py
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional
from enum import Enum
import pandas as pd


class LineType(Enum):
    DEMAND_FORECAST = "01. Demand forecast"
    DEPENDENT_DEMAND = "02. Dependent demand"
    TOTAL_DEMAND = "03. Total demand"
    INVENTORY = "04. Inventory"
    TARGET_STOCK = "05. Target stock"
    PRODUCTION_PLAN = "06. Production plan"


@dataclass
class PlanningConfig:
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
    material_id: str
    name: str
    material_type: str
    uom: str = "EA"
    lot_size: float = 1.0
    min_lot: float = 0.0
    max_lot: float = float('inf')
    lead_time_days: int = 0
    safety_stock_days: int = 0
    machine_id: str = None


@dataclass
class BOMItem:
    parent_id: str
    child_id: str
    qty_per: float
    uom: str = "EA"


@dataclass
class Machine:
    machine_id: str
    name: str
    capacity_hours: float
    oee: float = 0.85
    site: str = "MAIN"


class CalculationEngine:
    """
    Legacy calculation engine. Main pipeline uses PlanningEngine instead.
    """

    def __init__(self, config: PlanningConfig):
        self.config = config
        self.materials: Dict[str, Material] = {}
        self.bom: List[BOMItem] = []
        self.machines: Dict[str, Machine] = {}
        self.forecast: Dict[str, Dict[str, float]] = {}
        self.stock_levels: Dict[str, float] = {}
        self.safety_stock: Dict[str, float] = {}
        self.results = pd.DataFrame()
        self.calculation_log: List[str] = []

    def _log(self, message: str):
        self.calculation_log.append(message)
        print(f"[CALC] {message}")

    def load_from_excel(self, loaded_data) -> None:
        self._log("Loading data from Excel into engine...")
        if loaded_data.materials is not None and len(loaded_data.materials) > 0:
            self._load_materials_from_df(loaded_data.materials)
        if loaded_data.bom is not None and len(loaded_data.bom) > 0:
            self._load_bom_from_df(loaded_data.bom)
        if loaded_data.forecast is not None and len(loaded_data.forecast) > 0:
            self._load_forecast_from_df(loaded_data.forecast)
        if loaded_data.stock_levels is not None and len(loaded_data.stock_levels) > 0:
            self._load_stock_from_df(loaded_data.stock_levels)
        if loaded_data.safety_stock is not None and len(loaded_data.safety_stock) > 0:
            self._load_safety_stock_from_df(loaded_data.safety_stock)
        self._log(f"Loaded: {len(self.materials)} materials, {len(self.bom)} BOM items")

    def _load_materials_from_df(self, df: pd.DataFrame):
        self._log(f"Processing {len(df)} materials...")
        for _, row in df.iterrows():
            mat_id = str(row.get('material_id', row.get('Material number (SKU)', '')))
            if not mat_id or mat_id == 'nan':
                continue
            raw_type = str(row.get('material_type', row.get('Product type', 'PRODUCED')))
            if 'raw' in raw_type.lower() or 'packaging' in raw_type.lower():
                mat_type = 'PURCHASED'
            else:
                mat_type = 'PRODUCED'
            mat = Material(
                material_id=mat_id,
                name=str(row.get('name', row.get('Material name (SKU)', ''))),
                material_type=mat_type,
                machine_id=row.get('mill_machine', row.get('Mill machine group', None))
            )
            self.materials[mat.material_id] = mat

    def _load_bom_from_df(self, df: pd.DataFrame):
        for _, row in df.iterrows():
            parent_id = str(row.get('parent_id', row.get('Material', '')))
            child_id = str(row.get('child_id', row.get('Component', '')))
            if not parent_id or not child_id or parent_id == 'nan' or child_id == 'nan':
                continue
            qty = float(row.get('qty_per', row.get('BILLOFMATERIALITEMQUANTITY', 1.0)) or 1.0)
            self.bom.append(BOMItem(parent_id=parent_id, child_id=child_id, qty_per=qty))

    def _load_forecast_from_df(self, df: pd.DataFrame):
        id_col = 'material_id' if 'material_id' in df.columns else 'Material number'
        period_cols = [col for col in df.columns if '/' in str(col) or
                       (len(str(col)) == 7 and '-' in str(col))]
        for _, row in df.iterrows():
            mat_id = str(row.get(id_col, ''))
            if not mat_id or mat_id == 'nan':
                continue
            self.forecast[mat_id] = {}
            for pc in period_cols:
                val = row.get(pc, 0)
                if pd.notna(val) and val != 0:
                    period_str = str(pc).replace('/', '-').strip()
                    self.forecast[mat_id][period_str] = float(val)

    def _load_stock_from_df(self, df: pd.DataFrame):
        id_col = 'material_id' if 'material_id' in df.columns else 'Material'
        qty_col = 'quantity' if 'quantity' in df.columns else 'Total Stock'
        for _, row in df.iterrows():
            mat_id = str(row.get(id_col, ''))
            if not mat_id or mat_id == 'nan':
                continue
            self.stock_levels[mat_id] = float(row.get(qty_col, 0) or 0)

    def _load_safety_stock_from_df(self, df: pd.DataFrame):
        id_col = 'material_id' if 'material_id' in df.columns else 'Material number'
        ss_col = 'safety_stock_qty' if 'safety_stock_qty' in df.columns else 'Final stafety stock'
        for _, row in df.iterrows():
            mat_id = str(row.get(id_col, ''))
            if not mat_id or mat_id == 'nan':
                continue
            self.safety_stock[mat_id] = float(row.get(ss_col, 0) or 0)

    def load_materials(self, df):
        self._load_materials_from_df(df)

    def load_bom(self, df):
        self._load_bom_from_df(df)

    def load_forecast(self, df):
        self._load_forecast_from_df(df)

    def calculate_demand_forecast(self):
        self._log("Calculating demand forecast...")
        return {mid: self.forecast.get(mid, {}) for mid, m in self.materials.items() if m.material_type == 'PRODUCED'}

    def calculate_dependent_demand(self, production_plan):
        self._log("Calculating dependent demand...")
        dd = {mid: {} for mid in self.materials}
        for bi in self.bom:
            if bi.parent_id in production_plan:
                for period, qty in production_plan[bi.parent_id].items():
                    dd.setdefault(bi.child_id, {})
                    dd[bi.child_id][period] = dd[bi.child_id].get(period, 0) + qty * bi.qty_per
        return dd

    def calculate_total_demand(self, demand_forecast, dependent_demand):
        self._log("Calculating total demand...")
        td = {}
        for mid in set(demand_forecast) | set(dependent_demand):
            td[mid] = {}
            for p in set(demand_forecast.get(mid, {})) | set(dependent_demand.get(mid, {})):
                td[mid][p] = demand_forecast.get(mid, {}).get(p, 0) + dependent_demand.get(mid, {}).get(p, 0)
        return td

    def run_full_planning(self):
        self._log("Starting full planning run...")
        df = self.calculate_demand_forecast()
        dd = self.calculate_dependent_demand(df)
        td = self.calculate_total_demand(df, dd)
        return {'demand_forecast': df, 'dependent_demand': dd, 'total_demand': td}

    def get_status(self):
        return {
            'materials_loaded': len(self.materials),
            'bom_items': len(self.bom),
            'forecast_items': len(self.forecast),
            'stock_levels': len(self.stock_levels),
            'safety_stock_items': len(self.safety_stock),
        }


if __name__ == "__main__":
    config = PlanningConfig(initial_date="2025-12-01")
    engine = CalculationEngine(config)
    engine.materials = {
        'FG001': Material('FG001', 'Finished Good 1', 'PRODUCED'),
        'SF001': Material('SF001', 'Semi-Finished 1', 'PRODUCED'),
        'RM001': Material('RM001', 'Raw Material 1', 'PURCHASED'),
    }
    engine.bom = [BOMItem('FG001', 'SF001', 2.0), BOMItem('SF001', 'RM001', 3.0)]
    engine.forecast = {'FG001': {'2026-01': 100, '2026-02': 120, '2026-03': 110}}
    results = engine.run_full_planning()
    print(f"\nDependent demand for SF001: {results['dependent_demand'].get('SF001', {})}")
    print(f"Dependent demand for RM001: {results['dependent_demand'].get('RM001', {})}")
