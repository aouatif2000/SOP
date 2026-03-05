"""
S&OP Value Planning Engine
Converts volume-based planning results into financial values.
"""

import pandas as pd
from typing import Dict, List, Optional
from collections import defaultdict

from modules.models import (
    PlanningRow, LineType, ProductType,  ValuationParameters,
    SalesPriceItem, RawMaterialCost, MachineCost
)


class ValuePlanningEngine:
    """Converts planning volumes/hours to financial values."""
    
    def __init__(self, data, planning_results: Dict[str, List[PlanningRow]]):
        self.data = data
        self.planning_results = planning_results
        self.periods = data.periods
        self.value_results: Dict[str, List[PlanningRow]] = defaultdict(list)
        
    def calculate(self) -> Dict[str, List[PlanningRow]]:
        """Run value planning calculations."""
        print("\n" + "=" * 70)
        print("VALUE PLANNING ENGINE - FINANCIAL CONVERSION")
        print("=" * 70)
        
        # Convert each line type to financial values
        self._convert_demand_forecast()
        self._convert_total_demand()
        self._convert_inventory()
        self._convert_purchase_receipt()
        self._convert_capacity_utilization()
        self._convert_fte_requirements()
        
        # Create financial consolidation
        self._create_consolidation_rows()
        
        print("\nValue planning calculation complete")
        return dict(self.value_results)
    
    def _convert_demand_forecast(self):
        """Convert demand forecast volumes to revenue."""
        print("\n[1] Converting demand forecast to revenue...")
        
        rows = self.planning_results.get(LineType.DEMAND_FORECAST.value, [])
        converted = 0
        
        for row in rows:
            material = self.data.materials.get(row.material_number)
            if not material:
                continue
            
            # Get sales price
            sales_price = self.data.sales_prices.get(row.material_number)
            if not sales_price:
                continue
            
            # Create value row
            value_row = PlanningRow(
                material_number=row.material_number,
                material_name=row.material_name,
                product_type=row.product_type,
                product_family=row.product_family,
                spc_product=row.spc_product,
                product_cluster=row.product_cluster,
                product_name=row.product_name,
                line_type=LineType.DEMAND_FORECAST.value,
                aux_column=row.aux_column,
                aux_2_column=row.aux_2_column,
                starting_stock=0.0
            )
            
            # Convert volumes to revenue
            for period in self.periods:
                volume = row.get_value(period)
                revenue = volume * sales_price.price_per_unit
                value_row.set_value(period, revenue)
            
            self.value_results[LineType.DEMAND_FORECAST.value].append(value_row)
            converted += 1
        
        print(f"  Converted {converted} demand forecast rows to revenue")
    
    def _convert_total_demand(self):
        """Convert total demand volumes to raw material cost."""
        print("\n[2] Converting total demand to raw material cost...")
        
        rows = self.planning_results.get(LineType.TOTAL_DEMAND.value, [])
        converted = 0
        
        for row in rows:
            material = self.data.materials.get(row.material_number)
            if not material:
                continue
            
            # Only for raw materials and packaging
            if material.product_type not in [ProductType.RAW_MATERIAL, ProductType.PACKAGING_GOODS]:
                continue
            
            # Get material cost
            mat_cost = self.data.material_costs.get(row.material_number)
            if not mat_cost:
                continue
            
            # Create value row
            value_row = PlanningRow(
                material_number=row.material_number,
                material_name=row.material_name,
                product_type=row.product_type,
                product_family=row.product_family,
                spc_product=row.spc_product,
                product_cluster=row.product_cluster,
                product_name=row.product_name,
                line_type=LineType.TOTAL_DEMAND.value,
                aux_column=row.aux_column,
                aux_2_column=row.aux_2_column,
                starting_stock=0.0
            )
            
            # Convert volumes to cost
            for period in self.periods:
                volume = row.get_value(period)
                cost = volume * mat_cost.cost_per_unit
                value_row.set_value(period, cost)
            
            self.value_results[LineType.TOTAL_DEMAND.value].append(value_row)
            converted += 1
        
        print(f"  Converted {converted} total demand rows to raw material cost")
    
    def _convert_inventory(self):
        """Convert inventory volumes to inventory value."""
        print("\n[3] Converting inventory to value...")
        
        rows = self.planning_results.get(LineType.INVENTORY.value, [])
        converted = 0
        
        for row in rows:
            material = self.data.materials.get(row.material_number)
            if not material:
                continue
            
            # Get unit cost (try multiple sources)
            unit_cost = 0.0
            
            # 1. Try stock level sheet (Total Value / Total Stock)
            stock = self.data.stock.get(row.material_number)
            if stock and stock.get('Total Stock', 0) > 0:
                unit_cost = stock.get('Total Value', 0) / stock['Total Stock']
            
            # 2. Try raw material cost sheet
            if unit_cost == 0:
                mat_cost = self.data.material_costs.get(row.material_number)
                if mat_cost:
                    unit_cost = mat_cost.cost_per_unit
            
            # 3. Use default inventory value from material master
            if unit_cost == 0:
                unit_cost = material.default_inventory_value
            
            if unit_cost == 0:
                continue  # Skip if no cost available
            
            # Create value row
            value_row = PlanningRow(
                material_number=row.material_number,
                material_name=row.material_name,
                product_type=row.product_type,
                product_family=row.product_family,
                spc_product=row.spc_product,
                product_cluster=row.product_cluster,
                product_name=row.product_name,
                line_type=LineType.INVENTORY.value,
                aux_column=row.aux_column,
                aux_2_column=row.aux_2_column,
                starting_stock=row.starting_stock * unit_cost
            )
            
            # Convert inventory volumes to values
            for period in self.periods:
                volume = row.get_value(period)
                value = volume * unit_cost
                value_row.set_value(period, value)
            
            self.value_results[LineType.INVENTORY.value].append(value_row)
            converted += 1
        
        print(f"  Converted {converted} inventory rows to values")
    
    def _convert_purchase_receipt(self):
        """Convert purchase receipt volumes to purchase cost."""
        print("\n[4] Converting purchase receipts to cost...")
        
        rows = self.planning_results.get(LineType.PURCHASE_RECEIPT.value, [])
        converted = 0
        
        for row in rows:
            # Get material cost
            mat_cost = self.data.material_costs.get(row.material_number)
            if not mat_cost:
                continue
            
            # Create value row
            value_row = PlanningRow(
                material_number=row.material_number,
                material_name=row.material_name,
                product_type=row.product_type,
                product_family=row.product_family,
                spc_product=row.spc_product,
                product_cluster=row.product_cluster,
                product_name=row.product_name,
                line_type=LineType.PURCHASE_RECEIPT.value,
                aux_column=row.aux_column,
                aux_2_column=row.aux_2_column,
                starting_stock=0.0
            )
            
            # Convert volumes to cost
            for period in self.periods:
                volume = row.get_value(period)
                cost = volume * mat_cost.cost_per_unit
                value_row.set_value(period, cost)
            
            self.value_results[LineType.PURCHASE_RECEIPT.value].append(value_row)
            converted += 1
        
        print(f"  Converted {converted} purchase receipt rows to cost")
    
    def _convert_capacity_utilization(self):
        """Convert capacity hours to machine cost."""
        print("\n[5] Converting capacity utilization to machine cost...")
        
        rows = self.planning_results.get(LineType.CAPACITY_UTILIZATION.value, [])
        converted = 0
        
        for row in rows:
            # Extract machine/cost center from material_number
            machine_id = row.material_number
            
            # Get machine cost (first 5 chars of machine ID)
            cost_center = machine_id[:5] if len(machine_id) >= 5 else machine_id
            mach_cost = self.data.machine_costs.get(cost_center)
            
            if not mach_cost:
                continue
            
            # Create value row
            value_row = PlanningRow(
                material_number=row.material_number,
                material_name=row.material_name,
                product_type=row.product_type,
                product_family=row.product_family,
                spc_product=row.spc_product,
                product_cluster=row.product_cluster,
                product_name=row.product_name,
                line_type=LineType.CAPACITY_UTILIZATION.value,
                aux_column=row.aux_column,
                aux_2_column=row.aux_2_column,
                starting_stock=0.0
            )
            
            # Convert hours to cost
            for period in self.periods:
                hours = row.get_value(period)
                cost = hours * mach_cost.variable_cost_per_hour
                value_row.set_value(period, cost)
            
            self.value_results[LineType.CAPACITY_UTILIZATION.value].append(value_row)
            converted += 1
        
        print(f"  Converted {converted} capacity utilization rows to machine cost")
    
    def _convert_fte_requirements(self):
        """Convert FTE requirements to direct FTE cost."""
        print("\n[6] Converting FTE requirements to direct FTE cost...")
        
        if not self.data.valuation_params:
            print("  Warning: No valuation parameters loaded, skipping FTE conversion")
            return
        
        rows = self.planning_results.get(LineType.FTE_REQUIREMENTS.value, [])
        converted = 0
        
        direct_fte_cost = self.data.valuation_params.direct_fte_cost_per_month
        
        for row in rows:
            # Create value row
            value_row = PlanningRow(
                material_number=row.material_number,
                material_name=row.material_name,
                product_type=row.product_type,
                product_family=row.product_family,
                spc_product=row.spc_product,
                product_cluster=row.product_cluster,
                product_name=row.product_name,
                line_type=LineType.FTE_REQUIREMENTS.value,
                aux_column=row.aux_column,
                aux_2_column=row.aux_2_column,
                starting_stock=0.0
            )
            
            # Convert FTE count to cost
            for period in self.periods:
                fte_count = row.get_value(period)
                cost = fte_count * direct_fte_cost
                value_row.set_value(period, cost)
            
            self.value_results[LineType.FTE_REQUIREMENTS.value].append(value_row)
            converted += 1
        
        print(f"  Converted {converted} FTE requirement rows to direct FTE cost")
    
    def _create_consolidation_rows(self):
        """Create financial consolidation summary rows.
        
        Matches Excel VBA logic exactly:
        - TURNOVER = SUM(01. Demand forecast values)
        - RAW MATERIAL COST = SUM(03. Total demand values)
        - MACHINE COST = SUM(07. Capacity utilization values)
        - DIRECT FTE COST = SUM(12. FTE requirements values)
        - INDIRECT FTE COST = fixed per month from valuation params
        - OVERHEAD COST = fixed per month from valuation params
        - COST OF GOODS = RM + Machine + Direct FTE + Indirect FTE + Overhead
        - GROSS MARGIN = Turnover - COGS
        - SG&A COST = fixed per month from valuation params
        - EBITDA = Gross Margin - SG&A
        - D&A COST = Depreciation per year / 12
        - EBIT = EBITDA - D&A
        - FIXED ASSETS NET BOOK VALUE = constant from valuation params
        - INVENTORY VALUE = SUM(04. Inventory values)
        - RECEIVABLES = TURNOVER × (DSO / 30)
        - PAYABLES = SUM(06. Purchase receipt values) × (DPO / 30)
        - WORKING CAPITAL REQUIREMENTS = INVENTORY VALUE + RECEIVABLES - PAYABLES
        - CAPITAL INVESTMENT = FIXED ASSETS NBV + WORKING CAPITAL REQUIREMENTS
        - OPERATIONAL CASHFLOW = EBITDA + previous_inventory_value - current_inventory_value
        - ROCE = (EBIT × 12) / CAPITAL INVESTMENT
        """
        print("\n[7] Creating financial consolidation rows...")
        
        if not self.data.valuation_params:
            print("  Warning: No valuation parameters loaded, skipping consolidation")
            return
        
        vp = self.data.valuation_params
        
        # Accumulate totals from value results
        totals = {
            'turnover': {p: 0.0 for p in self.periods},
            'raw_material': {p: 0.0 for p in self.periods},
            'machine': {p: 0.0 for p in self.periods},
            'direct_fte': {p: 0.0 for p in self.periods},
            'inventory': {p: 0.0 for p in self.periods},
            'purchase_receipt': {p: 0.0 for p in self.periods},
        }
        
        # Sum by line type
        for row in self.value_results.get(LineType.DEMAND_FORECAST.value, []):
            for p in self.periods:
                totals['turnover'][p] += row.get_value(p)
        
        for row in self.value_results.get(LineType.TOTAL_DEMAND.value, []):
            for p in self.periods:
                totals['raw_material'][p] += row.get_value(p)
        
        for row in self.value_results.get(LineType.CAPACITY_UTILIZATION.value, []):
            for p in self.periods:
                totals['machine'][p] += row.get_value(p)
        
        for row in self.value_results.get(LineType.FTE_REQUIREMENTS.value, []):
            for p in self.periods:
                totals['direct_fte'][p] += row.get_value(p)
        
        for row in self.value_results.get(LineType.INVENTORY.value, []):
            for p in self.periods:
                totals['inventory'][p] += row.get_value(p)
        
        # Purchase receipt totals (used for Payables - matches Excel VBA)
        for row in self.value_results.get(LineType.PURCHASE_RECEIPT.value, []):
            for p in self.periods:
                totals['purchase_receipt'][p] += row.get_value(p)
        
        # Create consolidation rows
        consol_rows = []
        
        # 1. TURNOVER
        consol_rows.append(self._create_consol_row(
            "ZZZZZZ_TURNOVER", "", "01. Demand forecast", totals['turnover']
        ))
        
        # 2. RAW MATERIAL COST
        consol_rows.append(self._create_consol_row(
            "ZZZZZZ_RAW MATERIAL COST", "", "03. Total demand", totals['raw_material']
        ))
        
        # 3. MACHINE COST
        consol_rows.append(self._create_consol_row(
            "ZZZZZZ_MACHINE COST", "", "07. Capacity utilization", totals['machine']
        ))
        
        # 4. DIRECT FTE COST
        consol_rows.append(self._create_consol_row(
            "ZZZZZZ_DIRECT FTE COST", "", "12. FTE requirements", totals['direct_fte']
        ))
        
        # 5. INDIRECT FTE COST (fixed per month)
        indirect_values = {p: vp.indirect_fte_cost_per_month for p in self.periods}
        consol_rows.append(self._create_consol_row(
            "ZZZZZZ_INDIRECT FTE COST", "", None, indirect_values
        ))
        
        # 6. OVERHEAD COST (fixed per month)
        overhead_values = {p: vp.overhead_cost_per_month for p in self.periods}
        consol_rows.append(self._create_consol_row(
            "ZZZZZZ_OVERHEAD COST", "", None, overhead_values
        ))
        
        # 7. COST OF GOODS = RM + Machine + Direct FTE + Indirect FTE + Overhead
        cogs_values = {p: (totals['raw_material'][p] + totals['machine'][p] + 
                          totals['direct_fte'][p] + vp.indirect_fte_cost_per_month + 
                          vp.overhead_cost_per_month) for p in self.periods}
        consol_rows.append(self._create_consol_row(
            "ZZZZZZ_COST OF GOODS", "", None, cogs_values
        ))
        
        # 8. GROSS MARGIN = Turnover - COGS
        gross_margin = {p: totals['turnover'][p] - cogs_values[p] for p in self.periods}
        consol_rows.append(self._create_consol_row(
            "ZZZZZZ_GROSS MARGIN", "", None, gross_margin
        ))
        
        # 9. SG&A COST (fixed per month)
        sga_values = {p: vp.sga_cost_per_month for p in self.periods}
        consol_rows.append(self._create_consol_row(
            "ZZZZZZ_SG&A COST", "", None, sga_values
        ))
        
        # 10. EBITDA = Gross Margin - SG&A
        ebitda = {p: gross_margin[p] - vp.sga_cost_per_month for p in self.periods}
        consol_rows.append(self._create_consol_row(
            "ZZZZZZ_EBITDA", "", None, ebitda
        ))
        
        # 11. D&A COST (Depreciation & Amortization per year / 12)
        da_monthly = vp.depreciation_per_year / 12
        da_values = {p: da_monthly for p in self.periods}
        consol_rows.append(self._create_consol_row(
            "ZZZZZZ_D&A COST", "", None, da_values
        ))
        
        # 12. EBIT = EBITDA - D&A
        ebit = {p: ebitda[p] - da_monthly for p in self.periods}
        consol_rows.append(self._create_consol_row(
            "ZZZZZZ_EBIT", "", None, ebit
        ))
        
        # 13. FIXED ASSETS NET BOOK VALUE (constant from valuation params)
        nbv_values = {p: vp.net_book_value for p in self.periods}
        consol_rows.append(self._create_consol_row(
            "ZZZZZZ_FIXED ASSETS NET BOOK VALUE", "", None, nbv_values
        ))
        
        # 14. INVENTORY VALUE = SUM(04. Inventory values)
        consol_rows.append(self._create_consol_row(
            "ZZZZZZ_INVENTORY VALUE", "", "04. Inventory", totals['inventory']
        ))
        
        # 15. RECEIVABLES = TURNOVER × (DSO / 30)
        receivables = {p: totals['turnover'][p] * (vp.days_sales_outstanding / 30) 
                      for p in self.periods}
        consol_rows.append(self._create_consol_row(
            "ZZZZZZ_RECEIVABLES", "", "01. Demand forecast", receivables
        ))
        
        # 16. PAYABLES = SUM(Purchase Receipts) × (DPO / 30)
        # NOTE: Excel uses purchase receipt values, NOT raw material cost
        payables = {p: totals['purchase_receipt'][p] * (vp.days_payable_outstanding / 30) 
                   for p in self.periods}
        consol_rows.append(self._create_consol_row(
            "ZZZZZZ_PAYABLES", "", "06. Purchase receipt", payables
        ))
        
        # 17. WORKING CAPITAL REQUIREMENTS = INVENTORY VALUE + RECEIVABLES - PAYABLES
        working_capital = {p: (totals['inventory'][p] + receivables[p] - payables[p]) 
                          for p in self.periods}
        consol_rows.append(self._create_consol_row(
            "ZZZZZZ_WORKING CAPITAL REQUIREMENTS", "", None, working_capital
        ))
        
        # 18. CAPITAL INVESTMENT = FIXED ASSETS NBV + WORKING CAPITAL REQUIREMENTS
        capital_investment = {p: (vp.net_book_value + working_capital[p]) 
                            for p in self.periods}
        consol_rows.append(self._create_consol_row(
            "ZZZZZZ_CAPITAL INVESTMENT", "", None, capital_investment
        ))
        
        # 19. OPERATIONAL CASHFLOW = EBITDA + (prev_inventory - current_inventory)
        # For the first period, use starting inventory value from stock data
        prev_inventory_total = sum(
            row.starting_stock 
            for row in self.value_results.get(LineType.INVENTORY.value, [])
        )
        op_cashflow = {}
        for i, p in enumerate(self.periods):
            if i == 0:
                op_cashflow[p] = ebitda[p] + prev_inventory_total - totals['inventory'][p]
            else:
                prev_p = self.periods[i - 1]
                op_cashflow[p] = ebitda[p] + totals['inventory'][prev_p] - totals['inventory'][p]
        consol_rows.append(self._create_consol_row(
            "ZZZZZZ_OPERATIONAL CASHFLOW", "", None, op_cashflow
        ))
        
        # 20. ROCE = (EBIT × 12) / CAPITAL INVESTMENT
        roce = {}
        for p in self.periods:
            if capital_investment[p] != 0:
                roce[p] = (ebit[p] * 12) / capital_investment[p]
            else:
                roce[p] = 0
        consol_rows.append(self._create_consol_row(
            "ZZZZZZ_ROCE", "", None, roce
        ))
        
        # Add all consolidation rows to results
        self.value_results[LineType.CONSOLIDATION.value] = consol_rows
        
        print(f"  Created {len(consol_rows)} consolidation rows")
    
    def _create_consol_row(self, mat_num: str, name: str, aux: Optional[str], 
                           values: Dict[str, float]) -> PlanningRow:
        """Helper to create a consolidation row."""
        # Calculate 12-month total for aux_2_column (matches Excel behavior)
        total = sum(values.values())
        row = PlanningRow(
            material_number=mat_num,
            material_name=name,
            product_type="",
            product_family="",
            spc_product="",
            product_cluster="",
            product_name="",
            line_type=LineType.CONSOLIDATION.value,
            aux_column=aux,
            aux_2_column=str(total) if total != 0 else None,
            starting_stock=0.0,
            values=values.copy()
        )
        return row
