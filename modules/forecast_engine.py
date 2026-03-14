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

            # VBA DemandForecast (line 868): positional copy from ForecastStartClmn
            # ForecastStartClmn = ForecastActualStartClmn + ForecastActualsMonths + 1
            # The +1 is a gap column VBA always skips between actuals and forecast.
            all_sorted = sorted(forecast_data.items())
            all_values = [v for _, v in all_sorted]
            config_actuals = self.data.forecast_actuals_months
            start_idx = config_actuals + 1  # +1 gap column matches VBA

            self.results[mat_num] = {}
            for i, period in enumerate(self.periods):
                val_idx = start_idx + i
                if val_idx < len(all_values):
                    self.results[mat_num][period] = all_values[val_idx]
                else:
                    self.results[mat_num][period] = 0.0

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

        # VBA AUX1 (line 874): AVERAGE(ForecastActualStartClmn : ForecastActualStartClmn + ForecastActualsMonths - 1)
        # = positional average of the first months_actuals values in the Forecast sheet
        all_sorted_aux = sorted(forecast_data.items())
        all_values_aux = [v for _, v in all_sorted_aux]

        if self.months_actuals > 0 and all_values_aux:
            actuals_pool = all_values_aux[:self.months_actuals]
            aux_1 = round(sum(actuals_pool) / len(actuals_pool)) if actuals_pool else 0
        else:
            aux_1 = 0

        # Aux 2: AVERAGE of ForecastMonths values starting at config_actuals + 1
        # VBA: ForecastStartClmn = ForecastActualStartClmn + ForecastActualsMonths + 1
        #      AUX2 = AVERAGE(ForecastStartClmn : ForecastStartClmn + ForecastMonths - 1)
        config_actuals = self.data.forecast_actuals_months
        start_idx = config_actuals + 1   # +1 gap column VBA always skips
        end_idx = start_idx + self.months_forecast
        all_sorted = sorted(forecast_data.items())
        all_values = [v for _, v in all_sorted]
        start_idx = min(start_idx, len(all_values))
        end_idx = min(end_idx, len(all_values))
        pool = all_values[start_idx:end_idx]
        aux_2 = round(sum(pool) / len(pool)) if pool else 0

        return str(aux_1), str(aux_2)

    def get_forecast(self, material: str, period: str) -> float:
        return self.results.get(material, {}).get(period, 0.0)

    def get_all_forecasts(self) -> Dict[str, Dict[str, float]]:
        return self.results
