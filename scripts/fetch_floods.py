"""
洪水データ取得スクリプト
出典: Dartmouth Flood Observatory (DFO) — Global Flood Records (Zenodo, 2026年3月公開)
https://zenodo.org/records/19288171
1985年1月〜2023年12月、5,513件のグローバル洪水記録(CC0/CC-BY-4.0)

注意: 配布されているCSVには緯度・経度の列が含まれていない。
位置情報(ポリゴン・ポイント)は同梱の Global_Flood_Records.gpkg (GeoPackage形式、
地理空間データの標準フォーマット)にのみ含まれているため、本スクリプトは
CSVではなくこの.gpkgファイルを直接読み込む。各洪水イベントの図形(ポリゴン等)の
重心(セントロイド)を代表点として使用する。

事前準備:
    pip install geopandas requests

実行例:
    python fetch_floods.py --gpkg-url "https://zenodo.org/records/19288171/files/Global_Flood_Records.gpkg?download=1" --out ../data/floods.geojson
"""
import argparse
import io
import json

import geopandas as gpd
import requests

# 中国全土のバウンディングボックス
CHINA_BBOX = {"minlat": 18, "maxlat": 54, "minlon": 73, "maxlon": 135}

AREA_COL_CANDIDATES = ["Area (km²)", "Area", "Area_km2"]
SEVERITY_COL_CANDIDATES = ["Severity"]
BEGAN_COL_CANDIDATES = ["Start Date", "Began"]
ENDED_COL_CANDIDATES = ["End Date", "Ended"]
CAUSE_COL_CANDIDATES = ["Main Cause", "MainCause"]
DEAD_COL_CANDIDATES = ["Fatalities", "Dead"]
DISPLACED_COL_CANDIDATES = ["Displaced"]
COUNTRY_COL_CANDIDATES = ["Country"]


def find_column(columns, candidates):
    for c in candidates:
        if c in columns:
            return c
    return None


def fetch_gpkg(url: str) -> "gpd.GeoDataFrame":
    print(f"[fetch_floods] GeoPackageをダウンロード中: {url}", flush=True)
    res = requests.get(url, timeout=180)
    res.raise_for_status()
    gdf = gpd.read_file(io.BytesIO(res.content))
    print(f"[fetch_floods] 読み込み完了: {len(gdf)}件, カラム: {list(gdf.columns)}", flush=True)
    return gdf


def filter_china(gdf: "gpd.GeoDataFrame") -> "gpd.GeoDataFrame":
    country_col = find_column(gdf.columns, COUNTRY_COL_CANDIDATES)
    if country_col:
        gdf = gdf[gdf[country_col].astype(str).str.contains("China", case=False, na=False)]

    # 図形の重心(セントロイド)を代表点の緯度経度として使う
    centroids = gdf.geometry.centroid
    gdf = gdf.assign(lat=centroids.y, lon=centroids.x)

    gdf = gdf[
        (gdf["lat"] >= CHINA_BBOX["minlat"]) & (gdf["lat"] <= CHINA_BBOX["maxlat"]) &
        (gdf["lon"] >= CHINA_BBOX["minlon"]) & (gdf["lon"] <= CHINA_BBOX["maxlon"])
    ]
    return gdf


def to_geojson(gdf: "gpd.GeoDataFrame") -> dict:
    area_col = find_column(gdf.columns, AREA_COL_CANDIDATES)
    severity_col = find_column(gdf.columns, SEVERITY_COL_CANDIDATES)
    began_col = find_column(gdf.columns, BEGAN_COL_CANDIDATES)
    ended_col = find_column(gdf.columns, ENDED_COL_CANDIDATES)
    cause_col = find_column(gdf.columns, CAUSE_COL_CANDIDATES)
    dead_col = find_column(gdf.columns, DEAD_COL_CANDIDATES)
    displaced_col = find_column(gdf.columns, DISPLACED_COL_CANDIDATES)

    print(f"[fetch_floods] 使用カラム: area={area_col}, severity={severity_col}, "
          f"began={began_col}, ended={ended_col}, cause={cause_col}", flush=True)

    features = []
    for _, row in gdf.iterrows():
        def num(col):
            if not col:
                return None
            try:
                v = float(row.get(col))
                return None if v != v else v  # NaN check
            except (TypeError, ValueError):
                return None

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [row["lon"], row["lat"]]},
            "properties": {
                "began": str(row.get(began_col)) if began_col else None,
                "ended": str(row.get(ended_col)) if ended_col else None,
                "dead": num(dead_col),
                "displaced": num(displaced_col),
                "affected_sq_km": num(area_col),
                "severity": num(severity_col),
                "main_cause": row.get(cause_col) if cause_col else None,
            },
        })
    return {"type": "FeatureCollection", "features": features}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gpkg-url", type=str, required=True, help="DFO Global Flood RecordsのGeoPackage(.gpkg)ダウンロードURL")
    ap.add_argument("--out", type=str, default="../data/floods.geojson")
    args = ap.parse_args()

    gdf = fetch_gpkg(args.gpkg_url)
    gdf = filter_china(gdf)
    print(f"[fetch_floods] 中国域にフィルタ後: {len(gdf)}件", flush=True)

    geojson = to_geojson(gdf)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)

    print(f"[fetch_floods] {len(geojson['features'])}件の洪水イベントを {args.out} に保存しました", flush=True)


if __name__ == "__main__":
    main()
