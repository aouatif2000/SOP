"""
S&OP Planning Engine - Forecast Engine
Calculates Line 01: Demand Forecast

Aux Column 1: Average demand over actuals period (HISTORICAL - before planning month)
Aux Column 2: Average demand over forecast period (FUTURE - planning horizon)
"""

from typing import Dict, List
from modules.models import PlanningRow, LineType
from modules.data_loader import DataLoader


class ForecastEngine:
    """
    Generates Line 01: Demand Forecast
    
    Logic: Copy forecast values from Forecast sheet for finished products.
    
    Aux Columns (from Procedure document):
    - Aux 1: Average demand over HISTORICAL actuals (months before planning)
    - Aux 2: Average demand over FORECAST horizon (planning months)
    """
    
    def __init__(self, data: DataLoader, months_actuals: int = 12, months_forecast: int = 12):
        self.data = data
        self.periods = data.periods  # Planning periods (e.g., 2025-12 to 2026-11)
        self.months_actuals = months_actuals  # How many historical months to average
        self.months_forecast = months_forecast  # Planning horizon
        self.results: Dict[str, Dict[str, float]] = {}  # material -> {period: value}
        self.rows: List[PlanningRow] = []
    
    def calculate(self) -> List[PlanningRow]:
        """Calculate demand forecast for all materials with forecasts."""
        print("  [01] Calculating Demand Forecast...")
        print(f"       → Actuals months: {self.months_actuals}, Forecast months: {self.months_forecast}")
        
        for mat_num, forecast_data in self.data.forecasts.items():
            material = self.data.materials.get(mat_num)
            
            # Store in results (only planning periods)
            self.results[mat_num] = {}
            for period in self.periods:
                self.results[mat_num][period] = forecast_data.get(period, 0.0)
            
            # Calculate Aux columns using ALL available data (including historical)
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
        
        Aux 1: SUM of historical actuals / months_actuals (always divide by fixed months)
               Note: Excludes the month immediately before planning (may be partial)
        Aux 2: Average demand over FORECAST (planning period months)
        
        Returns: (aux_1, aux_2) as formatted strings
        """
        # Get the first planning period to determine cutoff
        first_planning_period = min(self.periods) if self.periods else None
        
        if not first_planning_period:
            return "0", "0"
        
        # Separate historical (before planning) from forecast (planning period)
        historical_values = []
        forecast_values = []
        
        for period, value in sorted(forecast_data.items()):
            if period < first_planning_period:
                # This is a historical period (before planning starts)
                historical_values.append(value)
            elif period in self.periods:
                # This is a planning period
                forecast_values.append(value)
            # Periods after planning period but not in planning are ignored
        
        # Aux 1: SUM of historical / months_actuals (fixed divisor)
        # Exclude the last historical month (immediately before planning - may be partial)
        if historical_values and self.months_actuals > 0:
            # Remove the last month (immediately before planning)
            actuals_pool = historical_values[:-1] if len(historical_values) > 1 else historical_values
            # Sum all available values, divide by months_actuals (fixed)
            total = sum(actuals_pool)
            aux_1 = round(total / self.months_actuals, 2)
        else:
            aux_1 = 0.0
        
        # Aux 2: SUM of forecast / months_forecast (fixed divisor)
        if forecast_values:
            total = sum(forecast_values)
            aux_2 = round(total / len(forecast_values), 2)  # Divide by actual count for forecast
        else:
            aux_2 = 0.0
        
        return str(aux_1), str(aux_2)
    
    def get_forecast(self, material: str, period: str) -> float:
        """Get forecast for a specific material and period."""
        return self.results.get(material, {}).get(period, 0.0)
    
    def get_all_forecasts(self) -> Dict[str, Dict[str, float]]:
        """Get all forecast data."""
        return self.results
