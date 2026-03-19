from modules.data_loader import DataLoader
data = DataLoader('uploads/03_2025_December_SOP consolidation_MS_RECONC.xlsm')
from collections import Counter
earliest = Counter()
for mat, fd in data.forecasts.items():
    if fd:
        e = sorted(fd.keys())[0]
        earliest[e] += 1
print("Earliest period distribution:")
for k, v in sorted(earliest.items()):
    print(f"  {k}: {v} materials")
print(f"Total: {len(data.forecasts)} materials")
# Show sorted keys for a few materials
for mat, fd in list(data.forecasts.items())[:3]:
    keys = sorted(fd.keys())
    print(f"  Mat {mat}: {len(keys)} periods, first={keys[0]}, sorted[13]={keys[13] if len(keys)>13 else 'MISSING'}, last={keys[-1]}")
