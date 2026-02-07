"""
S&OP Planning Engine - BOM Engine
Calculates Line 02: Dependent Demand (BOM Explosion)
Calculates Line 08: Dependent Requirements (Detail)
"""

from typing import Dict, List, Set, Tuple
from modules.models import PlanningRow, LineType
from modules.data_loader import DataLoader


class BOMEngine:
    """
    Generates:
    - Line 02: Dependent Demand (aggregated)
    - Line 08: Dependent Requirements (detailed by parent)
    
    Logic: BOM Explosion
    For each parent material with demand, calculate child component requirements.
    Formula: Child_Demand = Parent_Demand × BOM_Quantity_Per
    """
    
    def __init__(self, data: DataLoader, forecast_data: Dict[str, Dict[str, float]]):
        self.data = data
        self.periods = data.periods
        self.forecast_data = forecast_data
        
        # Results
        self.dependent_demand: Dict[str, Dict[str, float]] = {}  # child -> {period: total}
        self.dependent_details: Dict[str, Dict[str, Dict[str, float]]] = {}  # child -> {parent -> {period: qty}}
        
        self.rows_02: List[PlanningRow] = []  # Line 02
        self.rows_08: List[PlanningRow] = []  # Line 08
    
    def calculate(self) -> Tuple[List[PlanningRow], List[PlanningRow]]:
        """
        Perform BOM explosion to calculate dependent demand.
        Returns (Line 02 rows, Line 08 rows)
        """
        print("  [02] Calculating Dependent Demand (BOM Explosion)...")
        
        # Initialize dependent demand for all materials
        for mat_num in self.data.materials:
            self.dependent_demand[mat_num] = {p: 0.0 for p in self.periods}
        
        # Process each BOM relationship
        for bom_item in self.data.bom:
            if bom_item.is_coproduct:
                continue
            
            parent = bom_item.parent_material
            child = bom_item.component_material
            qty_per = bom_item.quantity_per
            
            # Get parent's demand (from forecast)
            parent_demand = self.forecast_data.get(parent, {})
            
            for period in self.periods:
                parent_qty = parent_demand.get(period, 0.0)
                if parent_qty > 0:
                    child_qty = parent_qty * qty_per
                    
                    # Initialize if needed
                    if child not in self.dependent_demand:
                        self.dependent_demand[child] = {p: 0.0 for p in self.periods}
                    
                    # Add to aggregated demand
                    self.dependent_demand[child][period] += child_qty
                    
                    # Track details for Line 08
                    if child not in self.dependent_details:
                        self.dependent_details[child] = {}
                    if parent not in self.dependent_details[child]:
                        self.dependent_details[child][parent] = {p: 0.0 for p in self.periods}
                    self.dependent_details[child][parent][period] += child_qty
        
        # Generate Line 02 rows (aggregated dependent demand)
        for mat_num, demand_data in self.dependent_demand.items():
            if any(v > 0 for v in demand_data.values()):
                material = self.data.materials.get(mat_num)
                
                row = PlanningRow(
                    material_number=mat_num,
                    material_name=material.name if material else '',
                    product_type=material.product_type.value if material else '',
                    product_family=material.product_family if material else '',
                    spc_product=material.spc_product if material else '',
                    product_cluster=material.product_cluster if material else '',
                    product_name=material.product_name if material else '',
                    line_type=LineType.DEPENDENT_DEMAND.value,
                    values=demand_data.copy()
                )
                self.rows_02.append(row)
        
        print(f"       → {len(self.rows_02)} materials with dependent demand")
        
        # Generate Line 08 rows (detailed requirements)
        print("  [08] Generating Dependent Requirements (Detail)...")
        
        for child, parent_data in self.dependent_details.items():
            child_material = self.data.materials.get(child)
            
            for parent, period_data in parent_data.items():
                if any(v > 0 for v in period_data.values()):
                    row = PlanningRow(
                        material_number=child,
                        material_name=child_material.name if child_material else '',
                        product_type=child_material.product_type.value if child_material else '',
                        product_family=child_material.product_family if child_material else '',
                        spc_product=child_material.spc_product if child_material else '',
                        product_cluster=child_material.product_cluster if child_material else '',
                        product_name=child_material.product_name if child_material else '',
                        line_type=LineType.DEPENDENT_REQUIREMENTS.value,
                        aux_column=parent,  # Store parent material number
                        values=period_data.copy()
                    )
                    self.rows_08.append(row)
        
        print(f"       → {len(self.rows_08)} detailed requirements")
        
        return self.rows_02, self.rows_08
    
    def get_dependent_demand(self, material: str, period: str) -> float:
        """Get dependent demand for a material and period."""
        return self.dependent_demand.get(material, {}).get(period, 0.0)
    
    def get_all_dependent_demand(self) -> Dict[str, Dict[str, float]]:
        """Get all dependent demand data."""
        return self.dependent_demand
