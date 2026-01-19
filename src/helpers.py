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

#cards json

import json
from pathlib import Path
import streamlit as st
import folium
from streamlit_folium import st_folium
CARDS_FILE = Path("cards.json")


def load_cards() -> list[dict]:
    if not CARDS_FILE.exists():
        # Minimaler Default, falls Datei fehlt
        return [
            {"id": "c1", "title": "Icebreaker", "task": "Jeder nennt eine Fun-Fact-Lüge und die Gruppe errät, was stimmt."},
            {"id": "c2", "title": "Foto-Challenge", "task": "Macht ein Gruppenfoto mit einem Fremden (höflich fragen)."},
        ]
    try:
        data = json.loads(CARDS_FILE.read_text(encoding="utf-8"))
        # defensive: nur gültige Karten zurückgeben
        cards = []
        for x in data if isinstance(data, list) else []:
            if isinstance(x, dict) and x.get("title") and x.get("task"):
                cid = str(x.get("id") or _stable_seed(str(x.get("title")), str(x.get("task"))))
                cards.append({"id": cid, "title": str(x["title"]), "task": str(x["task"])})
        return cards
    except Exception:
        return []


def save_cards(cards: list[dict]) -> None:
    # Speichert sauber formatiert
    CARDS_FILE.write_text(json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8")


def manage_cards_ui():
    """
    Simple deck editor. User can edit cards; persists to cards.json.
    """
    st.subheader("Karten verwalten")

    cards = load_cards()
    df_cards = pd.DataFrame(cards) if cards else pd.DataFrame(columns=["id", "title", "task"])

    # Hide id editing to avoid accidental breakage; if missing, we will re-generate
    if "id" not in df_cards.columns:
        df_cards["id"] = ""

    edited = st.data_editor(
        df_cards[["id", "title", "task"]],
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            "id": st.column_config.TextColumn("id", disabled=True),
            "title": st.column_config.TextColumn("Titel"),
            "task": st.column_config.TextColumn("Aufgabe"),
        },
    )

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Karten speichern", use_container_width=True):
            cleaned = []
            for _, row in edited.iterrows():
                title = str(row.get("title") or "").strip()
                task = str(row.get("task") or "").strip()
                if not title or not task:
                    continue
                cid = str(row.get("id") or _stable_seed(title, task))
                cleaned.append({"id": cid, "title": title, "task": task})

            save_cards(cleaned)
            st.success("Karten gespeichert.")
            st.rerun()

    with col2:
        if st.button("Zurücksetzen (Defaults)", use_container_width=True):
            if CARDS_FILE.exists():
                CARDS_FILE.unlink()
            st.session_state.pop("card_assignment", None)
            st.session_state.pop("card_done", None)
            st.success("Zurückgesetzt.")
            st.rerun()


# helpers.py (ADD THESE FUNCTIONS)
import base64
import json
import requests
import streamlit as st


def _gh_headers():
    token = st.secrets.get("GH_TOKEN", "")
    if not token:
        return None
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _gh_repo_info():
    repo = st.secrets.get("GH_REPO", "")
    branch = st.secrets.get("GH_BRANCH", "main")
    if not repo or "/" not in repo:
        return None, None, None
    owner, name = repo.split("/", 1)
    return owner, name, branch


def gh_get_json(path: str, default):
    """
    Reads a JSON file from GitHub repo via Contents API.
    Returns (data, sha).
    """
    headers = _gh_headers()
    owner, repo, branch = _gh_repo_info()
    if not headers or not owner:
        return default, None

    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    r = requests.get(url, headers=headers, params={"ref": branch}, timeout=20)

    if r.status_code == 404:
        return default, None
    r.raise_for_status()

    payload = r.json()
    content_b64 = payload.get("content", "")
    sha = payload.get("sha")

    if not content_b64:
        return default, sha

    raw = base64.b64decode(content_b64).decode("utf-8")
    try:
        return json.loads(raw), sha
    except Exception:
        return default, sha


def gh_put_json(path: str, data, sha: str | None, commit_message: str):
    """
    Writes JSON to GitHub repo via Contents API.
    If sha is None -> creates file. Else -> updates file.
    """
    headers = _gh_headers()
    owner, repo, branch = _gh_repo_info()
    if not headers or not owner:
        raise RuntimeError("GitHub Secrets not configured (GH_TOKEN/GH_REPO).")

    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"

    raw = json.dumps(data, ensure_ascii=False, indent=2)
    content_b64 = base64.b64encode(raw.encode("utf-8")).decode("utf-8")

    body = {
        "message": commit_message,
        "content": content_b64,
        "branch": branch,
    }
    if sha:
        body["sha"] = sha

    r = requests.put(url, headers=headers, json=body, timeout=20)
    r.raise_for_status()
    return r.json()


# ---------- Domain-level convenience wrappers ----------

def load_cards() -> list[dict]:
    path = st.secrets.get("GH_PATH_CARDS", "data/cards.json")
    data, sha = gh_get_json(path, default=[])
    # Normalize + store sha in session for later update
    st.session_state["__cards_sha"] = sha

    cards = []
    if isinstance(data, list):
        for x in data:
            if isinstance(x, dict) and x.get("title") and x.get("task"):
                cid = str(x.get("id") or "")
                cards.append(
                    {"id": cid, "title": str(x["title"]).strip(), "task": str(x["task"]).strip()}
                )

    # Ensure IDs
    for i, c in enumerate(cards):
        if not c["id"]:
            c["id"] = f"c{i+1}"

    return cards


def save_cards(cards: list[dict]):
    path = st.secrets.get("GH_PATH_CARDS", "data/cards.json")

    # Clean
    cleaned = []
    for c in cards:
        title = str(c.get("title", "")).strip()
        task = str(c.get("task", "")).strip()
        if not title or not task:
            continue
        cid = str(c.get("id", "")).strip() or f"c{len(cleaned)+1}"
        cleaned.append({"id": cid, "title": title, "task": task})

    sha = st.session_state.get("__cards_sha")
    gh_put_json(path, cleaned, sha, commit_message="Update cards deck via Streamlit")
    # Refresh sha after write
    _, new_sha = gh_get_json(path, default=cleaned)
    st.session_state["__cards_sha"] = new_sha


def load_progress() -> dict:
    path = st.secrets.get("GH_PATH_PROGRESS", "data/progress.json")
    data, sha = gh_get_json(path, default={})
    st.session_state["__progress_sha"] = sha
    return data if isinstance(data, dict) else {}


def save_progress(progress: dict):
    path = st.secrets.get("GH_PATH_PROGRESS", "data/progress.json")
    sha = st.session_state.get("__progress_sha")
    gh_put_json(path, progress, sha, commit_message="Update progress via Streamlit")
    _, new_sha = gh_get_json(path, default=progress)
    st.session_state["__progress_sha"] = new_sha
    
#helper for html map

import streamlit.components.v1 as components

ORS_API_KEY = "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6IjBjZTg0MmEwMDk0NjRkY2RiNzYzM2Q0NjBiZmJhN2EwIiwiaCI6Im11cm11cjY0In0="

def build_map_html(user_lat, user_lon, route_df):
    m = folium.Map(location=[user_lat, user_lon], zoom_start=19)

    folium.Marker(
        [user_lat, user_lon],
        tooltip="Start",
        popup="Start"
    ).add_to(m)

    # Markers
    for i, r in route_df.iterrows():
        lat, lon = float(r["lat"]), float(r["lon"])
        folium.Marker(
            [lat, lon],
            tooltip=f"{i+1}. {r['name']}",
            popup=f"{i+1}. {r['name']}",
        ).add_to(m)

    # Routes
    route_points = [(user_lat, user_lon)] + [
        (float(r["lat"]), float(r["lon"])) for _, r in route_df.iterrows()
    ]

    for i in range(len(route_points) - 1):
        seg = ors_walking_route_coords(
            ORS_API_KEY,
            route_points[i],
            route_points[i + 1]
        )
        folium.PolyLine(seg).add_to(m)

    return m.get_root().render()

