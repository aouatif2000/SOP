"""
S&OP Planning Engine - Forecast Engine
Calculates Line 01: Demand Forecast
"""

from typing import Dict, List
from modules.models import PlanningRow, LineType
from modules.data_loader import DataLoader


class ForecastEngine:
    """
    Generates Line 01: Demand Forecast
    
    Logic: Copy forecast values from Forecast sheet for finished products.
    """
    
    def __init__(self, data: DataLoader):
        self.data = data
        self.periods = data.periods
        self.results: Dict[str, Dict[str, float]] = {}  # material -> {period: value}
        self.rows: List[PlanningRow] = []
    
    def calculate(self) -> List[PlanningRow]:
        """Calculate demand forecast for all materials with forecasts."""
        print("  [01] Calculating Demand Forecast...")
        
        for mat_num, forecast_data in self.data.forecasts.items():
            material = self.data.materials.get(mat_num)
            
            # Store in results
            self.results[mat_num] = {}
            for period in self.periods:
                self.results[mat_num][period] = forecast_data.get(period, 0.0)
            
            # Create planning row
            row = PlanningRow(
                material_number=mat_num,
                material_name=material.name if material else '',
                product_type=material.product_type.value if material else '',
                product_family=material.product_family if material else '',
                spc_product=material.spc_product if material else '',
                product_cluster=material.product_cluster if material else '',
                product_name=material.product_name if material else '',
                line_type=LineType.DEMAND_FORECAST.value,
                values=self.results[mat_num].copy()
            )
            self.rows.append(row)
        
        print(f"       → {len(self.rows)} materials with forecast")
        return self.rows
    
    def get_forecast(self, material: str, period: str) -> float:
        """Get forecast for a specific material and period."""
        return self.results.get(material, {}).get(period, 0.0)
    
    def get_all_forecasts(self) -> Dict[str, Dict[str, float]]:
        """Get all forecast data."""
        return self.results
