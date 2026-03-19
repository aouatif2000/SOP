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
            # (where ForecastStartClmn = ForecastActualStartClmn + ForecastActualsMonths + 1)
            # to Planning column PlanningStartForecast+i.
            # This is POSITIONAL, not key-based. sorted_forecast[anchor + i] is the
            # value for planning period i, regardless of that column's date label.
            # anchor = months_actuals + 1 (same +1 used for Aux2 / starting_stock).
            aux_1, aux_2, all_sorted = self._calculate_aux_columns(mat_num, forecast_data)

            anchor = self.months_actuals + 1
            self.results[mat_num] = {}
            for i, period in enumerate(self.periods):
                idx = anchor + i
                self.results[mat_num][period] = all_sorted[idx][1] if idx < len(all_sorted) else 0.0

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
                # starting_stock intentionally omitted: VBA leaves it blank for Line 01
                values=self.results[mat_num].copy()
            )
            self.rows.append(row)

        print(f"       -> {len(self.rows)} materials with forecast")
        return self.rows

    def _calculate_aux_columns(self, mat_num: str, forecast_data: Dict[str, float]) -> tuple:
        """
        All positional values derived from sorted forecast columns (left→right order):

        AUX1       = AVERAGE of positions 0..months_actuals-1
        AUX2       = AVERAGE of positions anchor..anchor+months_forecast-1
                     where anchor = months_actuals + 1  (VBA's +1 gap-skip offset)
        all_sorted = returned so caller can reuse for positional monthly value fill
        """
        if not self.periods:
            return "0", "0", []

        all_sorted_aux = sorted(forecast_data.items())
        all_values_aux = [v for _, v in all_sorted_aux]

        # AUX1: positional average of first months_actuals entries (leftmost date columns).
        if self.months_actuals > 0 and all_values_aux:
            actuals_pool = all_values_aux[:self.months_actuals]
            aux_1 = round(sum(actuals_pool) / len(actuals_pool)) if actuals_pool else 0
        else:
            aux_1 = 0

        # anchor = months_actuals + 1 (VBA's PlanningStartForecastClmn offset)
        anchor = self.months_actuals + 1
        aux2_pool = all_values_aux[anchor : anchor + self.months_forecast]
        aux_2 = round(sum(aux2_pool) / len(aux2_pool)) if aux2_pool else 0

        return str(aux_1), str(aux_2), all_sorted_aux

    def get_forecast(self, material: str, period: str) -> float:
        return self.results.get(material, {}).get(period, 0.0)

    def get_all_forecasts(self) -> Dict[str, Dict[str, float]]:
        return self.results
