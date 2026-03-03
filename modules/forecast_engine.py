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
        VBA Logic (from PDF):
        AUX1 = IFERROR(AVERAGE(ForecastActualStart : ForecastActualStart + ForecastActualsMonths - 1), 0)
             -> AVERAGE of the actuals range from the forecast sheet
             -> AVERAGE ignores blanks, averages over months_actuals cells
        AUX2 = IFERROR(AVERAGE(PlanningStartForecast : PlanningEndForecast), 0)
             -> AVERAGE of the forecast values in the planning period columns
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

        # Aux 1: AVERAGE of actuals (months_actuals cells from forecast sheet)
        # VBA: AVERAGE(ForecastActualStartClmn : ForecastActualStartClmn + ForecastActualsMonths - 1)
        # AVERAGE in Excel counts zeros, only ignores truly blank (missing) cells
        # Take the first months_actuals values from historical
        if historical_values and self.months_actuals > 0:
            actuals_pool = historical_values[:self.months_actuals]
            aux_1 = round(sum(actuals_pool) / len(actuals_pool), 2) if actuals_pool else 0.0
        else:
            aux_1 = 0.0

        # Aux 2: AVERAGE of values copied from forecast sheet to planning sheet
        # VBA copies ForecastMonths values from forecast sheet starting at:
        #   ForecastStartClmn = PlanningMonthCol - (ForecastActualsMonths - ForecastMonths)
        # This means: start_month = planning_month - actuals_months + forecast_months
        # When forecast_months == actuals_months (default 12), start = planning_month
        # When forecast_months < actuals_months, start moves BEFORE planning month
        # The range includes actuals data from the forecast sheet
        all_sorted = sorted(forecast_data.items())
        all_periods = [p for p, v in all_sorted]
        all_values = [v for p, v in all_sorted]

        if all_periods and self.months_forecast > 0:
            # Find the planning month index in all_periods
            planning_idx = None
            for idx, p in enumerate(all_periods):
                if p == first_planning_period:
                    planning_idx = idx
                    break

            if planning_idx is not None:
                # VBA: ForecastStartClmn offset = -(actuals_months - forecast_months) from planning month
                offset = self.months_actuals - self.months_forecast
                start_idx = planning_idx - offset
                end_idx = start_idx + self.months_forecast

                # Clamp to valid range
                start_idx = max(0, start_idx)
                end_idx = min(len(all_values), end_idx)

                pool = all_values[start_idx:end_idx]
                aux_2 = round(sum(pool) / len(pool), 2) if pool else 0.0
            else:
                aux_2 = round(sum(forecast_values) / len(forecast_values), 2) if forecast_values else 0.0
        else:
            aux_2 = 0.0

        return str(aux_1), str(aux_2)

    def get_forecast(self, material: str, period: str) -> float:
        return self.results.get(material, {}).get(period, 0.0)

    def get_all_forecasts(self) -> Dict[str, Dict[str, float]]:
        return self.results
