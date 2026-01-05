import re
import math
from datetime import datetime
import pandas as pd
import openrouteservice


# --- Distance (Haversine) in Metern ---
def distance_m(user_lat: float, user_lon: float, lat: float, lon: float) -> float:
    R = 6371000.0  # Earth radius in meters
    phi1 = math.radians(user_lat)
    phi2 = math.radians(lat)
    dphi = math.radians(lat - user_lat)
    dlambda = math.radians(lon - user_lon)

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")

    if "opening_hours_raw" not in df.columns and "opening_hours" in df.columns:
        df["opening_hours_raw"] = df["opening_hours"]
    return df


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


# --- Opening hours: minimal support "Mo-Sa 20:00-02:00" ---
def is_open_now_basic(opening_hours: str, now: datetime):
    if not isinstance(opening_hours, str) or not opening_hours.strip():
        return None

    s = opening_hours.strip()
    m = re.search(
        r"(Mo|Tu|We|Th|Fr|Sa|Su)\s*-\s*(Mo|Tu|We|Th|Fr|Sa|Su)\s+(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})",
        s
    )
    if not m:
        return None

    a, b, start, end = m.group(1), m.group(2), m.group(3), m.group(4)
    days = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
    ia, ib = days.index(a), days.index(b)
    valid_days = days[ia:ib + 1] if ia <= ib else days[ia:] + days[:ib + 1]

    wd = days[now.weekday()]

    def to_min(hm):
        h, mm = hm.split(":")
        return int(h) * 60 + int(mm)

    now_min = now.hour * 60 + now.minute
    start_min = to_min(start)
    end_min = to_min(end)

    if wd not in valid_days:
        return False

    if start_min <= end_min:
        return start_min <= now_min <= end_min
    return (now_min >= start_min) or (now_min <= end_min)


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


def select_candidates(df: pd.DataFrame, k: int) -> pd.DataFrame:
    return (
        df.dropna(subset=["distance_m"])
          .sort_values("distance_m", ascending=True)
          .head(2 * k)
          .reset_index(drop=True)
    )

def ors_walking_route_coords(api_key: str, start, end):
    """
    start/end: (lat, lon)
    returns: list of (lat, lon) points for folium PolyLine
    """
    client = openrouteservice.Client(key=api_key)

    # ORS expects coordinates as [lon, lat]
    coords = [[start[1], start[0]], [end[1], end[0]]]

    # geojson output gives a LineString geometry
    res = client.directions(
        coordinates=coords,
        profile="foot-walking",
        format="geojson"
    )

    line = res["features"][0]["geometry"]["coordinates"]  # list of [lon, lat]
    return [(lat, lon) for lon, lat in line]