from locale import D_FMT
import math
import re
from datetime import datetime
from typing import Optional, Dict

from altair import DataFormat
import pandas as pd
import streamlit as st


# distance in meter
def distance_m(user_lat: float, user_lon: float, lat: float, lon: float) -> float:
    R = 6371000.0  # Earth radius in meters
    phi1 = math.radians(user_lat)
    phi2 = math.radians(lat)
    deltalat = math.radians(lat - user_lat)
    deltalon= math.radians(lon - user_lon)

    a = (
        math.sin(deltalat / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(deltalon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

#normalizes df for later modelling data (converts string to float, makes sure opening hours exist)
def normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "lat" in df.columns:
        df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    if "lon" in df.columns:
        df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    if "opening_hours_raw" not in df.columns and "opening_hours" in df.columns:
        df["opening_hours_raw"] = df["opening_hours"]
    return df

#adding col distance from users adresse to bar for selecting suitable candidates
def add_distance(df: pd.DataFrame, user_lat: float, user_lon: float) -> pd.DataFrame:
    df = df.copy()
    distances = []

    for _, row in df.iterrows():
        lat, lon = row.get("lat"), row.get("lon")
        if pd.isna(lat) or pd.isna(lon):
            distances.append(None)
        else:
            distances.append(distance_m(user_lat, user_lon, float(lat), float(lon)))

    df["distance_m"] = distances
    return df


# converts and searches for opening time in difficult to read "opening hours" string, to get the opening hours or information about the opening time
def is_open_now_basic(opening_hours: str, now: datetime) -> Optional[bool]:
    if not isinstance(opening_hours, str) or not opening_hours.strip():
        return None

    s = opening_hours.strip()
    m = re.search(
        r"(Mo|Tu|We|Th|Fr|Sa|Su)\s*-\s*(Mo|Tu|We|Th|Fr|Sa|Su)\s+(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})",
        s,
    )
    if not m:
        return None

    a, b, start, end = m.group(1), m.group(2), m.group(3), m.group(4)
    days = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
    ia, ib = days.index(a), days.index(b)
    valid_days = days[ia : ib + 1] if ia <= ib else days[ia:] + days[: ib + 1]

    wd = days[now.weekday()]

    def to_min(hm: str) -> int:
        h, mm = hm.split(":")
        return int(h) * 60 + int(mm)

    now_min = now.hour * 60 + now.minute
    start_min = to_min(start)
    end_min = to_min(end)

    if wd not in valid_days:
        return False

    if start_min <= end_min:
        return start_min <= now_min <= end_min

    # AI helped me with sorting information about wether its the actual day or the next day when time passes 00:00
    return (now_min >= start_min) or (now_min <= end_min)

#easier function to get information if the bar is open (1) closed(0) or else (0.5)
def add_opening_hours_features(df: pd.DataFrame, now: datetime) -> pd.DataFrame:
    df = df.copy()
    col = "opening_hours_raw" if "opening_hours_raw" in df.columns else "opening_hours"
    open_now_list = []
    open_score_list = []

    for _, row in df.iterrows():
        v = is_open_now_basic(row.get(col), now)
        open_now_list.append(v)

        if v is True:
            open_score_list.append(1.0)
        elif v is False:
            open_score_list.append(0.0)
        else:
            open_score_list.append(0.5)

    df["open_now"] = open_now_list
    df["open_score"] = open_score_list
    return df

#new for adding the weightinng correctly 
DEFAULT_WEIGHTS = {
    "distance": 5.0,
    "food": 0.5,
    "sportsbar": 0.5,
    "surprise": 0.5,
}


FEATURES = ["food", "sportsbar", "surprise"]
#getting information about features 
def has_feature(cell) -> bool:
    if cell is None:
        return False
    s = str(cell).strip()
    return s != "" and s.lower() != "nan"
#selecting the weighting depending on users input
def derive_weights(toggles: dict, base: dict = None) -> dict:
    if base is None:
        base = DEFAULT_WEIGHTS

    w = dict(base)
    enabled = [f for f in FEATURES if toggles.get(f, False)]
    n_enabled = len(enabled)

    if n_enabled == 0:
        # Case1: no user preference -> weighting stays the same 
        return w

    #Case2: 1-3 toggle get activated by user 
    for f in FEATURES:
        w[f] = 4.5 if toggles.get(f, False) else 0.0

    #making sure distance stays the same
    w["distance"] = float(base.get("distance", 5.0))
    return w
#getting the correct normalized distance score for correct weighting [1..0] where 1 is perfect and 0 very bad
def distance_score(distance_m: float, d_min: float, d_max: float) -> float:
    if distance_m is None:
        return 0.0
    try:
        d = float(distance_m)
    except (TypeError, ValueError):
        return 0.0

    denom = (d_max - d_min) if (d_max is not None and d_min is not None) else 0.0
    if denom <= 0:
        #if all distances are the sam eor not available then value stays  at 0.5
        return 0.5
    return (d_max - d) / denom

def compute_scores(df, toggles: dict):
    w = derive_weights(toggles)

    # Min/Max Distanz für Normalisierung
    if "distance_m" in df.columns:
        d_min = df["distance_m"].min()
        d_max = df["distance_m"].max()
    else:
        d_min = d_max = None

    df = df.copy()

    # Distanzscodistance score
    if "distance_m" in df.columns:
        df["_dist_score01"] = df["distance_m"].apply(lambda d: distance_score(d, d_min, d_max))
    else:
        df["_dist_score01"] = 0.0

    #
    def row_bonus(row: pd.Series) -> float:
        bonus = 0.0
        for f in FEATURES:
            if f in row.index and has_feature(row.get(f, "")):
                bonus += float(w.get(f, 0.0))
        return bonus


    df["pref_bonus"] = df.apply(row_bonus, axis=1)

    # Gesamtscore (Distanz dominiert durch Gewicht 5.0)
    df["score"] = float(w["distance"]) * df["_dist_score01"] + df["pref_bonus"]

    return df

#ranking bars: first prioority is the score (with preference) if its the same then the distance will be the decider
def rank_bars(df, toggles: dict):
    scored = compute_scores(df, toggles)

    sort_cols = ["score"]
    ascending = [False]

    # when score is the same
    if "distance_m" in scored.columns:
        sort_cols.append("distance_m")
        ascending.append(True)

    return scored.sort_values(sort_cols, ascending=ascending)

# creating df with selected candidates
def select_candidates(df: pd.DataFrame, k: int) -> pd.DataFrame:
    return (
        df.dropna(subset=["distance_m"])
        .sort_values("distance_m", ascending=True)
        .head(2 * k)
        .reset_index(drop=True)
    )

def preference_in_df(df: pd.DataFrame, toggles: dict) -> bool:
    for pref, enabled in toggles.items():
        if not enabled:
            continue
        if pref not in df.columns:
            return False
        if not df[pref].any():
            return False
    return True
