"""
fetch_aqueduct_depth.py (WRI直接配信サーバー版)

Aqueduct Floods(WRI)の地点検索専用データを取得するスクリプト。
Google Earth Engineは中国国内から利用できないため使用しない。
WRI自身が運用する配信サーバー(aqueduct.wridata.org)から直接GeoTIFFを取得し、
中国域にクリップしてCOG(Cloud Optimized GeoTIFF)として保存する。

【出典・URLパターンの根拠】
学術機関(CLIMAAX CRA Handbook)がこの配信元を実例コードで使用していることを確認済み:
https://handbook.climaax.eu/notebooks/workflows/FLOODS/02_River_flooding/Hazard_assessment_FLOOD_RIVER.html
ダウンロードURL: https://aqueduct.wridata.org/AqueductFloods20/{filename}
filename = inunriver_{scenario}_{model}_{year}_rp{returnperiod:05d}.tif

【注意】
このサーバー自体が中国国内から接続可能かどうかは未検証(Google系サービスではないため
可能性は高いが、確約はできない)。念のため実行環境から一度、下記のようなコマンドで
疎通確認してから本番実行することを推奨する:
  curl -I https://aqueduct.wridata.org/AqueductFloods20/inunriver_historical_000000000WATCH_1980_rp00010.tif
もし接続できない場合は、社内ネットワーク経由や海外拠点のマシンでの実行、
またはVPS(海外リージョン)を踏み台にした取得を検討すること。

【出力】
data/aqueduct_depth_rp{RP}.tif (RP = 10, 100 など、再現期間ごとに1ファイル、値=浸水深m)
"""

import argparse
import os
import sys

import rasterio
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="historical")
    parser.add_argument("--model", default="000000000WATCH")
    parser.add_argument("--year", type=int, default=1980)
    parser.add_argument("--rp", type=int, nargs="+", default=[10, 100],
                         help="再現期間(年)。複数指定可。例: --rp 10 100")
    parser.add_argument("--out-dir", default="data")
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    for rp in args.rp:
        out_path = os.path.join(args.out_dir, f"aqueduct_depth_rp{rp}.tif")
        try:
            download_and_clip(args.scenario, args.model, args.year, rp, out_path)
        except Exception as e:
            print(f"[rp={rp}] failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
