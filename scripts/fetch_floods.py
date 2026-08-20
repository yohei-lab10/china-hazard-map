"""
洪水データ取得スクリプト
出典: Dartmouth Flood Observatory (DFO) — Global Flood Records (Zenodo, 2026年3月公開)
https://zenodo.org/records/19288171
1985年1月〜2023年12月、5,513件のグローバル洪水記録(CC0/CC-BY-4.0)

DFO公式サイトは2026年にリニューアルされ、現在はこのZenodoデータセットが
正式な配布元になっている。以前のfloodobservatory.colorado.edu上のCSVとは
列名が異なるため、新しい列名(Start Date, End Date, Country, Area (km²),
Fatalities, Displaced, Severity 等)に対応させている。

事前準備:
    pip install pandas requests

実行例:
    python fetch_floods.py --csv-url "https://zenodo.org/records/19288171/files/Global_Flood_Records.csv?download=1" --out ../data/floods.geojson
"""
import argparse
import json
import io
import requests
import pandas as pd

# 中国全土のバウンディングボックス
CHINA_BBOX = {"minlat": 18, "maxlat": 54, "minlon": 73, "maxlon": 135}

# 列名の候補(バージョンにより変わる可能性があるため複数持たせる)
LAT_COL_CANDIDATES = ["lat", "Centroid Y", "Latitude", "latitude"]
LON_COL_CANDIDATES = ["long", "lon", "Centroid X", "Longitude", "longitude"]
AREA_COL_CANDIDATES = ["Area (km²)", "Area", "Affected Sq Km", "Area_km2"]
SEVERITY_COL_CANDIDATES = ["Severity", "Severity *"]
BEGAN_COL_CANDIDATES = ["Start Date", "Began"]
ENDED_COL_CANDIDATES = ["End Date", "Ended"]
CAUSE_COL_CANDIDATES = ["Main Cause", "MainCause"]
DEAD_COL_CANDIDATES = ["Fatalities", "Dead"]
DISPLACED_COL_CANDIDATES = ["Displaced"]
COUNTRY_COL_CANDIDATES = ["Country"]


def find_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def fetch_csv(url: str) -> pd.DataFrame:
    res = requests.get(url, timeout=120)
    res.raise_for_status()
    return pd.read_csv(io.StringIO(res.text), low_memory=False)


def filter_china(df: pd.DataFrame) -> pd.DataFrame:
    country_col = find_column(df, COUNTRY_COL_CANDIDATES)
    if country_col:
        df = df[df[country_col].astype(str).str.contains("China", case=False, na=False)]

    lat_col = find_column(df, LAT_COL_CANDIDATES)
    lon_col = find_column(df, LON_COL_CANDIDATES)
    if not lat_col or not lon_col:
        raise RuntimeError(f"緯度経度カラムが見つかりません。実際の列名: {list(df.columns)}")

    df[lat_col] = pd.to_numeric(df[lat_col], errors="coerce")
    df[lon_col] = pd.to_numeric(df[lon_col], errors="coerce")
    df = df[
        (df[lat_col] >= CHINA_BBOX["minlat"]) & (df[lat_col] <= CHINA_BBOX["maxlat"]) &
        (df[lon_col] >= CHINA_BBOX["minlon"]) & (df[lon_col] <= CHINA_BBOX["maxlon"])
    ]
    return df.rename(columns={lat_col: "lat", lon_col: "lon"})


def to_geojson(df: pd.DataFrame) -> dict:
    area_col = find_column(df, AREA_COL_CANDIDATES)
    severity_col = find_column(df, SEVERITY_COL_CANDIDATES)
    began_col = find_column(df, BEGAN_COL_CANDIDATES)
    ended_col = find_column(df, ENDED_COL_CANDIDATES)
    cause_col = find_column(df, CAUSE_COL_CANDIDATES)
    dead_col = find_column(df, DEAD_COL_CANDIDATES)
    displaced_col = find_column(df, DISPLACED_COL_CANDIDATES)

    print(f"[fetch_floods] 使用カラム: area={area_col}, severity={severity_col}, "
          f"began={began_col}, ended={ended_col}, cause={cause_col}", flush=True)

    features = []
    for _, row in df.iterrows():
        area = pd.to_numeric(row.get(area_col), errors="coerce") if area_col else None
        severity = pd.to_numeric(row.get(severity_col), errors="coerce") if severity_col else None
        dead = pd.to_numeric(row.get(dead_col), errors="coerce") if dead_col else None
        displaced = pd.to_numeric(row.get(displaced_col), errors="coerce") if displaced_col else None

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [row["lon"], row["lat"]]},
            "properties": {
                "began": row.get(began_col) if began_col else None,
                "ended": row.get(ended_col) if ended_col else None,
                "dead": None if dead is None or pd.isna(dead) else float(dead),
                "displaced": None if displaced is None or pd.isna(displaced) else float(displaced),
                "affected_sq_km": None if area is None or pd.isna(area) else float(area),
                "severity": None if severity is None or pd.isna(severity) else float(severity),
                "main_cause": row.get(cause_col) if cause_col else None,
            },
        })
    return {"type": "FeatureCollection", "features": features}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv-url", type=str, required=True, help="DFO Global Flood RecordsのCSVダウンロードURL")
    ap.add_argument("--out", type=str, default="../data/floods.geojson")
    args = ap.parse_args()

    df = fetch_csv(args.csv_url)
    print(f"[fetch_floods] 全世界の記録: {len(df)}件", flush=True)

    df = filter_china(df)
    print(f"[fetch_floods] 中国域にフィルタ後: {len(df)}件", flush=True)

    geojson = to_geojson(df)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)

    print(f"[fetch_floods] {len(geojson['features'])}件の洪水イベントを {args.out} に保存しました", flush=True)


if __name__ == "__main__":
    main()
