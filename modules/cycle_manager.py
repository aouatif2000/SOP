"""
S&OP Planning Engine - Cycle Manager
Saves and loads previous cycle planning results so that
Month-over-Month comparisons can be computed on the next run.

Storage format: Parquet (compact, typed, fast).
"""

import os
import pandas as pd
from pathlib import Path


class CycleManager:
    """Persist and retrieve the previous planning cycle's DataFrame."""

    DEFAULT_FILENAME = "previous_cycle_values.parquet"

    def __init__(self, storage_dir: str):
        self.storage_dir = Path(storage_dir)
        self._path = self.storage_dir / self.DEFAULT_FILENAME

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def has_previous_cycle(self) -> bool:
        """Return True if a previous-cycle snapshot exists on disk."""
        return self._path.is_file()

    def load_previous_cycle(self) -> pd.DataFrame:
        """Load the previous cycle DataFrame.

        Returns an empty DataFrame if the file is missing or corrupt.
        """
        if not self.has_previous_cycle():
            return pd.DataFrame()
        try:
            return pd.read_parquet(self._path)
        except Exception as exc:
            print(f"  Warning: could not read previous cycle ({exc}); starting fresh.")
            return pd.DataFrame()

    def save_current_as_previous(self, df: pd.DataFrame) -> None:
        """Overwrite the previous-cycle snapshot with the current results.

        Creates the storage directory if it does not exist.
        """
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        df.to_parquet(self._path, index=False)
        print(f"  Previous-cycle snapshot saved → {self._path}")

    def clear(self) -> None:
        """Remove the stored snapshot (useful for testing)."""
        if self._path.is_file():
            self._path.unlink()
