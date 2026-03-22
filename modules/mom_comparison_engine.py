"""
S&OP Planning Engine - Month-over-Month Comparison
Compares the current cycle's Inventory rows (04. Inventory) against
the previous cycle to produce a delta table and scatter-chart data.

VBA reference: CreateMoMComparison / CreateMoMScatterChart_color_markers
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple


class MoMComparisonEngine:
    """Calculate month-over-month deltas between two planning cycles."""

    INVENTORY_LINE = "04. Inventory"

    def __init__(self, current_df: pd.DataFrame, previous_df: pd.DataFrame):
        """
        Parameters
        ----------
        current_df : DataFrame
            Full planning output of the *current* cycle (from PlanningEngine.to_dataframe()).
        previous_df : DataFrame
            Full planning output of the *previous* cycle (loaded by CycleManager).
        """
        self.current_df = current_df
        self.previous_df = previous_df

    # ------------------------------------------------------------------
    # Main comparison
    # ------------------------------------------------------------------

    def calculate(self) -> pd.DataFrame:
        """Return a DataFrame with columns:

        Material number | Material name | Product type | Period |
        Current Inventory | Previous Inventory | Delta | Delta %

        Only materials present in both cycles are compared.
        Returns an empty DataFrame if either input is empty.
        """
        if self.current_df.empty or self.previous_df.empty:
            return pd.DataFrame()

        cur_inv = self._extract_inventory(self.current_df)
        prev_inv = self._extract_inventory(self.previous_df)

        if cur_inv.empty or prev_inv.empty:
            return pd.DataFrame()

        # Identify overlapping period columns
        cur_periods = self._period_columns(cur_inv)
        prev_periods = self._period_columns(prev_inv)
        common_periods = sorted(set(cur_periods) & set(prev_periods))

        if not common_periods:
            return pd.DataFrame()

        # Melt to long form for easy merge
        id_cols = ["Material number", "Material name", "Product type"]
        cur_long = cur_inv[id_cols + common_periods].melt(
            id_vars=id_cols, var_name="Period", value_name="Current Inventory"
        )
        prev_long = prev_inv[id_cols + common_periods].melt(
            id_vars=id_cols, var_name="Period", value_name="Previous Inventory"
        )

        merged = cur_long.merge(prev_long, on=id_cols + ["Period"], how="inner")
        merged["Delta"] = merged["Current Inventory"] - merged["Previous Inventory"]
        merged["Delta %"] = np.where(
            merged["Previous Inventory"] != 0,
            merged["Delta"] / merged["Previous Inventory"] * 100,
            np.where(merged["Delta"] != 0, np.inf, 0.0),
        )
        merged = merged.sort_values(["Material number", "Period"]).reset_index(drop=True)
        return merged

    # ------------------------------------------------------------------
    # Scatter-chart helpers
    # ------------------------------------------------------------------

    def create_scatter_data(self) -> Dict:
        """Return data suitable for an openpyxl scatter chart.

        Returns dict with keys:
            materials  – list[str]        material numbers
            current    – list[float]      summed current inventory per material
            previous   – list[float]      summed previous inventory per material
            colors     – list[str]        hex colour per material (quadrant-based)

        Quadrant colouring (VBA CreateMoMScatterChart_color_markers):
            Q1 (cur>0, prev<0) → red    FFC7CE
            Q2 (cur<0, prev>0) → orange FFC896
            Q3 (cur<0, prev<0) → orange FFC896
            Q4 (cur>0, prev>0) → green  C6EFCE
        """
        comparison = self.calculate()
        if comparison.empty:
            return {"materials": [], "current": [], "previous": [], "colors": []}

        agg = comparison.groupby("Material number").agg(
            current=("Current Inventory", "sum"),
            previous=("Previous Inventory", "sum"),
        ).reset_index()

        colors = []
        for _, row in agg.iterrows():
            c, p = row["current"], row["previous"]
            if c >= 0 and p < 0:
                colors.append("FFC7CE")   # red – Q1
            elif c >= 0 and p >= 0:
                colors.append("C6EFCE")   # green – Q4
            else:
                colors.append("FFC896")   # orange – Q2/Q3

        return {
            "materials": agg["Material number"].tolist(),
            "current": agg["current"].tolist(),
            "previous": agg["previous"].tolist(),
            "colors": colors,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_inventory(df: pd.DataFrame) -> pd.DataFrame:
        """Filter to 04. Inventory rows only."""
        if "Line type" not in df.columns:
            return pd.DataFrame()
        return df[df["Line type"] == MoMComparisonEngine.INVENTORY_LINE].copy()

    @staticmethod
    def _period_columns(df: pd.DataFrame) -> List[str]:
        """Return column names that look like YYYY-MM period headers."""
        periods = []
        for col in df.columns:
            col_str = str(col)
            if len(col_str) == 7 and col_str[4] == "-":
                try:
                    int(col_str[:4])
                    int(col_str[5:])
                    periods.append(col)
                except ValueError:
                    pass
        return sorted(periods)
