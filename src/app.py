# app.py
import streamlit as st
import pandas as pd
from datetime import datetime

import folium
from streamlit_folium import st_folium

from geopy.geocoders import Nominatim
from streamlit_geolocation import streamlit_geolocation

from helpers import normalize_df, add_distance, add_opening_hours_features, select_candidates


# -----------------------------
# Page setup
# -----------------------------
st.set_page_config(page_title="Pubcrawl Planner", layout="wide")
st.title("Pubcrawl Planner")

# -----------------------------
# Session state (prevents "reset" after reruns)
# -----------------------------
if "run" not in st.session_state:
    st.session_state["run"] = False
if "user_lat" not in st.session_state:
    st.session_state["user_lat"] = 49.0195
if "user_lon" not in st.session_state:
    st.session_state["user_lon"] = 12.0975
if "route_df" not in st.session_state:
    st.session_state["route_df"] = None
if "candidates_df" not in st.session_state:
    st.session_state["candidates_df"] = None
if "prefs" not in st.session_state:
    st.session_state["prefs"] = {"Essen": False, "Fußball": False, "Karaoke": False}


# -----------------------------
# Sidebar inputs
# -----------------------------
st.sidebar.header("Eingaben")

k = st.sidebar.number_input("Anzahl Bars (k)", min_value=1, max_value=20, value=4, step=1)

st.sidebar.subheader("Vorlieben")
pref_food = st.sidebar.toggle("Essen", value=st.session_state["prefs"]["Essen"])
pref_football = st.sidebar.toggle("Fußball", value=st.session_state["prefs"]["Fußball"])
pref_karaoke = st.sidebar.toggle("Karaoke", value=st.session_state["prefs"]["Karaoke"])
st.session_state["prefs"] = {"Essen": pref_food, "Fußball": pref_football, "Karaoke": pref_karaoke}

st.sidebar.subheader("Standort")
mode = st.sidebar.radio("Standort wählen", ["Adresse", "Smartphone (Browser)"], index=0)

if mode == "Adresse":
    address = st.sidebar.text_input("Adresse", value="Regensburg")
    if st.sidebar.button("Adresse suchen"):
        geolocator = Nominatim(user_agent="pubcrawl-planner")
        loc = geolocator.geocode(address)
        if loc is None:
            st.sidebar.error("Adresse nicht gefunden.")
        else:
            st.session_state["user_lat"] = float(loc.latitude)
            st.session_state["user_lon"] = float(loc.longitude)
else:
    st.sidebar.caption("Browser fragt ggf. nach Standortfreigabe.")
    loc = streamlit_geolocation()
    if isinstance(loc, dict) and loc.get("latitude") and loc.get("longitude"):
        st.session_state["user_lat"] = float(loc["latitude"])
        st.session_state["user_lon"] = float(loc["longitude"])

user_lat = float(st.session_state["user_lat"])
user_lon = float(st.session_state["user_lon"])
st.sidebar.write(f"Lat/Lon: {user_lat:.6f}, {user_lon:.6f}")

st.sidebar.divider()
st.sidebar.subheader("Daten")
uploaded = st.sidebar.file_uploader("Bars CSV hochladen", type=["csv"])
use_local = st.sidebar.checkbox("Lokale CSV nutzen", value=True)
local_path = st.sidebar.text_input("Pfad", value="data/bars.csv", disabled=not use_local)

st.sidebar.divider()
col_a, col_b = st.sidebar.columns(2)
with col_a:
    if st.button("Route berechnen", key="run_btn"):
        st.session_state["run"] = True
with col_b:
    if st.button("Reset", key="reset_btn"):
        st.session_state["run"] = False
        st.session_state["route_df"] = None
        st.session_state["candidates_df"] = None


# -----------------------------
# Data loader
# -----------------------------
def load_df() -> pd.DataFrame:
    if uploaded is not None:
        return pd.read_csv(uploaded)
    if use_local:
        return pd.read_csv(local_path)
    raise ValueError("Keine Datenquelle gewählt (CSV Upload oder lokale CSV).")


# -----------------------------
# Compute (only when run is True)
# -----------------------------
if not st.session_state["run"]:
    st.info("Links Eingaben setzen und „Route berechnen“ klicken.")
    st.stop()

try:
    with st.spinner("Lade Daten..."):
        df_raw = load_df()
except Exception as e:
    st.error(f"Fehler beim Laden der CSV: {e}")
    st.stop()

df = normalize_df(df_raw)

required = {"name", "lat", "lon"}
missing = required - set(df.columns)
if missing:
    st.error(f"CSV fehlt notwendige Spalten: {sorted(missing)}")
    st.stop()

with st.spinner("Berechne Distanz und Öffnungszeiten..."):
    df = add_distance(df, user_lat, user_lon)
    df = add_opening_hours_features(df, now=datetime.now())
    candidates = select_candidates(df, k)

    # Vorläufige Reihenfolge: die ersten k (nach Distanz)
    route_df = candidates.head(k).copy().reset_index(drop=True)

    st.session_state["candidates_df"] = candidates
    st.session_state["route_df"] = route_df


# -----------------------------
# Display result
# -----------------------------
route_df = st.session_state["route_df"]
candidates_df = st.session_state["candidates_df"]

st.subheader("Ergebnis (vorläufige Reihenfolge)")
show_cols = [c for c in ["name", "distance_m", "opening_hours_raw", "open_now", "open_score", "lat", "lon"] if c in route_df.columns]
st.dataframe(route_df[show_cols], use_container_width=True)

st.write("Aktuelle Vorlieben:", st.session_state["prefs"])

# -----------------------------
# Map (Folium)
# -----------------------------
st.subheader("Karte")

m = folium.Map(location=[user_lat, user_lon], zoom_start=14)

folium.Marker(
    [user_lat, user_lon],
    popup="Du",
    tooltip="Start",
).add_to(m)

points = []
for i, r in route_df.iterrows():
    lat, lon = float(r["lat"]), float(r["lon"])
    points.append((lat, lon))
    folium.Marker(
        [lat, lon],
        popup=f"{i+1}. {r['name']}",
        tooltip=f"{i+1}. {r['name']}",
    ).add_to(m)

# Polyline: verbindet Bars in der aktuellen Reihenfolge
if len(points) >= 2:
    folium.PolyLine(points).add_to(m)

st_folium(m, width=900, height=550)

st.caption(
    "Hinweis: Die Linie verbindet aktuell die Bars in einer vorläufigen Reihenfolge (nach Distanz / Kandidatenwahl). "
    "Ranking nach Vorlieben + echte Routing-Wege (Straßen) kommt als nächster Schritt."
)
