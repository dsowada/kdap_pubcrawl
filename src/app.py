# app.py (mobile-first, two-page flow: input -> map)
import streamlit as st
import pandas as pd
from datetime import datetime
import openrouteservice
import folium
from streamlit_folium import st_folium

from geopy.geocoders import Nominatim
from helpers import normalize_df, add_distance, add_opening_hours_features, select_candidates,ors_walking_route_coords


# -----------------------------
# Page config (mobile-friendly)
# -----------------------------
st.set_page_config(page_title="Pubcrawl Planner", layout="centered")


# -----------------------------
# Session state: page + data
# -----------------------------
if "page" not in st.session_state:
    st.session_state["page"] = "input"   # "input" or "map"
if "user_lat" not in st.session_state:
    st.session_state["user_lat"] = None
if "user_lon" not in st.session_state:
    st.session_state["user_lon"] = None
if "route_df" not in st.session_state:
    st.session_state["route_df"] = None
if "k" not in st.session_state:
    st.session_state["k"] = 4
if "prefs" not in st.session_state:
    st.session_state["prefs"] = {"food": False, "football": False, "karaoke": False}


# -----------------------------
# Helpers
# -----------------------------
def reset_all():
    st.session_state["page"] = "input"
    st.session_state["user_lat"] = None
    st.session_state["user_lon"] = None
    st.session_state["route_df"] = None


def geocode_address(addr: str):
    geolocator = Nominatim(user_agent="pubcrawl-planner")
    loc = geolocator.geocode(addr)
    if loc is None:
        return None, None
    return float(loc.latitude), float(loc.longitude)


def load_df() -> pd.DataFrame:
    # Mobile-first: simplest path is local CSV.
    # If you want upload, add st.file_uploader on input page.
    return pd.read_csv("regensburg_bars_backup.csv")


# -----------------------------
# INPUT PAGE
# -----------------------------
if st.session_state["page"] == "input":
    st.title("Pubcrawl Planner")

    st.write("Standort")
    address = st.text_input("Adresse", value="Regensburg")

    st.write("Anzahl Bars")
    k = st.number_input("k", min_value=1, max_value=20, value=int(st.session_state["k"]), step=1)

    st.write("Vorlieben")
    c1, c2, c3 = st.columns(3)
    with c1:
        food = st.toggle("Essen", value=st.session_state["prefs"]["food"])
    with c2:
        football = st.toggle("Fußball", value=st.session_state["prefs"]["football"])
    with c3:
        karaoke = st.toggle("Karaoke", value=st.session_state["prefs"]["karaoke"])

    # persist
    st.session_state["k"] = int(k)
    st.session_state["prefs"] = {"food": food, "football": football, "karaoke": karaoke}

    # Actions
    col_a, col_b = st.columns(2)
    with col_a:
        do_calc = st.button("Route berechnen", use_container_width=True)
    with col_b:
        do_reset = st.button("Reset", use_container_width=True)

    if do_reset:
        reset_all()
        st.rerun()

    if do_calc:
        # 1) address -> lat/lon
        with st.spinner("Suche Standort..."):
            user_lat, user_lon = geocode_address(address)

        if user_lat is None or user_lon is None:
            st.error("Adresse nicht gefunden. Bitte genauer eingeben.")
            st.stop()

        st.session_state["user_lat"] = user_lat
        st.session_state["user_lon"] = user_lon

        # 2) compute route (basic: nearest k after candidate selection)
        with st.spinner("Berechne Route..."):
            df_raw = load_df()
            df = normalize_df(df_raw)

            required = {"name", "lat", "lon"}
            missing = required - set(df.columns)
            if missing:
                st.error(f"CSV fehlt notwendige Spalten: {sorted(missing)}")
                st.stop()

            df = add_distance(df, user_lat, user_lon)
            df = add_opening_hours_features(df, now=datetime.now())
            candidates = select_candidates(df, st.session_state["k"])
            route_df = candidates.head(st.session_state["k"]).copy().reset_index(drop=True)

            st.session_state["route_df"] = route_df

        # 3) go to map page
        st.session_state["page"] = "map"
        st.rerun()


# -----------------------------
# MAP PAGE
# -----------------------------
elif st.session_state["page"] == "map":
    st.title("Deine Route")

    route_df = st.session_state["route_df"]
    user_lat = st.session_state["user_lat"]
    user_lon = st.session_state["user_lon"]

    if route_df is None or user_lat is None or user_lon is None:
        st.error("Keine Route vorhanden. Bitte zurück und neu berechnen.")
        st.button("Zurück", on_click=reset_all)
        st.stop()

    # Map
    m = folium.Map(location=[user_lat, user_lon], zoom_start=14)

    folium.Marker(
        [user_lat, user_lon],
        tooltip="Start",
        popup="Du",
    ).add_to(m)

    points = []
    for i, r in route_df.iterrows():
        lat, lon = float(r["lat"]), float(r["lon"])
        points.append((lat, lon))
        folium.Marker(
            [lat, lon],
            tooltip=f"{i+1}. {r['name']}",
            popup=f"{i+1}. {r['name']}",
        ).add_to(m)

    # connect bars in current order
    ORS_API_KEY = "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6IjBjZTg0MmEwMDk0NjRkY2RiNzYzM2Q0NjBiZmJhN2EwIiwiaCI6Im11cm11cjY0In0="

    if ORS_API_KEY is None:
        st.error("ORS_API_KEY fehlt. Lege ihn in .streamlit/secrets.toml ab.")
        st.stop()

    # Fußwege als Polylines zeichnen: Start -> Bar1 -> Bar2 -> ...
    route_points = [(user_lat, user_lon)] + [(float(r["lat"]), float(r["lon"])) for _, r in route_df.iterrows()]

    for i in range(len(route_points) - 1):
        seg = ors_walking_route_coords(ORS_API_KEY, route_points[i], route_points[i + 1])
        folium.PolyLine(seg).add_to(m)


    st_folium(m, width=360, height=650)  # gute Smartphone-Proportion


    # small list underneath (mobile-friendly)
    st.subheader("Reihenfolge")
    show_cols = [c for c in ["name", "distance_m", "open_now"] if c in route_df.columns]
    st.dataframe(route_df[show_cols], use_container_width=True, hide_index=True)

    # Reset / back
    st.button("Neu planen", use_container_width=True, on_click=reset_all)
