# S&OP Planning Engine — Test Suite

## Overview

This test suite provides unit-level coverage for the S&OP (Sales & Operations Planning) engine. It validates the correctness of every major calculation module — from demand forecasting and BOM explosion through inventory planning, capacity utilisation, financial consolidation, and month-over-month comparison.

All tests run against isolated, in-memory mock data so they execute quickly and deterministically without requiring an Excel upload file or database connection. The mocks replicate the structure of the `DataLoader` object that the real pipeline produces, ensuring the tests exercise the same code paths as production.

---

## Prerequisites

| Requirement | Version |
|---|---|
| Python | 3.11+ |
| pytest | 9.x |
| pandas | 2.x |
| numpy | 1.x |

### Install dependencies

```bash
# From the project root
pip install -r requirements.txt
```

If you are using the project virtual environment:

```bash
# Windows
venv\Scripts\activate
pip install -r requirements.txt

# Linux / macOS
source venv/bin/activate
pip install -r requirements.txt
```

---

## Running the tests

```bash
# Run all tests with verbose output
python -m pytest tests/ -v

# Run a single test file
python -m pytest tests/test_bom_engine.py -v

# Run tests matching a keyword
python -m pytest tests/ -k "truck" -v

# Stop on first failure
python -m pytest tests/ -x --tb=short
```

---

## Test files

### `test_models.py`

**Module under test:** `modules/models.py`

Validates the core data-model classes and enumerations used across the entire engine.

| Area | Scenarios |
|---|---|
| `ProductType` enum | String-to-enum parsing for Raw Material, Bulk Product, Packaged Product, Packed material (alias), Packaging Goods, unknown / empty strings |
| `Material` properties | `is_purchased` for raw materials and packaging goods; `is_produced` for bulk and packaged products; `OTHER` type returns `False` for both |
| `RoutingItem` | `time_per_unit` calculation (`standard_time / base_quantity`); zero `base_quantity` guard |
| `Machine` | `get_availability` with default and custom per-period values; `get_available_hours` combining shift hours, OEE, and availability |
| `PlanningConfig` | `get_periods()` month generation including year-boundary wrap (e.g. Nov 2025 → Feb 2026) |
| `PlanningRow` | `get_value` / `set_value` round-trip; `to_dict` serialisation |
| `SalesPriceItem` | `price_per_unit` derived property |

**Mock data:** Plain constructors with hard-coded values; no external dependencies.

---

### `test_forecast_engine.py`

**Module under test:** `modules/forecast_engine.py`

Covers Line 01 (Demand Forecast) generation and auxiliary column calculations.

| Area | Scenarios |
|---|---|
| Forecast placement | Values are anchored to `forecast_first_period` via `_offset_period` and mapped into the correct planning periods |
| Missing data | Periods without a corresponding forecast entry default to `0.0` |
| Material filtering | Materials absent from the material master are skipped |
| `aux1` / `aux2` | Average of the 12 actuals months and the forecast window, respectively |
| `_offset_period` | Static helper correctly advances months including year rollover |

**Mock data:** `MagicMock` mimicking `DataLoader` with explicit forecast dictionaries keyed by `YYYY-MM` strings.

---

### `test_bom_engine.py`

**Module under test:** `modules/bom_engine.py`

Validates BOM explosion logic and the creation of dependent demand / dependent requirements rows.

| Area | Scenarios |
|---|---|
| `compute_dependent_requirements` | Single-parent explosion with `qty_per` multiplication; no-children case returning empty dict |
| `create_dependent_demand_rows` (Line 02) | One row per parent, `aux_column` set to parent material number; multi-parent scenario producing two rows |
| `create_dependent_requirements_rows` (Line 08) | One row per child, `aux_column` = child, `aux_2_column` = `qty_per` |
| Two-level cascading | P1 → C1 → GC1 chain: production of 100 at level 0 cascades to 200 at level 1 and 600 at level 2 |

**Mock data:** Explicit `BOMItem` lists with known `qty_per` and `bom_header_quantity` values. Parent–child hierarchy managed via `bom_levels` dict.

---

### `test_calculation_engine.py`

**Module under test:** `modules/calculation_engine.py`

Tests the legacy `CalculationEngine` that predates the modular engine pipeline.

| Area | Scenarios |
|---|---|
| `calculate_demand_forecast` | Returns only produced (non-purchased) materials |
| `calculate_dependent_demand` | BOM-driven multiplication via `qty_per`; empty BOM returns zero |
| `calculate_total_demand` | Additive union of demand forecast and dependent demand across all materials |

**Mock data:** In-line `Material`, `BOMItem`, and `PlanningConfig` objects constructed directly.

---

### `test_inventory_engine.py`

**Module under test:** `modules/inventory_engine.py`

Covers Lines 03–07 of the planning output: total demand, target stock, production/purchase plan, and inventory balance.

| Area | Scenarios |
|---|---|
| `ceiling_multiple` | Basic ceiling (7 → 9 with multiple 3), exact fit, zero value, zero multiple, negative value |
| Produced material | Total demand equals forecast; production plan covers need; BOM header quantity ceiling (50 → 60 with header 30); running inventory balance stays non-negative |
| Purchased material | Purchase receipt generated (not production plan); MOQ ceiling (75 → 100 with MOQ 50); purchase plan shifted by lead time |
| Total demand aggregation | `forecast + dependent_demand_agg` produces correct sums |
| Row line types | Output rows contain `TOTAL_DEMAND`, `INVENTORY`, `PRODUCTION_PLAN`, `MIN_TARGET_STOCK` |

**Mock data:** `MagicMock` with helper lambdas for `get_production_ceiling`, `get_purchase_moq`, `get_lead_time`, etc. Safety stock and lot size set via `SafetyStockConfig`.

---

### `test_capacity_engine.py`

**Module under test:** `modules/capacity_engine.py`

Validates Lines 07, 09–12: capacity utilisation, available capacity, utilisation rate, shift availability, and FTE requirements.

| Area | Scenarios |
|---|---|
| Material-level cap util (Line 07) | `production_qty * (std_time / base_quantity)`; `aux_2_column` stores the rate |
| OEE adjustment | Machine-level hours = `raw_hours / OEE` |
| Utilisation rate (Line 10) | `cap_util / (shift_hours × availability)` |
| Truck capacity utilisation | `sum(demand / ton_per_truck × time_per_truck)` for `ZZZZ_TRUCK01` |
| Truck FTE | `truck_hours × fte_coeff / (fte_hours_per_year / 12)` |
| Group FTE | Group-level FTE with `fte_coeff` multiplier |
| Shift availability (Line 11) | Correct shift-hours value and `aux_column` label |

**Mock data:** `Machine`, `MachineGroup`, `RoutingItem` objects with explicit OEE, shift system, and availability-by-period values. Truck tests supply `all_line_data` dicts simulating the output of earlier pipeline steps.

**Assumptions:** Shift hours use the constant `SHIFT_HOURS` map from `models.py` (520 h for 3-shift, 347 h for 2-shift). FTE hours per year default to 1492.

---

### `test_value_planning_engine.py`

**Module under test:** `modules/value_planning_engine.py`

Covers the financial consolidation layer that converts volume-based planning rows into monetary values.

| Area | Scenarios |
|---|---|
| Revenue conversion | `demand_volume × (ex_works_revenue / volume_2025)` |
| Inventory value | `volume × (Total Value / Total Stock)`; starting stock sourced from `Value Unrestricted` |
| Consolidation row count | Exactly 20 consolidation rows produced |
| TURNOVER | Demand × unit price per period |
| COST OF GOODS | Sum of raw material, machine hours, direct FTE, indirect FTE, and overhead costs |
| GROSS MARGIN | `TURNOVER − COGS` |
| EBITDA | `GROSS MARGIN − SG&A` |
| EBIT | `EBITDA − D&A` |
| ROCE | `EBIT × 12 / Capital Investment` |
| Truck FTE value | `CapUtil × TimePerTruck / TonPerTruck × fte_cost_per_month` |

**Mock data:** `SalesPriceItem`, `RawMaterialCost`, `MachineCost` with known unit values. `ValuationParameters` sets fixed monthly costs (direct FTE 5 000, indirect 3 000, overhead 2 000, SG&A 1 500, D&A 12 000/yr).

---

### `test_inventory_quality_engine.py`

**Module under test:** `modules/inventory_quality_engine.py`

Validates the five-way inventory quality categorisation and Top 10 overstock ranking.

| Area | Scenarios |
|---|---|
| Overstock | `inventory − (safety + strategic + lot)` when above threshold; zero otherwise |
| Under-stock | Negative value when inventory is below target (`safety + strategic`) |
| Quality invariant | `under + safety + strategic + normal + overstock ≡ inventory` for every period |
| Top 10 ordering | 15 materials ranked by total overstock descending; top entry is the highest overstock material |
| Starting stock | Starting-stock period included in `overstock_by_period` output |
| Period totals | Global per-period sums verified as the aggregate of individual material values |

**Mock data:** `SafetyStockConfig` with explicit `safety_stock`, `lot_size`, and `strategic_stock`. Inventory rows carry `aux_column = "5.0"` as the unit value used for monetary conversion.

---

### `test_mom_comparison_engine.py`

**Module under test:** `modules/mom_comparison_engine.py`

Tests the month-over-month delta and scatter-chart data generation used to compare consecutive planning cycles.

| Area | Scenarios |
|---|---|
| Basic delta | `current − previous` per period per material |
| Delta percentage | `(current − previous) / previous × 100` |
| Zero previous | Produces `inf` when previous value is zero |
| Common periods only | Non-overlapping periods are excluded |
| Common materials only | Materials present in only one cycle are excluded |
| Empty input | Returns an empty DataFrame |
| Scatter colours | Green (`C6EFCE`) for positive-positive, orange (`FFC896`) for sign change |

**Mock data:** `pandas.DataFrame` built by a helper that simulates the pivoted planning output with `Material number`, `Line type`, and period columns.

---

### `test_planning_engine.py`

**Module under test:** `modules/planning_engine.py`

Unit-level checks for the orchestration layer and shared data structures.

| Area | Scenarios |
|---|---|
| `LineType` completeness | All 15 expected line-type strings exist in the enum; count matches |
| Total demand logic | `Line 03 = Line 01 + sum(Line 02)` verified arithmetically |
| `PlanningRow.to_dict` | Round-trip serialisation preserves all fields including `starting_stock`, `aux_column`, and period values |

**Note:** Full integration tests that load the Excel upload file and run the 6-step pipeline are not included here — they require the actual data file and are executed separately.

---

## Adding new tests

1. **Create a new file** in `tests/` following the naming convention `test_<module_name>.py`.
2. **Mirror the mock pattern** used in the existing files: build a `_make_data()` helper that returns a `MagicMock` with only the attributes your module under test reads.
3. **Isolate one responsibility per test class.** Group related assertions under a descriptive class name (e.g. `TestOverstockCalculation`).
4. **Use `pytest.approx`** for all floating-point comparisons to avoid rounding failures.
5. **Run the full suite** before committing:

   ```bash
   python -m pytest tests/ -v --tb=short
   ```

6. **Keep mocks minimal.** Only stub the `DataLoader` attributes that the engine actually accesses. This keeps tests fast and makes failures easy to diagnose.

### Naming guidelines

| Convention | Example |
|---|---|
| File | `test_<module>.py` |
| Class | `TestFeatureOrComponent` |
| Method | `test_<behaviour_under_test>` |
| Helper | `_make_data(...)`, `_make_row(...)` |

---

## Test coverage summary

| Test file | Module | Tests | Key areas |
|---|---|---|---|
| `test_models.py` | `models.py` | 16 | Enums, Material properties, Machine hours, PlanningConfig periods |
| `test_forecast_engine.py` | `forecast_engine.py` | 5 | Forecast placement, aux averages, period offset |
| `test_bom_engine.py` | `bom_engine.py` | 6 | BOM explosion, dependent demand/requirements rows, 2-level cascading |
| `test_calculation_engine.py` | `calculation_engine.py` | 5 | Legacy demand forecast, dependent demand, total demand |
| `test_inventory_engine.py` | `inventory_engine.py` | 10 | Ceiling multiple, production/purchase plans, MOQ, lead time, inventory balance |
| `test_capacity_engine.py` | `capacity_engine.py` | 7 | Cap util, OEE, utilisation rate, truck FTE, group FTE, shift availability |
| `test_value_planning_engine.py` | `value_planning_engine.py` | 10 | Revenue, COGS, EBITDA, EBIT, ROCE, inventory value, truck FTE value |
| `test_inventory_quality_engine.py` | `inventory_quality_engine.py` | 6 | Overstock/under-stock, quality invariant, Top 10, starting stock |
| `test_mom_comparison_engine.py` | `mom_comparison_engine.py` | 8 | Delta, delta %, common-period/material filtering, scatter chart |
| `test_planning_engine.py` | `planning_engine.py` | 5 | LineType completeness, total demand formula, row serialisation |
| **Total** | | **78** | |
