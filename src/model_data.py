import math
import re
from datetime import datetime
from typing import Optional, Dict

import pandas as pd
import streamlit as st


# --- Distance (Haversine) in meter ---
def distance_m(user_lat: float, user_lon: float, lat: float, lon: float) -> float:
    R = 6371000.0  # Earth radius in meters
    phi1 = math.radians(user_lat)
    phi2 = math.radians(lat)
    dphi = math.radians(lat - user_lat)
    dlambda = math.radians(lon - user_lon)

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalisiert Spalten und Typen für die weitere Verarbeitung.
    - lat/lon -> numeric (ungültige Werte werden NaN)
    - opening_hours_raw erzeugen, falls nur opening_hours existiert
    """
    df = df.copy()

    if "lat" in df.columns:
        df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    if "lon" in df.columns:
        df["lon"] = pd.to_numeric(df["lon"], errors="coerce")

    if "opening_hours_raw" not in df.columns and "opening_hours" in df.columns:
        df["opening_hours_raw"] = df["opening_hours"]

    return df


def add_distance(df: pd.DataFrame, user_lat: float, user_lon: float) -> pd.DataFrame:
    """
    Fügt distance_m (Meter) als Spalte hinzu.
    """
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


# --- Opening hours: minimal support "Mo-Sa 20:00-02:00"
def is_open_now_basic(opening_hours: str, now: datetime) -> Optional[bool]:
    """
    Minimaler Parser für ein sehr einfaches opening_hours-Format.
    Gibt zurück:
      - True / False, wenn interpretierbar
      - None, wenn nicht interpretierbar oder leer
    """
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

    # über Mitternacht (z.B. 20:00-02:00)
    return (now_min >= start_min) or (now_min <= end_min)


def add_opening_hours_features(df: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """
    Fügt open_now (True/False/None) und open_score (1.0/0.0/0.5) hinzu.
    """
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

def has_feature(cell) -> bool:
    if cell is None:
        return False
    s = str(cell).strip()
    return s != "" and s.lower() != "nan"



def derive_weights(toggles: dict, base: dict = None) -> dict:
    """
    toggles: {"food": bool, "sportsbar": bool, "surprise": bool}
    """
    if base is None:
        base = DEFAULT_WEIGHTS

    w = dict(base)  # copy
    enabled = [f for f in FEATURES if toggles.get(f, False)]
    n_enabled = len(enabled)

    if n_enabled == 0:
        # Case 1: nichts aktiv -> Default bleibt (Features geben leichten Bonus)
        return w

    # Case 2/3: Nur aktivierte Toggles zählen "stark"
    # -> aktivierte auf 3.0, deaktivierte auf 0.0 (strikt: "nur wenn toggles aktiv")
    for f in FEATURES:
        w[f] = 3.0 if toggles.get(f, False) else 0.0

    # distance bleibt 5.0 immer
    w["distance"] = float(base.get("distance", 5.0))
    return w

def distance_score(distance_m: float, d_min: float, d_max: float) -> float:
    """
    Normalisiert Distanz in [0..1], wobei 1 = beste (kleinste) Distanz.
    """
    if distance_m is None:
        return 0.0
    try:
        d = float(distance_m)
    except (TypeError, ValueError):
        return 0.0

    denom = (d_max - d_min) if (d_max is not None and d_min is not None) else 0.0
    if denom <= 0:
        # alle Distanzen gleich oder nicht vorhanden -> neutral
        return 0.5
    return (d_max - d) / denom

def compute_scores(df, toggles: dict):
    """
    Erwartet df Spalten:
      - distance_m (numeric)
      - food, sportsbar, surprise (optional)
    Ergebnis: df mit 'score' und 'pref_bonus'
    """
    w = derive_weights(toggles)

    # Min/Max Distanz für Normalisierung
    if "distance_m" in df.columns:
        d_min = df["distance_m"].min()
        d_max = df["distance_m"].max()
    else:
        d_min = d_max = None

    df = df.copy()

    # Distanzscore
    if "distance_m" in df.columns:
        df["_dist_score01"] = df["distance_m"].apply(lambda d: distance_score(d, d_min, d_max))
    else:
        df["_dist_score01"] = 0.0

    # Feature-Bonus (gewichtete Summe)
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

def rank_bars(df, toggles: dict):
    scored = compute_scores(df, toggles)

    sort_cols = ["score"]
    ascending = [False]

    # Tie-breaker: kleinere Distanz gewinnt bei gleichem Score
    if "distance_m" in scored.columns:
        sort_cols.append("distance_m")
        ascending.append(True)

    return scored.sort_values(sort_cols, ascending=ascending)


def select_candidates(df: pd.DataFrame, k: int) -> pd.DataFrame:
    """
    Nimmt die 2*k nächstgelegenen Bars (nach distance_m) als Kandidaten.
    """
    return (
        df.dropna(subset=["distance_m"])
        .sort_values("distance_m", ascending=True)
        .head(2 * k)
        .reset_index(drop=True)
    )
