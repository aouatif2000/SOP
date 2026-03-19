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

            # VBA DemandForecast: copies Forecast sheet column ForecastStartClmn+i
            # to Planning column PlanningStartForecast+i.
            # VBA uses positional +1 to skip the gap column that sits between actuals
            # and forecast in the Excel sheet.  Python's _load_forecasts already
            # excludes that gap column (it only reads date-formatted columns), so the
            # gap is absent from forecast_data entirely.  Using a direct period key
            # lookup is therefore equivalent to what VBA does — no positional offset
            # needed — and is also robust to any planning-month shift.
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
        VBA Logic:
        AUX1 = AVERAGE of the first ForecastActualsMonths columns in the Forecast sheet,
               starting from column ForecastActualStartClmn (position 0 of date columns).
               This is purely positional — VBA takes the first N date-valued columns
               regardless of their period labels.
        AUX2 = AVERAGE of ForecastMonths columns starting at
               ForecastActualStartClmn + ForecastActualsMonths + 1.
               The +1 is the VBA gap-skip offset (PlanningStartForecastClmn formula).
               This is also positional — it is NOT derived from data.periods so it
               stays anchored even when the UI shifts the planning_month.
        """
        if not self.periods:
            return "0", "0"

        all_sorted_aux = sorted(forecast_data.items())
        all_values_aux = [v for _, v in all_sorted_aux]

        # AUX1: positional average of first months_actuals entries (leftmost date columns).
        if self.months_actuals > 0 and all_values_aux:
            actuals_pool = all_values_aux[:self.months_actuals]
            aux_1 = round(sum(actuals_pool) / len(actuals_pool)) if actuals_pool else 0
        else:
            aux_1 = 0

        # AUX2: positional average of months_forecast entries starting at
        # months_actuals + 1.  The +1 mirrors VBA's PlanningStartForecastClmn offset
        # (ForecastActualStartClmn + ForecastActualsMonths + 1), which skips one
        # position between the actuals block and the forecast block.
        aux2_start = self.months_actuals + 1
        aux2_pool = all_values_aux[aux2_start : aux2_start + self.months_forecast]
        aux_2 = round(sum(aux2_pool) / len(aux2_pool)) if aux2_pool else 0

        return str(aux_1), str(aux_2)

    def get_forecast(self, material: str, period: str) -> float:
        return self.results.get(material, {}).get(period, 0.0)

    def get_all_forecasts(self) -> Dict[str, Dict[str, float]]:
        return self.results
