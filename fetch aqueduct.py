"""
Aqueduct Floods(WRI)データ取得スクリプト
出典: World Resources Institute (WRI) — Aqueduct Floods Hazard Maps
https://www.wri.org/data/aqueduct-floods

考え方:
- WRIが公開する河川洪水(inunriver)のGeoTIFF(浸水深、単位m)を1ファイルダウンロードし、
  中国域の各グリッド点の浸水深をサンプリングして0〜1のリスク値に変換する
- デフォルトでは「historical(現在の気候)・100年に1度の再現期間」のシナリオを使用する。
  他のシナリオ(将来気候・別の再現期間)を使いたい場合は引数で切り替え可能
- ライセンス: WRIのオープンデータ方針により利用制限なし(出典明記を推奨)

浸水深→リスク変換のしきい値(暫定・実務用の簡易区分):
    0m(浸水なし)      → risk = 0.0
    0〜0.5m           → risk = 0.3
    0.5〜1.0m         → risk = 0.6
    1.0m以上          → risk = 1.0

事前準備:
    pip install rasterio requests numpy

実行例:
    python fetch_aqueduct.py --scenario historical --model 000000000WATCH --year 1980 --rp 100 --grid-deg 1.0 --out ../data/aqueduct_floods.geojson

注意:
- 解像度は約10km四方(WRI公表値)と粗い。ローカルな詳細地形は反映されない
- 1ファイルのみを使用しており、複数の再現期間・将来シナリオを比較する機能は本スクリプトには無い
  (将来的に必要であれば、複数ファイルを取得して重ね合わせる改修を検討する)
"""
import argparse
import io
import json

import numpy as np
import rasterio
import requests

CHINA_BBOX = {"minlat": 18, "maxlat": 54, "minlon": 73, "maxlon": 135}

AQUEDUCT_BASE_URL = "https://aqueduct.wridata.org/AqueductFloods20/"


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


def depth_to_risk(depth) -> float:
    if depth is None or depth <= 0:
        return 0.0
    if depth < 0.5:
        return 0.3
    if depth < 1.0:
        return 0.6
    return 1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", type=str, default="historical", help="気候シナリオ(historical, rcp4p5, rcp8p5 等)")
    ap.add_argument("--model", type=str, default="000000000WATCH", help="モデル名(historicalの場合は000000000WATCH固定)")
    ap.add_argument("--year", type=int, default=1980, help="対象年(historicalは1980固定。将来シナリオは2030/2050/2080)")
    ap.add_argument("--rp", type=int, default=100, help="再現期間(年)。5,10,25,50,100,250,500,1000から選択")
    ap.add_argument("--grid-deg", type=float, default=1.0, help="グリッド間隔(度)")
    ap.add_argument("--out", type=str, default="../data/aqueduct_floods.geojson")
    args = ap.parse_args()

    filename = f"inunriver_{args.scenario}_{args.model}_{args.year}_rp{args.rp:05d}.tif"
    url = AQUEDUCT_BASE_URL + filename
    print(f"[fetch_aqueduct] ダウンロード中: {url}", flush=True)

    res = requests.get(url, timeout=300)
    res.raise_for_status()
    print(f"[fetch_aqueduct] ダウンロード完了: {len(res.content)} bytes", flush=True)

    grid_points = build_grid(args.grid_deg)
    print(f"[fetch_aqueduct] グリッド点数: {len(grid_points)}", flush=True)

    features = []
    with rasterio.open(io.BytesIO(res.content)) as src:
        band = src.read(1)
        nodata = src.nodata
        for lat, lon in grid_points:
            try:
                row, col = src.index(lon, lat)
                if row < 0 or row >= band.shape[0] or col < 0 or col >= band.shape[1]:
                    raise IndexError
                val = band[row, col]
                if nodata is not None and val == nodata:
                    no_data = True
                    depth = None
                else:
                    no_data = False
                    depth = float(val)
            except IndexError:
                no_data = True
                depth = None

            risk = None if no_data else depth_to_risk(depth)
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "inundation_depth_m": depth,
                    "risk": risk,
                    "no_data": no_data,
                    "scenario": args.scenario,
                    "return_period_years": args.rp,
                },
            })

    geojson = {"type": "FeatureCollection", "features": features}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)

    covered = sum(1 for f in features if not f["properties"]["no_data"])
    print(f"[fetch_aqueduct] {covered}/{len(grid_points)}件でデータ取得成功。{args.out} に保存しました", flush=True)


if __name__ == "__main__":
    main()
