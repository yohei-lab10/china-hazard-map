"""
洪水データ取得スクリプト
出典: Dartmouth Flood Observatory (DFO) Global Active Archive of Large Flood Events
https://floodobservatory.colorado.edu/Archives/index.html

DFOは現在(2026年)サイトリニューアル中のため、配布形式が変わる可能性があります。
実行前に下記URLで最新のダウンロードリンク(CSV/GeoJSON/Shapefile)を確認してください:
    https://floodobservatory.colorado.edu/dfo-wiki/index.php?title=Main_Page

代替ソース(GEEアカウントがある場合、より高精度):
    Global Flood Database (Cloud to Street x DFO, MODIS実測ベース)
    https://global-flood-database.cloudtostreet.ai/
    → Google Earth Engineの ee.ImageCollection("GLOBAL_FLOOD_DB/MODIS_EVENTS/V1") から
      国コード(China)で絞り込み、浸水ポリゴンをGeoJSONにエクスポートする運用を推奨。

事前準備:
    pip install pandas requests

実行例:
    python fetch_floods.py --csv-url <DFOのダウンロードURL> --out ../data/floods.geojson
"""
import argparse
import json
import io
import requests
import pandas as pd

# 中国全土のバウンディングボックス
CHINA_BBOX = {"minlat": 18, "maxlat": 54, "minlon": 73, "maxlon": 135}

# DFOのCSVはバージョンによって列名が異なるため、候補を複数持たせて最初に見つかったものを使う
AREA_COL_CANDIDATES = ["Affected Sq Km", "Area", "Area_km2", "AffectedArea"]
SEVERITY_COL_CANDIDATES = ["Severity", "Severity *"]


def fetch_csv(url: str) -> pd.DataFrame:
    res = requests.get(url, timeout=120)
    res.raise_for_status()
    return pd.read_csv(io.StringIO(res.text), low_memory=False)


def find_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def filter_china(df: pd.DataFrame) -> pd.DataFrame:
    # DFOのCSVは列名が "Country" "long" "lat" 等(バージョンにより異なるため要確認)
    if "Country" in df.columns:
        df = df[df["Country"].astype(str).str.contains("China", case=False, na=False)]
    lat_col = "lat" if "lat" in df.columns else "Centroid Y"
    lon_col = "long" if "long" in df.columns else "Centroid X"
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
    print(f"[fetch_floods] 冠水面積カラム: {area_col}, 深刻度カラム: {severity_col}")

    features = []
    for _, row in df.iterrows():
        dead = pd.to_numeric(row.get("Dead"), errors="coerce")
        displaced = pd.to_numeric(row.get("Displaced"), errors="coerce")
        area = pd.to_numeric(row.get(area_col), errors="coerce") if area_col else None
        severity = pd.to_numeric(row.get(severity_col), errors="coerce") if severity_col else None

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [row["lon"], row["lat"]]},
            "properties": {
                "began": row.get("Began"),
                "ended": row.get("Ended"),
                "dead": None if pd.isna(dead) else float(dead),
                "displaced": None if pd.isna(displaced) else float(displaced),
                "affected_sq_km": None if area is None or pd.isna(area) else float(area),
                "severity": None if severity is None or pd.isna(severity) else float(severity),
                "main_cause": row.get("MainCause"),
            },
        })
    return {"type": "FeatureCollection", "features": features}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv-url", type=str, required=True, help="DFOアーカイブのCSVダウンロードURL")
    ap.add_argument("--out", type=str, default="../data/floods.geojson")
    args = ap.parse_args()

    df = fetch_csv(args.csv_url)
    df = filter_china(df)
    geojson = to_geojson(df)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)

    print(f"[fetch_floods] {len(geojson['features'])}件の洪水イベントを {args.out} に保存しました")


if __name__ == "__main__":
    main()
