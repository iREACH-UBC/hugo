import os
import glob
import pandas as pd
import json

# List your sensor IDs here
sensor_ids = ["2021","2022", "2023", "2024","2026","2030","2031",
              "2032","2033","2034","2039","2040","2041","2042","2043",
              "MOD-00616", "MOD-00632", "MOD-00625", "MOD-00631", "MOD-00623",
              "MOD-00628", "MOD-00620", "MOD-00627", "MOD-00630", "MOD-00624"]

# Paths (relative to repo root)
input_folder = "calibrated_data"
output_file = "HelloLamppostData.json"

# AQHI label mapping function
def get_aqhi_label(value):
    if value <= 3:
        return "low"
    elif value <= 6:
        return "moderate"
    elif value <= 10:
        return "high"
    else:
        return "very high"

output_json = []

for sensor_id in sensor_ids:
    pattern = os.path.join(input_folder, f"{sensor_id}_calibrated_*.csv")
    files = sorted(glob.glob(pattern), reverse=True)

    if not files:
        print(f"No files found for {sensor_id}")
        continue

    latest_file = files[0]
    try:
        df = pd.read_csv(latest_file, parse_dates=["DATE"])
        if df.empty:
            print(f"Empty file for {sensor_id}")
            continue

        latest = df.sort_values("DATE").iloc[-1]
        value = float(latest["AQHI"])
        label = get_aqhi_label(value)

        output_json.append({
            sensor_id: {
                "label": label,
                "value": round(value, 2)
            }
        })

    except Exception as e:
        print(f"Failed to process {sensor_id}: {e}")

# Write JSON output
with open(output_file, "w") as f:
    json.dump(output_json, f, indent=4)

print(f"Written to {output_file}")
