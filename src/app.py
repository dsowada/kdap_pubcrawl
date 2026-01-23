import base64
import hashlib
import json
import random
import time
import folium
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
from datetime import datetime
from pathlib import Path

from model_data import (
    normalize_df,
    add_distance,
    add_opening_hours_features,
    select_candidates,
    has_feature,
    derive_weights,
    distance_score,
    compute_scores,
    rank_bars,
    preference_in_df,
)

from geodata import (
    geocode_address,
    build_map_html,
    ors_walking_route_coords, 
)

# Config
st.set_page_config(page_title="Pubcrawl Planner", layout="centered")

# Hardcoded ORS key and const data, only here for better overview, normally seperated in const.py
ORS_API_KEY = "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6IjBjZTg0MmEwMDk0NjRkY2RiNzYzM2Q0NjBiZmJhN2EwIiwiaCI6Im11cm11cjY0In0="

CSV_REL_PATH = Path("data") / "regensburg_bars_backup.csv"
DEFAULT_CARDS_PATH = "data/cards.json"
DEFAULT_PROGRESS_PATH = "data/progress.json"

# Session State
# setting sessions
if "page" not in st.session_state:
    st.session_state["page"] = "input"  # "input" or "map"

if "user_lat" not in st.session_state:
    st.session_state["user_lat"] = None
if "user_lon" not in st.session_state:
    st.session_state["user_lon"] = None

if "route_df" not in st.session_state:
    st.session_state["route_df"] = None

if "k" not in st.session_state:
    st.session_state["k"] = 4

if "prefs" not in st.session_state:
    st.session_state["prefs"] = {
        "food": False,
        "sportsbar": False,
        "surprise": False,
    }

if "pref_message" not in st.session_state:
    st.session_state["pref_message"] = None

# Helpers
# resetting sessions 
def reset_all():
    st.session_state["page"] = "input"
    st.session_state["user_lat"] = None
    st.session_state["user_lon"] = None
    st.session_state["route_df"] = None
    st.session_state["pref_message"]= None


#formatting the distance 
def format_distance_m(val) -> str:
    try:
        m = float(val)
    except Exception:
        return "—"
    if m >= 1000:
        return f"{m/1000:.1f} km"
    return f"{int(m)} m"


def repo_root() -> Path:
    # src/app.py -> repo_root is parent of src
    return Path(__file__).resolve().parent.parent
#loading the df and searching every possibibilty beacuase with probelms when pushing the data to github and with streamlit
def load_df() -> pd.DataFrame:
    root = Path(__file__).resolve().parent.parent
    target_name = "regensburg_bars_backup.csv"

    candidates = [
        root / "data" / target_name,
        root / "Data" / target_name,       
        root / target_name,                
        root / "src" / "data" / target_name,
    ]

    for p in candidates:
        if p.exists():
            st.success(f"CSV gefunden: {p}")
            return pd.read_csv(p)

    # Debugging(useful to differ for API Errors and not found data )
    st.error("CSV nicht gefunden. Debug-Infos:")
    st.write("repo_root:", str(root))
    st.write("root exists:", root.exists())
    st.write("root contents:", sorted([x.name for x in root.iterdir()]) if root.exists() else [])

    data_dir = root / "data"
    st.write("data_dir:", str(data_dir))
    st.write("data_dir exists:", data_dir.exists())
    if data_dir.exists():
        st.write("data_dir contents:", sorted([x.name for x in data_dir.iterdir()]))

    # searching for csv
    hits = list(root.rglob(target_name))
    st.write("rglob hits:", [str(h) for h in hits])

    st.stop()



# INPUT PAGE

if st.session_state["page"] == "input":
    st.title("KDAP Pubcrawl Application")

    st.write("Location:")
    address = st.text_input("Address", value="Regensburg")

    #input columns
    col_k, col_food, col_sports, col_surprise = st.columns([1.2, 1, 1, 1])
    #slider reduced to k = 10
    with col_k:
        st.markdown("**Bars to visit**")
        k = st.slider(
            "k",
            1, 10,
            int(st.session_state["k"]),
            1,
            label_visibility="collapsed"
        )

    with col_food:
        st.markdown("**Preferences**")
        food = st.toggle("🍔 Food", value=st.session_state["prefs"]["food"])

    with col_sports:
        st.markdown("&nbsp;", unsafe_allow_html=True)
        sportsbar = st.toggle("⚽ Sportsbar", value=st.session_state["prefs"]["sportsbar"])

    with col_surprise:
        st.markdown("&nbsp;", unsafe_allow_html=True)
        surprise = st.toggle("🎤 Surprise", value=st.session_state["prefs"]["surprise"])

    st.session_state["prefs"] = {
        "food": food,
        "sportsbar": sportsbar,
        "surprise": surprise,
    }


    st.session_state["k"] = int(k)
    st.session_state["prefs"] = {"food": food, "sportsbar": sportsbar, "surprise": surprise}

    b1, b2 = st.columns(2)
    with b1:
        do_calc = st.button("**Let's Go!**", use_container_width=True)
    with b2:
        do_reset = st.button("Reset", use_container_width=True)

    if do_reset:
        reset_all()
        st.rerun()

    if do_calc:
        with st.spinner("Suche Standort..."):
            user_lat, user_lon = geocode_address(address)

        if user_lat is None or user_lon is None:
            st.error(
                "Standort konnte nicht ermittelt werden. "
                "Bitte Adresse präzisieren (z. B. 'Domplatz 1, Regensburg') oder später erneut versuchen."
            )
            if st.session_state.get("geocode_last_error"):
                st.caption(f"Geocoding-Details: {st.session_state['geocode_last_error']}")
            st.stop()

        st.session_state["user_lat"] = user_lat
        st.session_state["user_lon"] = user_lon

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
            #df is a modelled table with openeing hours and computed distance from the user location
            prefs = st.session_state["prefs"]

            candidates = select_candidates(df, k)
            ranked_candidates = rank_bars(candidates, prefs)
            # checking for preferences
            print(preference_in_df(ranked_candidates, prefs))
            if(preference_in_df(ranked_candidates, prefs) == False):
                st.session_state["pref_message"] = "Your preference is not in walking distance."


            route_df = ranked_candidates.head(k).copy().reset_index(drop=True)
            
            st.session_state["route_df"] = route_df
            
            st.session_state["map_html"] = build_map_html(
                        user_lat,
                        user_lon,
                        route_df
                    )

        st.session_state["page"] = "map"
        st.rerun()


# MAP PAGE
elif st.session_state["page"] == "map":
    st.title("Your route:")

    route_df = st.session_state["route_df"]
    user_lat = st.session_state["user_lat"]
    user_lon = st.session_state["user_lon"]
    pref_message = st.session_state["pref_message"]

    if route_df is None or user_lat is None or user_lon is None:
        st.error("lookslike there's no route available, maybe you find a house party or your preferences are too special ...")
        st.button("back", width="stretch", on_click=reset_all)
        st.stop()

    # Collapsible map (for easier navigation)
    with st.expander("show/hide map", expanded=True):
        if "map_html" in st.session_state:
            components.html(
                st.session_state["map_html"],
                height=650
            )
        else:
            st.warning("Missing map... try to reload it")
    if(pref_message):
        st.badge(pref_message,color ="orange")
    #showing the bar order with score (and distance just for information )
    st.subheader("Bar tour order")
    show_cols = [c for c in ["name", "distance_m", "score"] if c in route_df.columns]
    st.dataframe(route_df[show_cols], width="stretch", hide_index=True)