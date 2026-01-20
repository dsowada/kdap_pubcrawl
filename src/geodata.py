import time
from typing import Optional, Tuple

import folium
import openrouteservice
import pandas as pd
import streamlit as st
from geopy.exc import GeocoderTimedOut, GeocoderUnavailable
from geopy.geocoders import Nominatim

ORS_API_KEY = "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6IjBjZTg0MmEwMDk0NjRkY2RiNzYzM2Q0NjBiZmJhN2EwIiwiaCI6Im11cm11cjY0In0="

def ors_walking_route_coords(
    start: Tuple[float, float],
    end: Tuple[float, float],
    api_key: str = ORS_API_KEY,
):
    """
    start/end: (lat, lon)
    returns: list of (lat, lon) points for folium PolyLine
    """
    if not api_key:
        raise RuntimeError("ORS_API_KEY ist leer. Bitte in helpers.py setzen.")

    client = openrouteservice.Client(key=api_key)

    coords = [[start[1], start[0]], [end[1], end[0]]]  # ORS: [lon, lat]
    res = client.directions(coordinates=coords, profile="foot-walking", format="geojson")

    line = res["features"][0]["geometry"]["coordinates"]  # list of [lon, lat]
    return [(lat, lon) for lon, lat in line]


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def geocode_address(addr: str) -> Tuple[Optional[float], Optional[float]]:
    """Nominatim Geocoding mit Retries + Cache."""
    geolocator = Nominatim(user_agent="pubcrawl-planner", timeout=10)

    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            loc = geolocator.geocode(addr)
            if loc is None:
                return None, None
            return float(loc.latitude), float(loc.longitude)
        except (GeocoderTimedOut, GeocoderUnavailable) as e:
            last_err = e
            time.sleep(0.8 * (attempt + 1))
        except Exception as e:
            last_err = e
            break

    st.session_state["geocode_last_error"] = str(last_err) if last_err else None
    return None, None


def build_map_html(user_lat: float, user_lon: float, route_df: pd.DataFrame) -> str:
    """Erzeugt eine Folium-Map inkl. ORS-Segmente und gibt HTML zurück."""
    m = folium.Map(location=[user_lat, user_lon], zoom_start=19)

    folium.Marker([user_lat, user_lon], tooltip="Start", popup="Start").add_to(m)

    for i, r in route_df.iterrows():
        lat, lon = float(r["lat"]), float(r["lon"])
        folium.Marker(
            [lat, lon],
            tooltip=f"{i+1}. {r['name']}",
            popup=f"{i+1}. {r['name']}",
        ).add_to(m)

    route_points = [(user_lat, user_lon)] + [
        (float(r["lat"]), float(r["lon"])) for _, r in route_df.iterrows()
    ]

    for i in range(len(route_points) - 1):
        seg = ors_walking_route_coords(route_points[i], route_points[i + 1])
        folium.PolyLine(seg).add_to(m)

    return m.get_root().render()
