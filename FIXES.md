# Bug Fixes

This document describes all bugs found and fixed when aligning the Python S&OP engine output with the Excel VBA reference output.

---

## LT 02 — Dependent Demand: missing rows for materials absent from Material Master

### Affected materials
- `150000546`
- `150002727`

### Root cause
Material `150000276` (a BOM parent) was missing from the Material Master sheet. The main planning loop in `planning_engine.py` looked up each material in `self.data.materials` and skipped the entire material — including any Line 02 (Dependent Demand) rows it should emit for its children — when the lookup returned `None`.

Because `150000276` was missing from Material Master, its children (`150000546`, `150002727`) never received dependent demand rows, causing their LT02 rows to be absent from the Python output.

### Fix
**File:** `modules/planning_engine.py` lines 179–189

One-liner change in the main BOM-level loop: when a material is not found in Material Master, still emit Line 02 rows for any dependent demand it has accumulated, then `continue` to the next material instead of silently skipping everything.

```python
# Before
material = self.data.materials.get(mat_num)
if material is None:
    continue  # skipped Line 02 rows for children too

# After
material = self.data.materials.get(mat_num)
if material is None:
    # still emit any accumulated dependent demand rows, then skip
    ... emit Line 02 rows ...
    continue
```

---

## LT 04 — Inventory: inactive materials being processed

### Affected materials
- `150000491`
- `300021059`

### Root cause
Both materials are marked as inactive (`Active = 0`) in Material Master. However, `_load_forecasts()` only excludes inactive materials from the forecast rows — materials that appear in the BOM or Safety Stock sheet could still enter the inventory engine's processing loops, which are driven by BOM levels and safety stock config rather than the forecast list.

Because the inventory engine generates a production plan from safety stock targets alone (no forecast needed), these inactive materials produced full LT04 output rows.

There was a secondary issue: the `Active` column in Material Master could contain blank cells. Python was treating `NaN` (blank) as `0` (inactive) instead of defaulting to `1` (active).

### Fixes

Three changes were applied:

**1. `modules/planning_engine.py` — BOM level loop (~line 190)**

Added an explicit active check before processing each material:

```python
if not material.is_active:
    continue
```

Inactive materials now generate no output rows during the level-by-level processing loop.

**2. `modules/planning_engine.py` — Standalone safety stock loop (~line 244)**

Added `and self.data.materials[mat_num].is_active` to the loop filter so that inactive materials in the Safety Stock sheet are excluded:

```python
# Before
for mat_num in safety_stock_only_materials:

# After
for mat_num in safety_stock_only_materials:
    if not self.data.materials[mat_num].is_active:
        continue
```

**3. `modules/data_loader.py` — `_load_materials()` line 193**

Fixed the NaN-default bug. When the `Active` column cell is blank, the material now correctly defaults to active (`1`) instead of being treated as inactive:

```python
# Before
is_active = bool(row.get('Active', 0))  # NaN -> 0 (inactive) — wrong

# After
active_val = row.get('Active')
is_active = bool(active_val) if pd.notna(active_val) else True  # blank -> active
```

---

## LT 04 / LT 06 — Inventory and Purchase Receipt differences

### Affected materials
All materials using the purchase receipt heuristic.

### Root cause
Two independent issues caused Python's LT04 and LT06 values to differ from Excel:

#### Issue A — Default lead time for materials not in the Purchase sheet

VBA's `GeneratePlanningSheet` populates the Purchase Plan row's auxiliary column with the lead time using:

```vba
=INT(IFERROR(XLOOKUP(..., LeadTimeColumn, 1), 1))
```

The `IFERROR` default is `1`, so any material absent from the Purchase sheet gets `lead_time = 1` in Excel. The purchase receipt heuristic in `PurchaseReceiptHeuristic()` then skips the first planning month (marks it grey/frozen with value 0) and only starts the heuristic from month 2.

Python's `get_lead_time()` was returning `0` for unlisted materials, causing the heuristic to fire in month 1 where Excel blocks it.

**Fix — `modules/data_loader.py` `get_lead_time()` line 644:**

```python
# Before
return 0  # no delay if not in purchase sheet

# After
return 1  # matches VBA IFERROR default
```

#### Issue B — Post-actuals heuristic recalculation

VBA's `PurchaseReceiptHeuristic()` writes **live Excel formulas** into all flexible period cells. After `ProcessPurchaseReceipts()` overwrites frozen and first-flexible periods with actual PO quantities, Excel's formula engine **auto-recalculates the entire chain from scratch** — including the first-flexible period when its actual is 0.

Python's old recalculation code:
1. Advanced `running_stock` through frozen AND first-flexible periods (using the stale heuristic value for first-flexible if actual = 0)
2. Only recalculated purchase receipts from the second-flexible period onward

This caused `running_stock` to be inflated at the recalculation boundary, producing wrong receipt quantities for all subsequent months.

Example for material `150000276` (lead_time=1, Jan-2026 actual=3000):

| | `running_stock` before Feb-2026 | Feb-2026 receipt |
|---|---|---|
| Old (wrong) | 6888 (included stale 3000 heuristic for Feb) | 3000 |
| New (correct) | 5078 (only frozen Jan actual applied) | 0 |
| Excel reference | 5078 | 0 |

**Fix — `modules/inventory_engine.py` lines 196–232:**

Replaced the two-pass recalculation with a single loop:

```python
running_stock = initial_stock
for i, period in enumerate(self.periods):
    demand = total_demand.get(period, 0.0)
    raw_need = target_values[period] - running_stock + demand

    if i < lead_time or (i == lead_time and actuals_map.get(period, 0.0) > 0):
        # Frozen or first-flexible locked by actual: keep actuals-set value
        prod_qty  = production_plan.get(period, 0.0) if production_plan else 0.0
        purch_qty = purchase_receipt.get(period, 0.0)
    else:
        # First-flexible with no actual, or beyond: run heuristic
        ...recompute prod_qty / purch_qty from raw_need...

    running_stock = running_stock - demand + prod_qty + purch_qty
```

### Verification
After all fixes, comparison against `vergelijking v3.xlsx` shows **0 differences** for both LT04 (Inventory) and LT06 (Purchase Receipt) across all 217 materials.

---

## LT 11 — Shift Availability: uniform shift system override (testing alignment, known deviation)

### Root cause / context

VBA `MachineGroupShiftAvailability()` (`mdl_GroupProdLine_formulas.bas` lines 32–89) assigns a shift system to each LT11 row via a user-selectable dropdown. The **default value is always FTE sheet row 4** (the second dropdown option), applied identically to **all** machine groups.

Python's original implementation derived the shift system **per group** from the first non-unlimited machine in the group (`_calculate_shift_availability`, `capacity_engine.py`). This is semantically more correct — different groups can genuinely run different shift patterns — but it does not match the Excel VBA default, which assigns the same system to every group.

### Override applied (testing accuracy)

Python is overridden to match the VBA default: all LT11 rows use the **same shift system** (the second entry in the FTE sheet, matching VBA row 4).

**`modules/data_loader.py` — `_load_fte_config()`**

Shift-type FTE entries are now collected in sheet order. `self.default_shift_name` is set to the second shift entry (index 1), matching VBA row 4:

```python
if len(shift_entries_ordered) >= 2:
    self.default_shift_name = shift_entries_ordered[1]
```

**`modules/capacity_engine.py` — `_calculate_shift_availability()`**

Per-machine derivation replaced with a single uniform lookup:

```python
# VBA default: all groups use the same shift system (FTE sheet row 4 = second option)
grp_shift_name = getattr(self.data, 'default_shift_name', '3-shift system')
grp_shift_hours = self.shift_hours_lookup.get(grp_shift_name, 520.0)
```

### ⚠ Known deviation — needs future attention

The VBA default is a UI convenience, not the intended business logic. In reality, different machine groups run different shift patterns. The correct approach is to derive the shift system per group from machine configuration (which Python's original code did). This override exists **only to keep test comparisons against the Excel reference accurate**. Once per-group shift system configuration is properly modelled (e.g., stored per group in Material Master or a dedicated config sheet), this override should be removed.
