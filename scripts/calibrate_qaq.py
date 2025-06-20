import os
import glob
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz

# ----------------------------------------
# CONFIGURATION
# ----------------------------------------
sensor_ids = [
    "MOD-00616", "MOD-00632", "MOD-00625", "MOD-00631", "MOD-00623",
    "MOD-00628", "MOD-00620", "MOD-00627", "MOD-00630", "MOD-00624"
]

data_folder = "data"
output_folder = "calibrated_data"
os.makedirs(output_folder, exist_ok=True)

# ----------------------------------------
# TIMEZONE
# ----------------------------------------
pst = pytz.timezone("America/Los_Angeles")
now_pst = datetime.now(pst)
past_24h = now_pst.replace(tzinfo=None) - timedelta(hours=24)

# ----------------------------------------
# DUMMY CALIBRATION FUNCTIONS
# ----------------------------------------
calibration_functions = {
    "CO": lambda x: x * 1.1,
    "NO": lambda x: x * 0.9,
    "NO2": lambda x: x * 1.05,
    "O3": lambda x: x * 1.2,
    "CO2": lambda x: x,
    "T": lambda x: x + 0.5,
    "RH": lambda x: x,
    "PM1.0": lambda x: x,
    "PM2.5": lambda x: x,
    "PM10": lambda x: x,
    "TE": lambda x: "QAQ",  # Placeholder
}

varmap = {
    "gases.co.diff": "CO",
    "gases.no.diff": "NO",
    "gases.no2.diff": "NO2",
    "gases.o3.diff": "O3",
    "gases.co2.raw": "CO2",
    "met.temp": "T",
    "met.rh": "RH",
    "opc.pm1": "PM1.0",
    "opc.pm25": "PM2.5",
    "opc.pm10": "PM10"
}

def apply_aqhi_ceiling(aqhi_series, pm25_1h_series):
    aqhi_rounded = aqhi_series.round()
    pm25_ceiling = np.ceil(pm25_1h_series["PM2.5"] / 10)
    return np.maximum(aqhi_rounded, pm25_ceiling).astype(int)

# ----------------------------------------
# PROCESS EACH SENSOR
# ----------------------------------------
for sensor in sensor_ids:
    pattern = os.path.join(data_folder, f"{sensor}-*.csv")
    files = glob.glob(pattern)
    if not files:
        print(f"No files found for {sensor}")
        continue

    latest_file = max(files, key=os.path.getmtime)
    print(f"\U0001F4C4 Processing {latest_file}")

    try:
        df = pd.read_csv(latest_file)
    except Exception as e:
        print(f"❌ Failed to read {latest_file}: {e}")
        continue

    try:
        if "timestamp_local" not in df.columns:
            raise KeyError("'timestamp_local' missing")
        df["DATE"] = pd.to_datetime(df["timestamp_local"])
        df = df[df["DATE"] >= past_24h].copy()
    except Exception as e:
        print(f"❌ Error preparing DATE column for {sensor}: {e}")
        continue

    if df.empty or df["DATE"].max() < past_24h:
        print(f"⚠️ Data for {sensor} is stale. Skipping AQHI calculation and inserting fallback row.")
        fallback_date = now_pst.replace(tzinfo=None)
        fallback = {
            "DATE": fallback_date.isoformat(),
            "TE": "QAQ",
            "CO": -1, "NO": -1, "NO2": -1, "O3": -1,
            "CO2": -1, "T": -1, "RH": -1,
            "PM1.0": -1, "PM2.5": -1, "PM10": -1,
            "AQHI": -1, "Top_AQHI_Contributor": "-1"
        }
        df = pd.DataFrame([fallback])
        sensor_output_folder = os.path.join(output_folder, sensor)
        os.makedirs(sensor_output_folder, exist_ok=True)
        output_path = os.path.join(sensor_output_folder, f"{sensor}_calibrated_{fallback_date.date()}_to_{now_pst.date()}.csv")
        df.to_csv(output_path, index=False)
        print(f"✅ Saved fallback file for {sensor}: {output_path}")
        continue

    for raw, std in varmap.items():
        if raw in df.columns:
            df[std] = calibration_functions[std](pd.to_numeric(df[raw], errors="coerce"))
        else:
            df[std] = np.nan
    df["TE"] = calibration_functions["TE"](None)

    df = df.sort_values("DATE")
    df.set_index("DATE", inplace=True)

    required = {"NO2", "O3", "PM2.5"}
    if not required.issubset(df.columns):
        print(f"❌ Missing columns for AQHI in {sensor}")
        continue

    rolling = df[["NO2", "O3", "PM2.5"]].rolling("3h").mean()
    pm25_1h = df[["PM2.5"]].rolling("1h").mean()

    NO2_component = (np.exp(0.000871 * rolling["NO2"]/1000) - 1)
    O3_component  = (np.exp(0.000537 * rolling["O3"]/1000) - 1)
    PM25_component = (np.exp(0.000487 * rolling["PM2.5"]) - 1)

    component_sum = NO2_component + O3_component + PM25_component
    raw_aqhi = pd.Series((10 / 10.4) * 100 * component_sum, index=df.index)

    df["AQHI"] = apply_aqhi_ceiling(raw_aqhi, pm25_1h)

    df["NO2_contrib"] = NO2_component / component_sum
    df["O3_contrib"] = O3_component / component_sum
    df["PM25_contrib"] = PM25_component / component_sum
    # Identify dominant AQHI contributor
    df["Top_AQHI_Contributor"] = df[["NO2_contrib", "O3_contrib", "PM25_contrib"]] \
                                    .idxmax(axis=1).str.replace("_contrib", "")
    
    df.reset_index(inplace=True)
    df["DATE"] = df["DATE"].apply(lambda dt: dt.isoformat())
    
    # Final column ordering
    desired_cols = [
        "DATE", "TE", "CO", "NO", "NO2", "O3", "CO2", "T", "RH",
        "PM1.0", "PM2.5", "PM10", "AQHI", "Top_AQHI_Contributor"
    ]
    
    # Ensure all required columns are present
    for col in desired_cols:
        if col not in df.columns:
            df[col] = np.nan
    df = df[desired_cols]
    
    # Save
    date_str = df["DATE"].apply(lambda d: d.split("T")[0]).min()
    sensor_output_folder = os.path.join(output_folder, sensor)
    os.makedirs(sensor_output_folder, exist_ok=True)
    
    output_path = os.path.join(
        sensor_output_folder,
        f"{sensor}_calibrated_{date_str}_to_{now_pst.date()}.csv"
    )
    df.to_csv(output_path, index=False)
    print(f"✅ Saved calibrated file: {output_path}")
