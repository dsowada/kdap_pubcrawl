# app.py
import streamlit as st
import pandas as pd
from datetime import datetime
import folium
from streamlit_folium import st_folium
from geopy.geocoders import Nominatim
import random
import hashlib
from pathlib import Path
from helpers import (
    normalize_df,
    add_distance,
    add_opening_hours_features,
    select_candidates,
    ors_walking_route_coords,
    load_cards,
    save_cards,
    load_progress,
    save_progress,
)

# -----------------------------
# Page config (mobile-friendly)
# -----------------------------
st.set_page_config(page_title="Pubcrawl Planner", layout="centered")

# -----------------------------
# Session state init
# -----------------------------
if "page" not in st.session_state:
    st.session_state["page"] = "input"

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

# Card assignment per bar (stable within session)
if "card_assignment" not in st.session_state:
    st.session_state["card_assignment"] = {}  # {bar_name: card_id}

# Progress is persistent (GitHub JSON)
if "progress" not in st.session_state:
    st.session_state["progress"] = {}  # loaded from GH once

if "progress_loaded" not in st.session_state:
    st.session_state["progress_loaded"] = False


# -----------------------------
# Helpers
# -----------------------------
def reset_all():
    st.session_state["page"] = "input"
    st.session_state["user_lat"] = None
    st.session_state["user_lon"] = None
    st.session_state["route_df"] = None
    st.session_state["card_assignment"] = {}


def geocode_address(addr: str):
    geolocator = Nominatim(user_agent="pubcrawl-planner")
    loc = geolocator.geocode(addr)
    if loc is None:
        return None, None
    return float(loc.latitude), float(loc.longitude)




def load_df() -> pd.DataFrame:
    base = Path(__file__).resolve().parent  # Ordner von app.py
    csv_path = (base / ".." / "data" / "regensburg_bars_backup.csv").resolve()

    if not csv_path.exists():
        # Debug-Hilfe (zeigt dir in Streamlit sofort, wo er sucht)
        st.error(f"CSV nicht gefunden unter: {csv_path}")
        st.info(f"Aktueller Ordner: {Path.cwd()}")
        st.stop()

    return pd.read_csv(csv_path)



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


def ensure_progress_loaded():
    if not st.session_state["progress_loaded"]:
        try:
            st.session_state["progress"] = load_progress()
        except Exception:
            # If GH not configured or error occurs, fall back to in-memory
            st.session_state["progress"] = {}
        st.session_state["progress_loaded"] = True


def assign_cards_to_route(route_df: pd.DataFrame, cards: list[dict]):
    """
    Assigns one card per bar (bar_name -> card_id). Stable per day, tries to avoid duplicates.
    """
    if not cards:
        return

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


def deck_editor_ui():
    """
    Editable deck stored persistently via GitHub (helpers.load_cards/save_cards).
    """
    st.subheader("Karten-Deck verwalten")

    try:
        cards = load_cards()
    except Exception as e:
        st.error(f"Deck konnte nicht geladen werden (GitHub Secrets gesetzt?): {e}")
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

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Deck speichern", width="stretch",):
            # re-create cards list
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
                # Reset assignment so new deck may apply
                st.session_state["card_assignment"] = {}
                st.rerun()
            except Exception as e:
                st.error(f"Speichern fehlgeschlagen: {e}")

    with col2:
        if st.button("Progress zurücksetzen", width="stretch",):
            ensure_progress_loaded()
            st.session_state["progress"] = {}
            try:
                save_progress({})
                st.success("Progress zurückgesetzt.")
                st.rerun()
            except Exception as e:
                st.error(f"Reset fehlgeschlagen: {e}")


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

    st.session_state["k"] = int(k)
    st.session_state["prefs"] = {"food": food, "football": football, "karaoke": karaoke}

    col_a, col_b = st.columns(2)
    with col_a:
        do_calc = st.button("Route berechnen", width="stretch",)
    with col_b:
        do_reset = st.button("Reset", width="stretch",

    if do_reset:
        reset_all()
        st.rerun()

    with st.expander("Karten verwalten (persistiert in GitHub)", expanded=False):
        deck_editor_ui()

    if do_calc:
        with st.spinner("Suche Standort..."):
            user_lat, user_lon = geocode_address(address)

        if user_lat is None or user_lon is None:
            st.error("Adresse nicht gefunden. Bitte genauer eingeben.")
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
        st.button("Zurück", width="stretch",, on_click=reset_all)
        st.stop()

    ensure_progress_loaded()

    # Load cards deck (persistent)
    try:
        cards = load_cards()
    except Exception:
        cards = []

    cards_by_id = {c["id"]: c for c in cards}
    assign_cards_to_route(route_df, cards)

    # Collapsible map
    with st.expander("Karte anzeigen / ausblenden", expanded=False):
        m = folium.Map(location=[user_lat, user_lon], zoom_start=14)

        folium.Marker([user_lat, user_lon], tooltip="Start", popup="Start").add_to(m)

        for i, r in route_df.iterrows():
            lat, lon = float(r["lat"]), float(r["lon"])
            folium.Marker(
                [lat, lon],
                tooltip=f"{i+1}. {r['name']}",
                popup=f"{i+1}. {r['name']}",
            ).add_to(m)

        # ORS hardcoded (as requested)
        ORS_API_KEY = "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6IjBjZTg0MmEwMDk0NjRkY2RiNzYzM2Q0NjBiZmJhN2EwIiwiaCI6Im11cm11cjY0In0="

        route_points = [(user_lat, user_lon)] + [
            (float(r["lat"]), float(r["lon"])) for _, r in route_df.iterrows()
        ]

        for i in range(len(route_points) - 1):
            seg = ors_walking_route_coords(ORS_API_KEY, route_points[i], route_points[i + 1])
            folium.PolyLine(seg).add_to(m)

        st_folium(m, width=360, height=650)

    st.divider()

    st.subheader("Reihenfolge")
    show_cols = [c for c in ["name", "distance_m", "open_now"] if c in route_df.columns]
    st.dataframe(route_df[show_cols], width="stretch",, hide_index=True)

    st.divider()

    st.subheader("Karten pro Bar")

    # Render per-bar card with checkbox + strikethrough
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

            # Card look
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

    # Persist progress to GitHub
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Progress speichern", width="stretch", disabled=not progress_changed):
            try:
                save_progress(st.session_state["progress"])
                st.success("Progress gespeichert.")
            except Exception as e:
                st.error(f"Speichern fehlgeschlagen: {e}")

    with col2:
        st.button("Neu planen", width="stretch",, on_click=reset_all)
