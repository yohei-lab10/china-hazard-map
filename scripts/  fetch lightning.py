"""
落雷密度データ取得スクリプト
出典: WWLLN Global Lightning Climatology (WGLC) - University of Calgary / Zenodo
https://zenodo.org/records/10725446 (0.5度グリッド、日次・月次の落雷密度 [strokes/km2/day])

事前準備:
    pip install xarray netCDF4 requests

手順:
    1. 上記ZenodoページからNetCDFファイル(月次 or 気候値)を手動でダウンロードし、
       scripts/wglc_data/ 配下に置いてください(ライセンス上、都度URLを確認の上で取得することを推奨)。
    2. 本スクリプトでNetCDFを読み込み、中国域を切り出してGeoJSON(グリッドポイント)に変換します。

実行例:
    python fetch_lightning.py --nc-file wglc_data/WGLC_monthly_climatology.nc --out ../data/lightning.geojson
"""
import argparse
import json

import xarray as xr

CHINA_BBOX = {"minlat": 18, "maxlat": 54, "minlon": 73, "maxlon": 135}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nc-file", type=str, required=True, help="ダウンロード済みのWGLC NetCDFファイルパス")
    ap.add_argument("--out", type=str, default="../data/lightning.geojson")
    args = ap.parse_args()

    ds = xr.open_dataset(args.nc_file)

    # 変数名はファイルにより異なるため確認が必要(例: 'density', 'stroke_density' など)
    var_name = [v for v in ds.data_vars][0]
    da = ds[var_name]

    # 中国域に切り出し(緯度が降順の場合があるため両対応)
    da = da.sel(
        lat=slice(CHINA_BBOX["maxlat"], CHINA_BBOX["minlat"]),
        lon=slice(CHINA_BBOX["minlon"], CHINA_BBOX["maxlon"]),
    )

    # 時間次元があれば年平均を取る
    if "time" in da.dims:
        da = da.mean(dim="time")

    features = []
    lats = da["lat"].values
    lons = da["lon"].values
    values = da.values

    for i, lat in enumerate(lats):
        for j, lon in enumerate(lons):
            val = float(values[i, j])
            if val <= 0:
                continue
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
                "properties": {"density": val},
            })

    geojson = {"type": "FeatureCollection", "features": features}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)

    print(f"{len(features)}件の落雷密度グリッドを {args.out} に保存しました")


if __name__ == "__main__":
    main()
