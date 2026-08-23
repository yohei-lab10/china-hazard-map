"""
fetch_aqueduct.py (WRI直接配信サーバー版)

Aqueduct Floods(WRI)のデータを取得するスクリプト。用途は2つ:
  1. 地点検索用: 中国域に切り出したCOG(Cloud Optimized GeoTIFF)。ブラウザ側(geotiff.js)が
     検索地点のピクセル値をその場で読み取り、浸水深(m)をそのまま表示する。
  2. 地図ヒートマップ用: 上記1と同じCOGを間引いてグリッド化し、浸水深(m)付きのGeoJSONとして
     出力する。地図全体をヒートマップで塗るのは1のCOGでは重すぎるため、荒いグリッドに変換している。
どちらも同じダウンロード元データ(1回のダウンロード)から生成するため、二重にWRIへ問い合わせない。

Google Earth Engineは中国国内から利用できないため使用しない。
WRI自身が運用する配信サーバー(aqueduct.wridata.org)から直接GeoTIFFを取得する。

【出典・URLパターンの根拠】
学術機関(CLIMAAX CRA Handbook)がこの配信元を実例コードで使用していることを確認済み:
https://handbook.climaax.eu/notebooks/workflows/FLOODS/02_River_flooding/Hazard_assessment_FLOOD_RIVER.html
ダウンロードURL: https://aqueduct.wridata.org/AqueductFloods20/{filename}
filename = inunriver_{scenario}_{model}_{year}_rp{returnperiod:05d}.tif

【データの性質について、重要】
これは実際に起きた洪水イベントの実測記録ではなく、地形・河川流量モデルによる
「statistically, this depth could occur once every N years」という想定浸水深(シミュレーション)。
過去の実測データが必要な場合はDartmouth Flood Observatory(洪水実績、data/floods.geojson)を
参照すること。ただしそちらは浸水「面積」のみで、深度(m)は持っていない。

【注意】
このサーバー自体が中国国内から接続可能かどうかは未検証(Google系サービスではないため
可能性は高いが、確約はできない)。念のため実行環境から一度、下記のようなコマンドで
疎通確認してから本番実行することを推奨する:
  curl -I https://aqueduct.wridata.org/AqueductFloods20/inunriver_historical_000000000WATCH_1980_rp00010.tif
もし接続できない場合は、社内ネットワーク経由や海外拠点のマシンでの実行、
またはVPS(海外リージョン)を踏み台にした取得を検討すること。

【出力】
data/aqueduct_depth_rp{RP}.tif       … 地点検索用のCOG(値=浸水深m)
data/aqueduct_floods_rp{RP}.geojson  … 地図ヒートマップ用の間引きグリッド(properties.depth_m)
(RP = 10, 100 など、再現期間ごとに1組)
"""

import argparse
import json
import os
import sys

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.mask import mask
from rasterio.io import MemoryFile
import requests

# 中国全土をやや余裕を持って覆うバウンディングボックス(経度, 緯度)
# 地点検索で国境際を検索した場合の取りこぼしを避けるため、公式国境より少し広めに取っている
CHINA_BBOX_GEOMETRY = {
    "type": "Polygon",
    "coordinates": [[
        [72.0, 17.0], [137.0, 17.0], [137.0, 54.5], [72.0, 54.5], [72.0, 17.0],
    ]],
}

BASE_URL = "https://aqueduct.wridata.org/AqueductFloods20"


def download_and_clip(scenario: str, model: str, year: int, rp: int, out_path: str):
    model_padded = model.rjust(14, "0")  # WRIの命名規則: モデル名を14桁にゼロ埋め
    filename = f"inunriver_{scenario}_{model_padded}_{year}_rp{rp:05d}.tif"
    url = f"{BASE_URL}/{filename}"
    print(f"[rp={rp}] downloading: {url}")

    resp = requests.get(url, timeout=600)
    resp.raise_for_status()

    with MemoryFile(resp.content) as memfile:
        with memfile.open() as src:
            out_image, out_transform = mask(src, [CHINA_BBOX_GEOMETRY], crop=True)
            out_meta = src.meta.copy()
            out_meta.update({
                "driver": "COG",  # ブラウザ側(geotiff.js)からの部分読み込み(レンジリクエスト)に対応
                "height": out_image.shape[1],
                "width": out_image.shape[2],
                "transform": out_transform,
                "compress": "DEFLATE",
            })
            with rasterio.open(out_path, "w", **out_meta) as dst:
                dst.write(out_image)
    print(f"[rp={rp}] saved: {out_path} ({os.path.getsize(out_path) / 1e6:.1f} MB)")


def build_heatmap_grid(tif_path: str, out_path: str, grid_deg: float = 1.0):
    """
    地点検索用のCOG(約1km解像度)を間引いて、地図ヒートマップ用の粗いGeoJSONグリッドを作る。
    全ピクセルをそのままGeoJSON化すると数百万点になり非現実的なため、grid_deg(度)間隔に
    平均値でリサンプリングしてから点群に変換する(他のハザードのgrid-degと考え方は同じ)。
    """
    with rasterio.open(tif_path) as src:
        res_deg = abs(src.transform.a)  # 元データの1ピクセルあたりの度数(≒0.0083度)
        decim = max(1, round(grid_deg / res_deg))
        out_shape = (max(1, src.height // decim), max(1, src.width // decim))
        data = src.read(1, out_shape=out_shape, resampling=Resampling.average)
        # 間引き後のグリッドに合わせて変換行列(ピクセル→経緯度)をスケールし直す
        scaled_transform = src.transform * src.transform.scale(
            src.width / data.shape[-1], src.height / data.shape[-2]
        )

        features = []
        for row in range(data.shape[0]):
            for col in range(data.shape[1]):
                v = data[row, col]
                if np.isnan(v) or v <= 0 or v > 50:  # 50m超は異常値としてデータなし扱い
                    continue
                lon, lat = scaled_transform * (col + 0.5, row + 0.5)
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
                    "properties": {"depth_m": round(float(v), 3)},
                })

    geojson = {"type": "FeatureCollection", "features": features}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False)
    print(f"[heatmap grid] saved: {out_path} ({len(features)} points)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="historical")
    parser.add_argument("--model", default="000000000WATCH")
    parser.add_argument("--year", type=int, default=1980)
    parser.add_argument("--rp", type=int, nargs="+", default=[10, 100],
                         help="再現期間(年)。複数指定可。例: --rp 10 100")
    parser.add_argument("--out-dir", default="data")
    parser.add_argument("--heatmap-grid-deg", type=float, default=1.0,
                         help="ヒートマップ用グリッドの間引き間隔(度)。既定は他ハザードと合わせて1.0度")
    parser.add_argument("--skip-heatmap-grid", action="store_true",
                         help="地点検索用COGのみ生成し、ヒートマップ用GeoJSONは作らない")
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    for rp in args.rp:
        tif_path = os.path.join(args.out_dir, f"aqueduct_depth_rp{rp}.tif")
        try:
            download_and_clip(args.scenario, args.model, args.year, rp, tif_path)
        except Exception as e:
            print(f"[rp={rp}] failed: {e}", file=sys.stderr)
            continue

        if not args.skip_heatmap_grid:
            geojson_path = os.path.join(args.out_dir, f"aqueduct_floods_rp{rp}.geojson")
            try:
                build_heatmap_grid(tif_path, geojson_path, args.heatmap_grid_deg)
            except Exception as e:
                print(f"[rp={rp}] heatmap grid failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
