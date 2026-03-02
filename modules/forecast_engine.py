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
    def __init__(self, data: DataLoader, months_actuals: int = 12, months_forecast: int = 12):
        self.data = data
        self.periods = data.periods
        self.months_actuals = months_actuals
        self.months_forecast = months_forecast
        self.results: Dict[str, Dict[str, float]] = {}
        self.rows: List[PlanningRow] = []

    def calculate(self) -> List[PlanningRow]:
        print("  [01] Calculating Demand Forecast...")

        for mat_num, forecast_data in self.data.forecasts.items():
            material = self.data.materials.get(mat_num)
            if not material:
                continue  # Skip materials not in material master

            self.results[mat_num] = {}
            for period in self.periods:
                self.results[mat_num][period] = forecast_data.get(period, 0.0)

            aux_1, aux_2 = self._calculate_aux_columns(mat_num, forecast_data)

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

        print(f"       -> {len(self.rows)} materials with forecast")
        return self.rows

    def _calculate_aux_columns(self, mat_num: str, forecast_data: Dict[str, float]) -> tuple:
        """
        Aux 1: SUM of historical actuals / months_actuals (fixed divisor)
               Excludes the month immediately before planning (may be partial)
        Aux 2: Average demand over FORECAST (planning period months)
        """
        first_planning_period = min(self.periods) if self.periods else None
        if not first_planning_period:
            return "0", "0"

        historical_values = []
        forecast_values = []

        for period, value in sorted(forecast_data.items()):
            if period < first_planning_period:
                historical_values.append(value)
            elif period in self.periods:
                forecast_values.append(value)

        # Aux 1: SUM of historical / months_actuals (fixed divisor)
        # Exclude the last historical month (immediately before planning)
        if historical_values and self.months_actuals > 0:
            actuals_pool = historical_values[:-1] if len(historical_values) > 1 else historical_values
            total = sum(actuals_pool)
            aux_1 = round(total / self.months_actuals, 2)
        else:
            aux_1 = 0.0

        # Aux 2: SUM of forecast / count
        if forecast_values:
            total = sum(forecast_values)
            aux_2 = round(total / len(forecast_values), 2)
        else:
            aux_2 = 0.0

        return str(aux_1), str(aux_2)

    def get_forecast(self, material: str, period: str) -> float:
        return self.results.get(material, {}).get(period, 0.0)

    def get_all_forecasts(self) -> Dict[str, Dict[str, float]]:
        return self.results
