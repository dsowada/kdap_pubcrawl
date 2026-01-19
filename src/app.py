# src/app.py
# Stable Streamlit app: input -> map, collapsible map, global cards + global progress (GitHub if configured),
# robust CSV path loading, robust geocoding (timeouts/retries/cache), Streamlit width API (no use_container_width).

import base64
import hashlib
import json
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import folium
import pandas as pd
import requests
import streamlit as st
from geopy.exc import GeocoderTimedOut, GeocoderUnavailable
from geopy.geocoders import Nominatim
from streamlit_folium import st_folium
import streamlit.components.v1 as components


from helpers import (
    normalize_df,
    add_distance,
    add_opening_hours_features,
    select_candidates,
    ors_walking_route_coords,
    build_map_html,
)

# ---------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------
st.set_page_config(page_title="Pubcrawl Planner", layout="centered")

# Hardcoded ORS key (as requested)
ORS_API_KEY = "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6IjBjZTg0MmEwMDk0NjRkY2RiNzYzM2Q0NjBiZmJhN2EwIiwiaCI6Im11cm11cjY0In0="

CSV_REL_PATH = Path("data") / "regensburg_bars_backup.csv"
DEFAULT_CARDS_PATH = "data/cards.json"
DEFAULT_PROGRESS_PATH = "data/progress.json"


# ---------------------------------------------------------------------
# Session State
# ---------------------------------------------------------------------
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
    st.session_state["prefs"] = {"food": False, "football": False, "karaoke": False}

# card assignment per bar for current route
if "card_assignment" not in st.session_state:
    st.session_state["card_assignment"] = {}  # {bar_name: card_id}

# progress is global (same for all users) if persisted; local fallback works too
if "progress" not in st.session_state:
    st.session_state["progress"] = {}

if "progress_loaded" not in st.session_state:
    st.session_state["progress_loaded"] = False

# internal SHAs for GitHub contents updates
if "__cards_sha" not in st.session_state:
    st.session_state["__cards_sha"] = None
if "__progress_sha" not in st.session_state:
    st.session_state["__progress_sha"] = None


# ---------------------------------------------------------------------
# Utility Helpers
# ---------------------------------------------------------------------
def reset_all():
    st.session_state["page"] = "input"
    st.session_state["user_lat"] = None
    st.session_state["user_lon"] = None
    st.session_state["route_df"] = None
    st.session_state["card_assignment"] = {}


def _stable_seed(*parts: str) -> int:
    s = "|".join(parts)
    h = hashlib.sha256(s.encode("utf-8")).hexdigest()
    return int(h[:8], 16)


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

def load_df() -> pd.DataFrame:
    root = Path(__file__).resolve().parent.parent  # repo root (…/kdap_pubcrawl)
    target_name = "regensburg_bars_backup.csv"

    candidates = [
        root / "data" / target_name,
        root / "Data" / target_name,       # Case-Variante
        root / target_name,                # falls doch im Root
        root / "src" / "data" / target_name,  # falls versehentlich unter src/data
    ]

    for p in candidates:
        if p.exists():
            st.success(f"CSV gefunden: {p}")
            return pd.read_csv(p)

    # Debug: Was ist wirklich vorhanden?
    st.error("CSV nicht gefunden. Debug-Infos:")

    st.write("repo_root:", str(root))
    st.write("root exists:", root.exists())
    st.write("root contents:", sorted([x.name for x in root.iterdir()]) if root.exists() else [])

    data_dir = root / "data"
    st.write("data_dir:", str(data_dir))
    st.write("data_dir exists:", data_dir.exists())
    if data_dir.exists():
        st.write("data_dir contents:", sorted([x.name for x in data_dir.iterdir()]))

    # Rekursive Suche
    hits = list(root.rglob(target_name))
    st.write("rglob hits:", [str(h) for h in hits])

    st.stop()



# ---------------------------------------------------------------------
# Geocoding (robust + cached)
# ---------------------------------------------------------------------
@st.cache_data(ttl=24 * 3600, show_spinner=False)
def geocode_address(addr: str) -> Tuple[Optional[float], Optional[float]]:
    """
    Robust geocoding via Nominatim with retries, higher timeout and caching.
    Returns (lat, lon) or (None, None).
    """
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

    # Keep last error for optional UI diagnostics
    st.session_state["geocode_last_error"] = str(last_err) if last_err else None
    return None, None


# ---------------------------------------------------------------------
# GitHub persistence (optional) + local fallback
# ---------------------------------------------------------------------
def _gh_headers() -> Optional[Dict[str, str]]:
    token = st.secrets.get("GH_TOKEN", None)
    if not token:
        return None
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _gh_repo_info() -> Tuple[Optional[str], Optional[str], str]:
    repo = st.secrets.get("GH_REPO", "")
    branch = st.secrets.get("GH_BRANCH", "main")
    if not repo or "/" not in repo:
        return None, None, branch
    owner, name = repo.split("/", 1)
    return owner, name, branch


def gh_get_json(path: str, default: Any) -> Tuple[Any, Optional[str]]:
    """
    Returns (data, sha). Uses GitHub if secrets configured; else returns local file (repo) if available.
    """
    headers = _gh_headers()
    owner, repo, branch = _gh_repo_info()

    # If GH is configured, use GitHub contents API
    if headers and owner and repo:
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
        r = requests.get(url, headers=headers, params={"ref": branch}, timeout=20)
        if r.status_code == 404:
            return default, None
        r.raise_for_status()
        payload = r.json()
        sha = payload.get("sha")
        content_b64 = payload.get("content", "")
        if not content_b64:
            return default, sha
        raw = base64.b64decode(content_b64).decode("utf-8")
        try:
            return json.loads(raw), sha
        except Exception:
            return default, sha

    # Local fallback (useful for local development)
    local_path = (repo_root() / path).resolve()
    if local_path.exists():
        try:
            return json.loads(local_path.read_text(encoding="utf-8")), None
        except Exception:
            return default, None

    return default, None


def gh_put_json(path: str, data: Any, sha: Optional[str], commit_message: str) -> None:
    """
    Writes to GitHub if configured; otherwise writes to local repo file (for local dev).
    """
    headers = _gh_headers()
    owner, repo, branch = _gh_repo_info()

    raw = json.dumps(data, ensure_ascii=False, indent=2)

    # GitHub write
    if headers and owner and repo:
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
        content_b64 = base64.b64encode(raw.encode("utf-8")).decode("utf-8")
        body: Dict[str, Any] = {"message": commit_message, "content": content_b64, "branch": branch}
        if sha:
            body["sha"] = sha
        r = requests.put(url, headers=headers, json=body, timeout=20)
        r.raise_for_status()
        return

    # Local dev fallback
    local_path = (repo_root() / path).resolve()
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_text(raw, encoding="utf-8")


def load_cards() -> List[Dict[str, str]]:
    path = st.secrets.get("GH_PATH_CARDS", DEFAULT_CARDS_PATH)
    data, sha = gh_get_json(path, default=[])
    st.session_state["__cards_sha"] = sha

    cards: List[Dict[str, str]] = []
    if isinstance(data, list):
        for x in data:
            if isinstance(x, dict) and x.get("title") and x.get("task"):
                cid = str(x.get("id") or "").strip()
                title = str(x["title"]).strip()
                task = str(x["task"]).strip()
                cards.append({"id": cid, "title": title, "task": task})

    # ensure IDs
    for i, c in enumerate(cards):
        if not c["id"]:
            c["id"] = f"c{i+1}"

    # minimal defaults if empty
    if not cards:
        cards = [
            {"id": "c1", "title": "Icebreaker", "task": "Jeder nennt eine Fun-Fact-Lüge und die Gruppe errät, was stimmt."},
            {"id": "c2", "title": "Foto-Challenge", "task": "Macht ein Gruppenfoto mit einem Fremden (höflich fragen)."},
        ]
    return cards


def save_cards(cards: List[Dict[str, str]]) -> None:
    path = st.secrets.get("GH_PATH_CARDS", DEFAULT_CARDS_PATH)
    cleaned: List[Dict[str, str]] = []

    for c in cards:
        title = str(c.get("title", "")).strip()
        task = str(c.get("task", "")).strip()
        if not title or not task:
            continue
        cid = str(c.get("id", "")).strip() or f"c{len(cleaned)+1}"
        cleaned.append({"id": cid, "title": title, "task": task})

    sha = st.session_state.get("__cards_sha")
    gh_put_json(path, cleaned, sha, commit_message="Update cards deck via Streamlit")

    # refresh sha (only meaningful in GH mode)
    _, new_sha = gh_get_json(path, default=cleaned)
    st.session_state["__cards_sha"] = new_sha


def load_progress() -> Dict[str, bool]:
    path = st.secrets.get("GH_PATH_PROGRESS", DEFAULT_PROGRESS_PATH)
    data, sha = gh_get_json(path, default={})
    st.session_state["__progress_sha"] = sha
    return data if isinstance(data, dict) else {}


def save_progress(progress: Dict[str, bool]) -> None:
    path = st.secrets.get("GH_PATH_PROGRESS", DEFAULT_PROGRESS_PATH)
    sha = st.session_state.get("__progress_sha")
    gh_put_json(path, progress, sha, commit_message="Update progress via Streamlit")
    _, new_sha = gh_get_json(path, default=progress)
    st.session_state["__progress_sha"] = new_sha


def ensure_progress_loaded() -> None:
    if not st.session_state["progress_loaded"]:
        try:
            st.session_state["progress"] = load_progress()
        except Exception:
            st.session_state["progress"] = {}
        st.session_state["progress_loaded"] = True


# ---------------------------------------------------------------------
# Cards assignment + Editor UI
# ---------------------------------------------------------------------
def assign_cards_to_route(route_df: pd.DataFrame, cards: List[Dict[str, str]]) -> None:
    if not cards:
        return

    # stable per day (same route day -> same deck order), avoids duplicates when possible
    day_str = datetime.now().strftime("%Y-%m-%d")
    seed = _stable_seed("deck", day_str)
    rnd = random.Random(seed)

    deck = cards.copy()
    rnd.shuffle(deck)

    used = set(st.session_state["card_assignment"].values())

    for i, r in route_df.iterrows():
        bar_key = str(r.get("name", f"Bar_{i+1}"))
        if bar_key in st.session_state["card_assignment"]:
            continue

        pick = None
        for c in deck:
            if c["id"] not in used:
                pick = c
                break
        if pick is None:
            pick = deck[i % len(deck)]

        st.session_state["card_assignment"][bar_key] = pick["id"]
        used.add(pick["id"])


def deck_editor_ui() -> None:
    st.subheader("Karten-Deck verwalten")

    try:
        cards = load_cards()
    except Exception as e:
        st.error(f"Deck konnte nicht geladen werden: {e}")
        return

    df = pd.DataFrame(cards) if cards else pd.DataFrame(columns=["id", "title", "task"])
    if "id" not in df.columns:
        df["id"] = ""

    edited = st.data_editor(
        df[["id", "title", "task"]],
        width="stretch",
        num_rows="dynamic",
        column_config={
            "id": st.column_config.TextColumn("id", disabled=True),
            "title": st.column_config.TextColumn("Titel"),
            "task": st.column_config.TextColumn("Aufgabe"),
        },
    )

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Deck speichern", width="stretch"):
            cleaned = []
            for _, row in edited.iterrows():
                title = str(row.get("title") or "").strip()
                task = str(row.get("task") or "").strip()
                if not title or not task:
                    continue
                cid = str(row.get("id") or "").strip()
                cleaned.append({"id": cid, "title": title, "task": task})

            try:
                save_cards(cleaned)
                st.success("Deck gespeichert.")
                st.session_state["card_assignment"] = {}
                st.rerun()
            except Exception as e:
                st.error(f"Speichern fehlgeschlagen: {e}")

    with c2:
        if st.button("Progress zurücksetzen", width="stretch"):
            ensure_progress_loaded()
            st.session_state["progress"] = {}
            try:
                save_progress({})
                st.success("Progress zurückgesetzt.")
                st.rerun()
            except Exception as e:
                st.error(f"Reset fehlgeschlagen: {e}")

# INPUT PAGE

if st.session_state["page"] == "input":
    st.title("KDAP Pubcrawl Application")

    st.write("Location:")
    address = st.text_input("Address", value="Regensburg")

    # 4 columns in ONE row directly under the address field
    col_k, col_food, col_sports, col_surprise = st.columns([1.2, 1, 1, 1])

    with col_k:
        st.markdown("**Bars to visit**")
        k = st.slider(
            "k",
            1, 20,
            int(st.session_state["k"]),
            1,
            label_visibility="collapsed"
        )

    with col_food:
        st.markdown("**Preferences**")
        #st.write("")  # ← wichtiger Spacer
        food = st.toggle("🍔 Food", value=st.session_state["prefs"]["food"])

    with col_sports:
        st.markdown("&nbsp;", unsafe_allow_html=True)
        #st.write("")  # gleicher Spacer
        football = st.toggle("⚽ Sportsbar", value=st.session_state["prefs"]["football"])

    with col_surprise:
        st.markdown("&nbsp;", unsafe_allow_html=True)
        #st.write("")
        surprise = st.toggle("🎤 Surprise", value=st.session_state["prefs"]["karaoke"])


    st.session_state["k"] = int(k)
    st.session_state["prefs"] = {"food": food, "football": football, "karaoke": surprise}

    b1, b2 = st.columns(2)
    with b1:
        do_calc = st.button("**Let's Go!**", use_container_width=True)
    with b2:
        do_reset = st.button("Reset", use_container_width=True)

    if do_reset:
        reset_all()
        st.rerun()


    #with st.expander("Karten verwalten (global)", expanded=False):
    #    deck_editor_ui()

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

            candidates = select_candidates(df, st.session_state["k"])
            route_df = candidates.head(st.session_state["k"]).copy().reset_index(drop=True)
            st.session_state["route_df"] = route_df
            st.session_state["map_html"] = build_map_html(
                        user_lat,
                        user_lon,
                        route_df
                    )


            # reset assignment each time you plan new route
            st.session_state["card_assignment"] = {}

        st.session_state["page"] = "map"
        st.rerun()


# MAP PAGE
elif st.session_state["page"] == "map":
    st.title("Your route:")

    route_df = st.session_state["route_df"]
    user_lat = st.session_state["user_lat"]
    user_lon = st.session_state["user_lon"]

    if route_df is None or user_lat is None or user_lon is None:
        st.error("lookslike there's no route available, maybe you find a house party or your preferences are too special ...")
        st.button("back", width="stretch", on_click=reset_all)
        st.stop()

    ensure_progress_loaded()

    # Load cards (global)
    try:
        cards = load_cards()
    except Exception:
        cards = []
    cards_by_id = {c["id"]: c for c in cards}

    assign_cards_to_route(route_df, cards)

    # Collapsible map
    with st.expander("show/hide map", expanded=True):
        if "map_html" in st.session_state:
            components.html(
                st.session_state["map_html"],
                height=650
            )
        else:
            st.warning("Missing map... try to reload it")

    st.subheader("Bar tour order")
    show_cols = [c for c in ["name", "distance_m", "open_now"] if c in route_df.columns]
    st.dataframe(route_df[show_cols], width="stretch", hide_index=True)

    st.divider()

    st.subheader("Tasks per bar")
    progress_changed = False

    for i, r in route_df.iterrows():
        bar_name = str(r.get("name", f"Bar {i+1}"))
        card_id = st.session_state["card_assignment"].get(bar_name)
        card = cards_by_id.get(card_id) if card_id else None

        with st.expander(f"{i+1}. {bar_name}", expanded=(i == 0)):
            meta = []
            if "distance_m" in r:
                meta.append(f"Distanz: {format_distance_m(r['distance_m'])}")
            if "open_now" in r:
                meta.append(f"Offen: {'Ja' if bool(r['open_now']) else 'Unklar/Nein'}")
            if meta:
                st.caption(" • ".join(meta))

            if not card:
                st.info("Keine Karte zugewiesen (Deck leer oder nicht geladen).")
                continue

            done_key = f"{bar_name}:{card['id']}"
            done_val = bool(st.session_state["progress"].get(done_key, False))

            new_done = st.checkbox("Erledigt", value=done_val, key=f"chk_{done_key}")
            if new_done != done_val:
                st.session_state["progress"][done_key] = new_done
                progress_changed = True

            # Card look + strikethrough when done
            st.markdown(
                """
                <div style="
                    border: 1px solid rgba(0,0,0,0.12);
                    border-radius: 14px;
                    padding: 12px 14px;
                    margin-top: 10px;
                    background: rgba(0,0,0,0.02);
                ">
                """,
                unsafe_allow_html=True,
            )

            if new_done:
                st.markdown(f"**<s>{card['title']}</s>**", unsafe_allow_html=True)
                st.markdown(f"<s>{card['task']}</s>", unsafe_allow_html=True)
            else:
                st.markdown(f"**{card['title']}**")
                st.write(card["task"])

            st.markdown("</div>", unsafe_allow_html=True)

    st.divider()

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Progress speichern", width="stretch", disabled=not progress_changed):
            try:
                save_progress(st.session_state["progress"])
                st.success("Progress gespeichert.")
            except Exception as e:
                st.error(f"Speichern fehlgeschlagen: {e}")

    with c2:
        st.button("Neu planen", width="stretch", on_click=reset_all)
