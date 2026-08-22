"""
降雨強度データ取得スクリプト(水害ハザード用)
出典: CHIRPS (Climate Hazards Group InfraRed Precipitation with Station data)
      UCサンタバーバラ大学 Climate Hazards Center
https://data.chc.ucsb.edu/products/CHIRPS-2.0/

考え方:
- CHIRPSの日次降水量ラスタ(GeoTIFF)を一定期間分ダウンロードし、
  中国域の各グリッド点について「観測された最大24時間降水量」を集計する
- その値を、中国気象局の公式な暴雨分類基準(24時間降水量)で5段階のリスク値に変換する
    50mm未満          → risk = 0.0(暴雨未満)
    50〜99.9mm(暴雨)   → risk = 0.33
    100〜249.9mm(大暴雨) → risk = 0.66
    250mm以上(特大暴雨) → risk = 1.0
- CHIRPSは北緯50度〜南緯50度のみをカバーするため、それより北(黒竜江省北端の一部)は
  データ欠損として "no_data": true を明示的に出力する(サイト側で灰色表示するため)

注意:
- CHIRPSの全期間(1981年〜現在)・全世界の日次データは膨大なため、本スクリプトは
  --days で指定した直近日数分のみをダウンロードして「観測された最大値」の近似値とする。
  長期間の真の統計的極値ではなく、指定期間内での近似値である点に留意すること。
  より長期・高精度な統計が必要な場合は、CHIRPSの月次データを複数年分処理する
  よう改修するか、中国気象局の実測データへの切り替えを検討する。

事前準備:
    pip install rasterio requests numpy

実行例:
    python fetch_rainfall.py --days 180 --grid-deg 1.0 --out ../data/rainfall_risk.geojson
"""
import argparse
import io
import json
import time
from datetime import datetime, timedelta

import numpy as np
import rasterio
import requests

# 中国全土のバウンディングボックス
CHINA_BBOX = {"minlat": 18, "maxlat": 54, "minlon": 73, "maxlon": 135}

# CHIRPSの有効カバー範囲(北緯50度〜南緯50度)
CHIRPS_MAX_LAT = 50.0

CHIRPS_DAILY_URL_TMPL = (
    "https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_daily/tifs/p05/"
    "{year}/chirps-v2.0.{year}.{month:02d}.{day:02d}.tif.gz"
)


def build_grid(grid_deg: float):
    points = []
    lat = CHINA_BBOX["minlat"]
    while lat <= CHINA_BBOX["maxlat"]:
        lon = CHINA_BBOX["minlon"]
        while lon <= CHINA_BBOX["maxlon"]:
            points.append((round(lat, 4), round(lon, 4)))
            lon += grid_deg
        lat += grid_deg
    return points


def fetch_daily_max(days: int, grid_points: list) -> dict:
    """指定日数分のCHIRPS日次ラスタをダウンロードし、各グリッド点の最大値を集計する"""
    import gzip

    max_values = {p: None for p in grid_points}
    end = datetime.now()
    dates = [end - timedelta(days=i) for i in range(days)]

    for i, d in enumerate(dates):
        url = CHIRPS_DAILY_URL_TMPL.format(year=d.year, month=d.month, day=d.day)
        try:
            res = requests.get(url, timeout=60)
            if res.status_code != 200:
                continue
            raw = gzip.decompress(res.content)
            with rasterio.open(io.BytesIO(raw)) as src:
                band = src.read(1)
                nodata = src.nodata
                for lat, lon in grid_points:
                    if abs(lat) > CHIRPS_MAX_LAT:
                        continue  # カバー範囲外はスキップ(後でno_data扱いにする)
                    try:
                        row, col = src.index(lon, lat)
                        val = band[row, col]
                        if nodata is not None and val == nodata:
                            continue
                        if max_values[(lat, lon)] is None or val > max_values[(lat, lon)]:
                            max_values[(lat, lon)] = float(val)
                    except IndexError:
                        continue
        except requests.exceptions.RequestException as e:
            print(f"[fetch_rainfall] {d.date()} の取得に失敗(スキップ): {e}", flush=True)
        if (i + 1) % 30 == 0:
            print(f"[fetch_rainfall] {i + 1}/{days}日分を処理済み", flush=True)
        time.sleep(0.2)

    return max_values


def rainfall_to_risk(mm) -> float:
    """中国気象局の暴雨分類基準(24時間降水量)に基づくリスク変換"""
    if mm is None:
        return None
    if mm < 50:
        return 0.0
    if mm < 100:
        return 0.33
    if mm < 250:
        return 0.66
    return 1.0


def to_geojson(grid_points: list, max_values: dict) -> dict:
    features = []
    for lat, lon in grid_points:
        out_of_coverage = abs(lat) > CHIRPS_MAX_LAT
        mm = max_values.get((lat, lon))
        risk = None if out_of_coverage else rainfall_to_risk(mm)

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "max_rainfall_mm": mm,
                "risk": risk,
                "no_data": out_of_coverage or mm is None,
            },
        })
    return {"type": "FeatureCollection", "features": features}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=180, help="直近何日分のCHIRPS日次データを集計するか")
    ap.add_argument("--grid-deg", type=float, default=1.0, help="グリッド間隔(度)")
    ap.add_argument("--out", type=str, default="../data/rainfall_risk.geojson")
    args = ap.parse_args()

    grid_points = build_grid(args.grid_deg)
    print(f"[fetch_rainfall] グリッド点数: {len(grid_points)}", flush=True)

    max_values = fetch_daily_max(args.days, grid_points)
    geojson = to_geojson(grid_points, max_values)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)

    covered = sum(1 for f in geojson["features"] if not f["properties"]["no_data"])
    print(f"[fetch_rainfall] {covered}/{len(grid_points)}件で降雨データ取得成功。{args.out} に保存しました", flush=True)


if __name__ == "__main__":
    main()
