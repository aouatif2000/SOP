"""
S&OP Planning Engine - Inventory Quality Engine

VBA reference: BuildInventoryQualitySheet() (line 6779)

5-way monetary categorization per period, per material:
  Under           — amount below target (negative signal, shown as positive shortfall)
  Safety Stock    — safety stock portion (up to safety_stock * unit_value)
  Strategic Stock — strategic stock portion
  Normal Variation— excess above target, up to one lot-size worth
  Overstock       — excess beyond one lot-size (the problematic overhang)

Invariant: Under + Safety + Strategic + Normal + Overstock = total inventory value
"""

from typing import Dict, List, Any


class InventoryQualityEngine:
    def __init__(self, data, planning_results: Dict, value_results: Dict):
        self.data = data
        self.planning_results = planning_results
        self.value_results = value_results
        self.periods: List[str] = data.periods

    def _get_unit_value(self, mat_num: str) -> float:
        """Unit inventory value (EUR/unit) — from value Line 04 aux_column, or stock sheet fallback."""
        inv_val_rows = self.value_results.get('04. Inventory', [])
        for row in inv_val_rows:
            if row.material_number == mat_num:
                try:
                    return float(row.aux_column)
                except (TypeError, ValueError):
                    break
        # Fallback: stock sheet TotalValue / TotalStock, then material master
        stock = self.data.stock.get(mat_num)
        if stock:
            ts = stock.get('Total Stock', 0)
            if ts and ts > 0:
                return stock.get('Total Value', 0) / ts
        mat = self.data.materials.get(mat_num)
        if mat:
            return mat.default_inventory_value
        return 0.0

    def calculate(self) -> Dict[str, Any]:
        """Return quality breakdown for all materials with an inventory row."""
        inv_val_rows = self.value_results.get('04. Inventory', [])

        per_material: List[Dict] = []

        for row in inv_val_rows:
            mat_num = row.material_number
            unit_val = self._get_unit_value(mat_num)

            ss_cfg = self.data.safety_stock.get(mat_num)
            safety_stock_vol = ss_cfg.safety_stock if ss_cfg else 0.0
            strategic_stock_vol = ss_cfg.strategic_stock if ss_cfg else 0.0
            lot_size_vol = max(ss_cfg.lot_size, 1.0) if ss_cfg else 1.0

            safety_val = safety_stock_vol * unit_val
            strategic_val = strategic_stock_vol * unit_val
            lot_val = lot_size_vol * unit_val
            target_val = safety_val + strategic_val   # sum = minimum target stock

            periods_data: Dict[str, Dict[str, float]] = {}
            mat_total_overstock = 0.0
            mat_total_inventory = 0.0

            for period in self.periods:
                inventory_value = row.values.get(period, 0.0)
                mat_total_inventory += inventory_value

                if inventory_value < target_val:
                    # Below target: all categories are 0 except Under
                    under = inventory_value - target_val   # negative
                    s_safety = 0.0
                    s_strategic = 0.0
                    s_normal = 0.0
                    s_overstock = 0.0
                else:
                    under = 0.0
                    excess = inventory_value - target_val
                    # Fill safety and strategic first, then the excess goes to normal/overstock
                    s_safety = safety_val
                    s_strategic = strategic_val
                    s_normal = min(excess, lot_val)
                    s_overstock = max(0.0, excess - lot_val)

                mat_total_overstock += s_overstock
                periods_data[period] = {
                    'under': round(under, 2),
                    'safety': round(s_safety, 2),
                    'strategic': round(s_strategic, 2),
                    'normal': round(s_normal, 2),
                    'overstock': round(s_overstock, 2),
                    'inventory': round(inventory_value, 2),
                }

            mat = self.data.materials.get(mat_num)
            per_material.append({
                'material_number': mat_num,
                'material_name': mat.name if mat else '',
                'product_family': mat.product_family if mat else '',
                'unit_value': round(unit_val, 4),
                'total_overstock': round(mat_total_overstock, 2),
                'total_inventory': round(mat_total_inventory, 2),
                'periods': periods_data,
            })

        # Global totals per period
        period_totals: Dict[str, Dict[str, float]] = {
            p: {'under': 0.0, 'safety': 0.0, 'strategic': 0.0, 'normal': 0.0,
                'overstock': 0.0, 'inventory': 0.0}
            for p in self.periods
        }
        total_overstock = 0.0
        for m in per_material:
            total_overstock += m['total_overstock']
            for p, vals in m['periods'].items():
                for cat in ('under', 'safety', 'strategic', 'normal', 'overstock', 'inventory'):
                    period_totals[p][cat] = round(period_totals[p][cat] + vals[cat], 2)

        top_10 = sorted(per_material, key=lambda x: x['total_overstock'], reverse=True)[:10]

        return {
            'periods': self.periods,
            'total_overstock': round(total_overstock, 2),
            'period_totals': period_totals,
            'per_material': per_material,
            'top_10_overstocks': top_10,
        }
