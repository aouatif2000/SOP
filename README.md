# SOP

## Architecture

```
sop_macro_engine/
├── main.py                    # Entry point
├── validate.py                # Validation script
├── requirements.txt           # Dependencies
├── modules/
│   ├── models.py              # Data structures
│   ├── data_loader.py         # Reads raw Excel sheets
│   ├── forecast_engine.py     # Line 01: Demand Forecast
│   ├── bom_engine.py          # Lines 02, 08: BOM Explosion
│   ├── inventory_engine.py    # Lines 03-07: Inventory & Planning
│   ├── capacity_engine.py     # Lines 07, 09-12: Capacity & FTE
│   └── planning_engine.py     # Main orchestrator
└── ui/
    ├── app.py                 # Flask web server
    └── templates/index.html   # Web interface
```
## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run web server
python main.py

# 3. Open browser
http://localhost:5000

# 4. Upload Excel file, click Calculate
```

## Line Types Generated (14 Total)

| Line | Name | Python Module |
|------|------|---------------|
| 01 | Demand Forecast | forecast_engine.py |
| 02 | Dependent Demand | bom_engine.py |
| 03 | Total Demand | inventory_engine.py |
| 04 | Inventory | inventory_engine.py |
| 05 | Minimum Target Stock | inventory_engine.py |
| 06 | Production Plan | inventory_engine.py |
| 06 | Purchase Receipt | inventory_engine.py |
| 07 | Purchase Plan | inventory_engine.py |
| 07 | Capacity Utilization | capacity_engine.py |
| 08 | Dependent Requirements | bom_engine.py |
| 09 | Available Capacity | capacity_engine.py |
| 10 | Utilization Rate | capacity_engine.py |
| 11 | Shift Availability | capacity_engine.py |
| 12 | FTE Requirements | capacity_engine.py |


## Input Sheets Read (Raw Data Only)

- Material master
- BOM
- Routing
- OEE + Machine groups
- Forecast sheet
- Stock level sheet
- Safety stock
- Config
- FTE sheet


# Calculation Logic

### BOM Explosion (Line 02)
```
Child_Demand = Parent_Demand × BOM_Quantity_Per
```

### Total Demand (Line 03)
```
Total = Forecast + Dependent_Demand
```

### Production Plan (Line 06)
```
If (Stock - Demand) < Target:
    Plan = ceil((Target - (Stock - Demand)) / LotSize) × LotSize
```

### Capacity Utilization (Line 07)
```
Hours = (Production_Qty / Base_Qty) × Standard_Time
```

### Utilization Rate (Line 10)
```
Rate = Hours_Used / Hours_Available
```

### FTE Requirements (Line 12)
```
FTE = Total_Hours / (FTE_Hours_Per_Year / 12)