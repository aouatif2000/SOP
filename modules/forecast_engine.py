"""
S&OP Planning Engine - Forecast Engine
Calculates Line 01: Demand Forecast

Aux Column 1: Average demand over actuals period (historical)
Aux Column 2: Average demand over forecast period (future)
"""

from typing import Dict, List
from modules.models import PlanningRow, LineType
from modules.data_loader import DataLoader


class ForecastEngine:
    """
    Generates Line 01: Demand Forecast
    
    Logic: Copy forecast values from Forecast sheet for finished products.
    
    Aux Columns (from Procedure document):
    - Aux 1: Average demand in last X months (actuals)
    - Aux 2: Average demand over the forecast horizon
    """
    
    def __init__(self, data: DataLoader, months_actuals: int = 0, months_forecast: int = 12):
        self.data = data
        self.periods = data.periods
        self.months_actuals = months_actuals  # How many months of historical actuals
        self.months_forecast = months_forecast  # Planning horizon
        self.results: Dict[str, Dict[str, float]] = {}  # material -> {period: value}
        self.rows: List[PlanningRow] = []
    
    def calculate(self) -> List[PlanningRow]:
        """Calculate demand forecast for all materials with forecasts."""
        print("  [01] Calculating Demand Forecast...")
        print(f"       → Actuals months: {self.months_actuals}, Forecast months: {self.months_forecast}")
        
        for mat_num, forecast_data in self.data.forecasts.items():
            material = self.data.materials.get(mat_num)
            
            # Store in results
            self.results[mat_num] = {}
            for period in self.periods:
                self.results[mat_num][period] = forecast_data.get(period, 0.0)
            
            # Calculate Aux columns
            aux_1, aux_2 = self._calculate_aux_columns(mat_num, forecast_data)
            
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
                aux_column=aux_1,
                aux_2_column=aux_2,
                values=self.results[mat_num].copy()
            )
            self.rows.append(row)
        
        print(f"       → {len(self.rows)} materials with forecast")
        return self.rows
    
    def _calculate_aux_columns(self, mat_num: str, forecast_data: Dict[str, float]) -> tuple:
        """
        Calculate Aux 1 and Aux 2 for Line 01.
        
        Aux 1: Average demand over actuals period (first X months in data)
        Aux 2: Average demand over forecast period (remaining months)
        
        Returns: (aux_1, aux_2) as formatted strings or floats
        """
        all_values = []
        for period in self.periods:
            val = forecast_data.get(period, 0.0)
            all_values.append(val)
        
        # Aux 1: Average of actuals (first months_actuals periods)
        if self.months_actuals > 0 and len(all_values) >= self.months_actuals:
            actuals_values = all_values[:self.months_actuals]
            non_zero_actuals = [v for v in actuals_values if v > 0]
            if non_zero_actuals:
                aux_1 = round(sum(non_zero_actuals) / len(non_zero_actuals), 2)
            else:
                aux_1 = 0.0
        else:
            # If no actuals specified, use all non-zero values average
            non_zero = [v for v in all_values if v > 0]
            aux_1 = round(sum(non_zero) / len(non_zero), 2) if non_zero else 0.0
        
        # Aux 2: Average of forecast (remaining months after actuals)
        if self.months_actuals > 0 and len(all_values) > self.months_actuals:
            forecast_values = all_values[self.months_actuals:]
            non_zero_forecast = [v for v in forecast_values if v > 0]
            if non_zero_forecast:
                aux_2 = round(sum(non_zero_forecast) / len(non_zero_forecast), 2)
            else:
                aux_2 = 0.0
        else:
            # If no actuals, Aux 2 = average of all periods
            non_zero = [v for v in all_values if v > 0]
            aux_2 = round(sum(non_zero) / len(non_zero), 2) if non_zero else 0.0
        
        return str(aux_1), str(aux_2)
    
    def get_forecast(self, material: str, period: str) -> float:
        """Get forecast for a specific material and period."""
        return self.results.get(material, {}).get(period, 0.0)
    
    def get_all_forecasts(self) -> Dict[str, Dict[str, float]]:
        """Get all forecast data."""
        return self.results
