"""
S&OP Planning Engine - Inventory Engine
Calculates:
- Line 03: Total Demand
- Line 04: Inventory
- Line 05: Minimum Target Stock
- Line 06: Production Plan / Purchase Receipt
- Line 07: Purchase Plan
"""

import numpy as np
from typing import Dict, List, Tuple
from modules.models import PlanningRow, LineType, ProductType
from modules.data_loader import DataLoader


class InventoryEngine:
    """
    Generates inventory-related planning lines.
    
    Calculation sequence:
    1. Line 03: Total Demand = Forecast + Dependent Demand
    2. Line 05: Target Stock from safety stock config
    3. Line 06: Production/Purchase Plan based on need
    4. Line 04: Inventory balance calculation
    5. Line 07: Purchase Plan (timing)
    """
    
    def __init__(
        self, 
        data: DataLoader,
        forecast_data: Dict[str, Dict[str, float]],
        dependent_demand: Dict[str, Dict[str, float]]
    ):
        self.data = data
        self.periods = data.periods
        self.forecast_data = forecast_data
        self.dependent_demand = dependent_demand
        
        # Results
        self.total_demand: Dict[str, Dict[str, float]] = {}
        self.target_stock: Dict[str, float] = {}
        self.production_plan: Dict[str, Dict[str, float]] = {}
        self.purchase_receipt: Dict[str, Dict[str, float]] = {}
        self.purchase_plan: Dict[str, Dict[str, float]] = {}
        self.inventory: Dict[str, Dict[str, float]] = {}
        
        # Planning rows
        self.rows_03: List[PlanningRow] = []  # Total demand
        self.rows_04: List[PlanningRow] = []  # Inventory
        self.rows_05: List[PlanningRow] = []  # Target stock
        self.rows_06_prod: List[PlanningRow] = []  # Production plan
        self.rows_06_purch: List[PlanningRow] = []  # Purchase receipt
        self.rows_07: List[PlanningRow] = []  # Purchase plan
    
    def calculate(self) -> Dict[str, List[PlanningRow]]:
        """
        Calculate all inventory-related lines.
        Returns dict of line_type -> rows
        """
        self._calculate_total_demand()
        self._calculate_target_stock()
        self._calculate_production_purchase_plan()
        self._calculate_inventory()
        self._calculate_purchase_plan()
        
        return {
            LineType.TOTAL_DEMAND.value: self.rows_03,
            LineType.INVENTORY.value: self.rows_04,
            LineType.MIN_TARGET_STOCK.value: self.rows_05,
            LineType.PRODUCTION_PLAN.value: self.rows_06_prod,
            LineType.PURCHASE_RECEIPT.value: self.rows_06_purch,
            LineType.PURCHASE_PLAN.value: self.rows_07,
        }
    
    def _calculate_total_demand(self):
        """Line 03: Total Demand = Forecast + Dependent Demand"""
        print("  [03] Calculating Total Demand...")
        
        # Get all materials with any demand
        all_materials = set(self.forecast_data.keys()) | set(self.dependent_demand.keys())
        
        for mat_num in all_materials:
            forecast = self.forecast_data.get(mat_num, {})
            dependent = self.dependent_demand.get(mat_num, {})
            
            self.total_demand[mat_num] = {}
            for period in self.periods:
                total = forecast.get(period, 0.0) + dependent.get(period, 0.0)
                self.total_demand[mat_num][period] = total
            
            # Only create row if there's actual demand
            if any(v > 0 for v in self.total_demand[mat_num].values()):
                material = self.data.materials.get(mat_num)
                row = PlanningRow(
                    material_number=mat_num,
                    material_name=material.name if material else '',
                    product_type=material.product_type.value if material else '',
                    product_family=material.product_family if material else '',
                    spc_product=material.spc_product if material else '',
                    product_cluster=material.product_cluster if material else '',
                    product_name=material.product_name if material else '',
                    line_type=LineType.TOTAL_DEMAND.value,
                    values=self.total_demand[mat_num].copy()
                )
                self.rows_03.append(row)
        
        print(f"       → {len(self.rows_03)} materials with total demand")
    
    def _calculate_target_stock(self):
        """Line 05: Minimum Target Stock from safety stock config"""
        print("  [05] Setting Minimum Target Stock...")
        
        # Get materials that have demand or are in safety stock config
        materials_to_process = set(self.total_demand.keys()) | set(self.data.safety_stock.keys())
        
        for mat_num in materials_to_process:
            ss_config = self.data.safety_stock.get(mat_num)
            
            if ss_config:
                # Use target stock if set, otherwise safety stock
                target = ss_config.target_stock if ss_config.target_stock > 0 else ss_config.safety_stock
            else:
                target = 0.0
            
            self.target_stock[mat_num] = target
            
            # Create row
            material = self.data.materials.get(mat_num)
            if material:
                row = PlanningRow(
                    material_number=mat_num,
                    material_name=material.name,
                    product_type=material.product_type.value,
                    product_family=material.product_family,
                    spc_product=material.spc_product or '',
                    product_cluster=material.product_cluster or '',
                    product_name=material.product_name or '',
                    line_type=LineType.MIN_TARGET_STOCK.value,
                    values={p: target for p in self.periods}
                )
                self.rows_05.append(row)
        
        print(f"       → {len(self.rows_05)} materials with target stock")
    
    def _calculate_production_purchase_plan(self):
        """
        Line 06: Production Plan / Purchase Receipt
        
        Logic:
        - If projected inventory falls below target, plan to replenish
        - Produced materials -> Production Plan
        - Purchased materials -> Purchase Receipt
        - Round up to lot size
        """
        print("  [06] Calculating Production/Purchase Plan...")
        
        for mat_num in self.total_demand:
            material = self.data.materials.get(mat_num)
            if not material:
                continue
            
            # Determine if produced or purchased
            is_purchased = material.is_purchased
            
            # Get parameters
            initial_stock = self.data.stock_levels.get(mat_num, 0.0)
            target = self.target_stock.get(mat_num, 0.0)
            ss_config = self.data.safety_stock.get(mat_num)
            lot_size = ss_config.lot_size if ss_config else 1.0
            if lot_size <= 0:
                lot_size = 1.0
            
            # Calculate plan
            plan_data = {}
            running_stock = initial_stock
            
            for period in self.periods:
                demand = self.total_demand[mat_num].get(period, 0.0)
                projected_stock = running_stock - demand
                
                # Check if we need to replenish
                if projected_stock < target:
                    needed = target - projected_stock
                    # Round up to lot size
                    if lot_size > 1:
                        needed = np.ceil(needed / lot_size) * lot_size
                    plan_qty = max(0, needed)
                else:
                    plan_qty = 0.0
                
                plan_data[period] = plan_qty
                running_stock = projected_stock + plan_qty
            
            # Store in appropriate dict
            if is_purchased:
                self.purchase_receipt[mat_num] = plan_data
            else:
                self.production_plan[mat_num] = plan_data
            
            # Create row
            line_type = LineType.PURCHASE_RECEIPT if is_purchased else LineType.PRODUCTION_PLAN
            row = PlanningRow(
                material_number=mat_num,
                material_name=material.name,
                product_type=material.product_type.value,
                product_family=material.product_family,
                spc_product=material.spc_product or '',
                product_cluster=material.product_cluster or '',
                product_name=material.product_name or '',
                line_type=line_type.value,
                starting_stock=initial_stock,
                values=plan_data.copy()
            )
            
            if is_purchased:
                self.rows_06_purch.append(row)
            else:
                self.rows_06_prod.append(row)
        
        print(f"       → {len(self.rows_06_prod)} production plans, {len(self.rows_06_purch)} purchase receipts")
    
    def _calculate_inventory(self):
        """
        Line 04: Inventory Balance
        
        Formula: Inventory[t] = Inventory[t-1] - Demand[t] + Production[t] + Purchase[t]
        """
        print("  [04] Calculating Inventory Balance...")
        
        for mat_num in self.total_demand:
            material = self.data.materials.get(mat_num)
            initial_stock = self.data.stock_levels.get(mat_num, 0.0)
            
            self.inventory[mat_num] = {}
            running_stock = initial_stock
            
            for period in self.periods:
                demand = self.total_demand[mat_num].get(period, 0.0)
                production = self.production_plan.get(mat_num, {}).get(period, 0.0)
                purchase = self.purchase_receipt.get(mat_num, {}).get(period, 0.0)
                
                running_stock = max(0, running_stock - demand + production + purchase)
                self.inventory[mat_num][period] = running_stock
            
            # Create row
            if material:
                row = PlanningRow(
                    material_number=mat_num,
                    material_name=material.name,
                    product_type=material.product_type.value,
                    product_family=material.product_family,
                    spc_product=material.spc_product or '',
                    product_cluster=material.product_cluster or '',
                    product_name=material.product_name or '',
                    line_type=LineType.INVENTORY.value,
                    starting_stock=initial_stock,
                    values=self.inventory[mat_num].copy()
                )
                self.rows_04.append(row)
        
        print(f"       → {len(self.rows_04)} materials with inventory")
    
    def _calculate_purchase_plan(self):
        """
        Line 07: Purchase Plan
        
        Purchase plan with lead time considerations.
        Aux column indicates the order sequence.
        """
        print("  [07] Calculating Purchase Plan...")
        
        order_num = 1
        for mat_num, receipt_data in self.purchase_receipt.items():
            material = self.data.materials.get(mat_num)
            if not material:
                continue
            
            # Check if there are any purchases planned
            if not any(v > 0 for v in receipt_data.values()):
                continue
            
            plan_data = {}
            for period in self.periods:
                # For simplicity, purchase plan = purchase receipt (same timing)
                plan_data[period] = receipt_data.get(period, 0.0)
            
            self.purchase_plan[mat_num] = plan_data
            
            # Create row
            row = PlanningRow(
                material_number=mat_num,
                material_name=material.name,
                product_type=material.product_type.value,
                product_family=material.product_family,
                spc_product=material.spc_product or '',
                product_cluster=material.product_cluster or '',
                product_name=material.product_name or '',
                line_type=LineType.PURCHASE_PLAN.value,
                aux_column=str(order_num),
                values=plan_data.copy()
            )
            self.rows_07.append(row)
            order_num += 1
        
        print(f"       → {len(self.rows_07)} purchase plans")
    
    def get_production_plan(self) -> Dict[str, Dict[str, float]]:
        """Get all production plans."""
        return self.production_plan
    
    def get_total_demand(self) -> Dict[str, Dict[str, float]]:
        """Get all total demand data."""
        return self.total_demand
