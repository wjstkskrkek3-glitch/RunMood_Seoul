import pandas as pd
from pathlib import Path
from .config import BASE_DIR

PROFILE = BASE_DIR / "data" / "air_quality_profile_2025_by_district_hour.csv"

def historical_air_profile(district: str, hour: int):
    if not PROFILE.exists(): return None
    df=pd.read_csv(PROFILE,encoding="utf-8-sig")
    x=df[(df["district"]==district)&(df["hour"]==int(hour))]
    return None if x.empty else x.iloc[0].to_dict()
