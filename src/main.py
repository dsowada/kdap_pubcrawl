from datetime import datetime
import notebook
import pandas as pd
import requests
from helpers import normalize_df, add_distance, add_opening_hours_features, select_candidates

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
#OVERPASS_URL = "https://overpass.openstreetmap.ru/api/interpreter"

def get_bars_regensburg_df(center_lat=49.019533, center_lon=12.097487, radius_m=1200):
    query = f"""
    [out:json][timeout:60];
    (
      nwr["amenity"="bar"](around:{radius_m},{center_lat},{center_lon});
      nwr["amenity"="pub"](around:{radius_m},{center_lat},{center_lon});
    );
    out center tags;
    """
    r = requests.get(OVERPASS_URL, params={"data": query}, timeout=90)
    r.raise_for_status()
    data = r.json()

    rows = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        rows.append({
            "name": tags.get("name"),
            "amenity": tags.get("amenity"),
            "lat": lat,
            "lon": lon,
            "opening_hours": tags.get("opening_hours"),
            #"website": tags.get("website") or tags.get("contact:website"),
            #"phone": tags.get("phone") or tags.get("contact:phone"),
            "street": tags.get("addr:street"),
            "housenumber": tags.get("addr:housenumber"),
            "postcode": tags.get("addr:postcode"),
            "city": tags.get("addr:city"),
            #"osm_type": el.get("type"),
            #"osm_id": el.get("id"),
        })

    #df = pd.DataFrame(rows).dropna(subset=["lat","lon"])
    return df



def main():
    user_lat, user_lon = 49.0200, 12.0950
    k = 4
    # maybe gewichtung noch einstellen 
    #df = get_bars_regensburg_df(center_lat=49.019533, center_lon=12.097487, radius_m=1200)
    df = pd.read_csv("../data/regensburg_bars_backup.csv")
    df = normalize_df(df)

    df = add_distance(df, user_lat, user_lon)
    df = add_opening_hours_features(df, now=datetime.now())

    candidates = select_candidates(df, k)

    print(candidates[["name", "distance_m", "opening_hours_raw", "open_now", "open_score"]].head(10))


if __name__ == "__main__":
    main()
