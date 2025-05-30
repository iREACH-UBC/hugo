import os
import glob
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz

# -------------------------------
# CONFIGURATION
# -------------------------------
sensor_ids = ["2021","2022", "2023", "2024","2026","2030","2031","2032","2033","2034","2039","2040","2041","2042","2043"]
data_folder = "data"
output_folder = "calibrated_data"
os.makedirs(output_folder, exist_ok=True)

# -------------------------------
# Define Time Range
# -------------------------------
pst_tz = pytz.timezone("America/Los_Angeles")
now_pst = datetime.now(pst_tz)

def parse_filename_date(filename, sensor_id):
    base = os.path.basename(filename)
    parts = base.split('_')
    if len(parts) != 2:
        raise ValueError("Filename does not match expected format")
    date_part = parts[1].split('.')[0]
    return datetime.strptime(date_part, "%Y-%m-%d").date()

# -------------------------------
# Dummy Calibration Functions
# -------------------------------
calibration_functions = {
    'CO': lambda x: x * 1.1,
    'NO': lambda x: x * 0.9,
    'NO2': lambda x: x * 1.05,
    'O3': lambda x: x * 1.2,
    'CO2': lambda x: x,
    'T': lambda x: x + 0.5,
    'RH': lambda x: x,
    'PM1.0': lambda x: x,
    'PM2.5': lambda x: x,
    'PM10': lambda x: x,
    'WD': lambda x: x,
    'WS': lambda x: x * 1.1
}

def apply_aqhi_ceiling(aqhi_series, pm25_1h_series):
    """
    Return a series where AQHI is the maximum of the original AQHI (rounded) 
    and the ceiling of 1-hour PM2.5 divided by 10. The result is an integer series.
    """
    aqhi_rounded = aqhi_series.round()
    pm25_ceiling = np.ceil(pm25_1h_series["PM2.5"] / 10)
    return np.maximum(aqhi_rounded, pm25_ceiling).astype(int)

# -------------------------------
# Process Each Sensor
# -------------------------------
for sensor in sensor_ids:
    pattern = os.path.join(data_folder, f"{sensor}", f"{sensor}_*.csv")
    files = glob.glob(pattern)
    if not files:
        print(f"No raw data files found for sensor {sensor} in {data_folder}")
        continue

    file_dates = {parse_filename_date(f, sensor) for f in files}
    sorted_dates = sorted(file_dates)
    if len(sorted_dates) < 1:
        print(f"Not enough data files for sensor {sensor}")
        continue
    last_two_dates = sorted_dates[-2:]
    print(f"Sensor {sensor}: Processing files for dates: {last_two_dates}")

    dfs = []
    for d in last_two_dates:
        file_pattern = os.path.join(data_folder, f"{sensor}_{d.strftime('%Y-%m-%d')}.csv")
        matched = glob.glob(file_pattern)
        if matched:
            try:
                df = pd.read_csv(matched[0])
                dfs.append(df)
            except Exception as e:
                print(f"Error reading {matched[0]}: {e}")
    if not dfs:
        print(f"No valid data for sensor {sensor} on the last two dates.")
        continue

    joined_df = pd.concat(dfs, ignore_index=True)

    try:
        # Convert DATE to datetime and shift back by 7 hours to simulate PST day transition
        joined_df['DATE'] = pd.to_datetime(joined_df['DATE']) - timedelta(hours=7)
        joined_df['DATE'] = joined_df['DATE'].dt.tz_localize(None)
    except Exception as e:
        print(f"Error converting DATE column: {e}")
        continue


    past_24h = now_pst.replace(tzinfo=None) - timedelta(hours=24)
    recent_df = joined_df[joined_df['DATE'] >= past_24h].copy()
    if recent_df.empty:
        print(f"No data in the past 24 hours for sensor {sensor}")
        continue

    for col, func in calibration_functions.items():
        if col in recent_df.columns:
            recent_df[col] = recent_df[col].apply(func)

    # Drop all columns after PM10 (keep up to and including PM10, plus DATE)
    if "PM10" in recent_df.columns:
        pm10_index = recent_df.columns.get_loc("PM10")
        cols_to_keep = recent_df.columns[:pm10_index + 1].tolist()
        if "DATE" not in cols_to_keep:
            cols_to_keep.insert(0, "DATE")
        recent_df = recent_df.loc[:, cols_to_keep]
    else:
        print(f"'PM10' not found in columns for sensor {sensor}. Skipping.")
        continue

    # Sort by DATE and set index for rolling averages
    recent_df = recent_df.sort_values("DATE")
    recent_df.set_index("DATE", inplace=True)

    # Make sure NO2, O3, PM2.5 are available
    required_cols = {"NO2", "O3", "PM2.5"}
    if not required_cols.issubset(recent_df.columns):
        print(f"Missing required columns for AQHI in sensor {sensor}")
        continue

    # 3-hour rolling mean for AQHI calculation
    rolling_means = recent_df[["NO2", "O3", "PM2.5"]].rolling("3H").mean()
    
    # 1-hour rolling mean for AQHI-Plus calculation
    PM25_1h = recent_df[["PM2.5"]].rolling("1H").mean()

    # Calculate individual AQHI components
    NO2_component = (np.exp(0.000871 * rolling_means["NO2"]) - 1)
    O3_component  = (np.exp(0.000537 * rolling_means["O3"]) - 1)
    PM25_component = (np.exp(0.000487 * rolling_means["PM2.5"]) - 1)
    
    # Calculate total AQHI
    component_sum = NO2_component + O3_component + PM25_component
    raw_aqhi = (10 / 10.4) * 100 * component_sum
    recent_df["AQHI"] = apply_aqhi_ceiling(raw_aqhi, PM25_1h)
    print(recent_df["AQHI"])
    
    # Normalize for contribution proportions
    recent_df["NO2_contrib"] = NO2_component / component_sum
    recent_df["O3_contrib"] = O3_component / component_sum
    recent_df["PM25_contrib"] = PM25_component / component_sum
    
    # Identify dominant AQHI contributor
    recent_df["Top_AQHI_Contributor"] = recent_df[["NO2_contrib", "O3_contrib", "PM25_contrib"]].idxmax(axis=1).str.replace("_contrib", "")

    # Reset index to get DATE back as column
    recent_df.reset_index(inplace=True)

    # Convert DATE column to ISO8601 format
    recent_df['DATE'] = recent_df['DATE'].apply(lambda dt: dt.isoformat())

    # Create sensor-specific subfolder
    sensor_output_folder = os.path.join(output_folder, sensor)
    os.makedirs(sensor_output_folder, exist_ok=True)

    # Save to sensor-specific folder
    output_file = os.path.join(
        sensor_output_folder,
        f"{sensor}_calibrated_{last_two_dates[0].strftime('%Y-%m-%d')}_to_{last_two_dates[-1].strftime('%Y-%m-%d')}.csv"
    )
    recent_df.to_csv(output_file, index=False)
    print(f"Calibrated data with AQHI for sensor {sensor} saved to {output_file}")

