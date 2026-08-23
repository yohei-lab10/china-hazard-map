"""
fetch_lightning.py

雷ハザードのデータをWGLC(WWLLN Global Lightning Climatology)から取得し、
中国域のグリッド点ごとの相対リスク値(0〜1)としてGeoJSON化するスクリプト。

【データソース】
WWLLN Global Lightning Climatology (WGLC) v2024.0.0 (Kaplan & Lau, 2021/2022)
University of Calgary / 元 University of Hong Kong
https://doi.org/10.5281/zenodo.10725446
2010〜2023年の実測(WWLLN)を集計した月別気候値(多年平均)。0.5度格子。
CC BY-SA 4.0(要クレジット表示)。

【従来のLIGHTNING_SAMPLE(コード内ハードコード10点)との違い】
従来はコード内に手打ちした10地点のみのサンプルデータだったが、本スクリプトにより
実測ベースの格子データ(中国域で数百点規模)に置き換える。ただし地震のような
「個々のイベント記録」ではなく、あくまで「多年平均の落雷密度(気候値)」である点に注意。
(個々の落雷イベント記録は無料公開されていないため)

【リスク値への変換方法】
降雨強度(CHIRPS)のような公的な絶対基準(中国気象局の暴雨分類など)が雷には存在しないため、
洪水実績(DFO)と同様に「中国域内での相対正規化」を採用する:
  risk = min(1.0, density / (中国域内の97パーセンタイル値))
既存の97パーセンタイル値超えは全て1.0に切り詰める(外れ値による偏りを避けるため)。

【出力】
data/lightning.geojson (中国域のみ、各点 properties.risk = 0.0〜1.0)

【使い方】
pip install xarray netCDF4 requests numpy
python fetch_lightning.py --out data/lightning.geojson
"""

import argparse
import json
import sys

import numpy as np
import requests
import xarray as xr

WGLC_URL = "https://zenodo.org/records/10725446/files/wglc_climatology_30m_monthly.nc?download=1"

# 中国全土を覆うバウンディングボックス(経度, 緯度)。他のスクリプトと合わせて少し余裕を持たせている。
CHINA_BBOX = {"lon_min": 72.0, "lon_max": 137.0, "lat_min": 17.0, "lat_max": 54.5}

# 既知の変数名候補(WGLCの配布ファイルはバージョンにより名称が変わることがあるため複数試す)
DENSITY_VAR_CANDIDATES = ["density", "stroke_density", "lightning_density", "strokes"]


def find_density_var(ds: xr.Dataset) -> str:
    for name in DENSITY_VAR_CANDIDATES:
        if name in ds.data_vars:
            return name
    # 候補に一致しない場合、(month, lat, lon)らしき3次元変数を自動検出する
    for name, da in ds.data_vars.items():
        if da.ndim == 3:
            print(f"注意: 既知の変数名候補に一致しませんでした。'{name}' を密度変数として使用します。", file=sys.stderr)
            return name
    raise RuntimeError(
        f"密度変数が見つかりませんでした。ファイル内の変数一覧: {list(ds.data_vars)}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nc-file", default=None,
                         help="既にダウンロード済みのNetCDFファイルパス(指定時はダウンロードをスキップ)")
    parser.add_argument("--out", default="data/lightning.geojson")
    parser.add_argument("--percentile", type=float, default=97.0,
                         help="リスク値1.0に対応させる中国域内のパーセンタイル(外れ値対策)")
    args = parser.parse_args()

    nc_path = args.nc_file
    if nc_path is None:
        nc_path = "wglc_climatology_30m_monthly.nc"
        print(f"downloading: {WGLC_URL}")
        resp = requests.get(WGLC_URL, timeout=1800)
        resp.raise_for_status()
        with open(nc_path, "wb") as f:
            f.write(resp.content)
        print(f"saved: {nc_path} ({len(resp.content) / 1e6:.1f} MB)")

    ds = xr.open_dataset(nc_path)
    var_name = find_density_var(ds)
    da = ds[var_name]

    # 月次気候値(12ヶ月)を年間平均に集約。次元名は "month" を想定(異なる場合は自動検出)。
    time_dim = "month" if "month" in da.dims else da.dims[0]
    annual_mean = da.mean(dim=time_dim, skipna=True)

    # 経度が0〜360で格納されている場合は-180〜180に変換
    lon_name = "lon" if "lon" in annual_mean.dims else "longitude"
    lat_name = "lat" if "lat" in annual_mean.dims else "latitude"
    lons = annual_mean[lon_name].values
    if lons.max() > 180:
        annual_mean = annual_mean.assign_coords({lon_name: (((annual_mean[lon_name] + 180) % 360) - 180)})
        annual_mean = annual_mean.sortby(lon_name)

    # 中国域に切り出す
    clipped = annual_mean.sel(
        {lon_name: slice(CHINA_BBOX["lon_min"], CHINA_BBOX["lon_max"]),
         lat_name: slice(CHINA_BBOX["lat_min"], CHINA_BBOX["lat_max"])}
    )
    # 緯度が降順(90→-90)格納の場合、上のsliceが空になるので反転して再試行
    if clipped.sizes.get(lat_name, 0) == 0:
        clipped = annual_mean.sel(
            {lon_name: slice(CHINA_BBOX["lon_min"], CHINA_BBOX["lon_max"]),
             lat_name: slice(CHINA_BBOX["lat_max"], CHINA_BBOX["lat_min"])}
        )

    values = clipped.values
    valid = values[~np.isnan(values)]
    if valid.size == 0:
        raise RuntimeError("中国域内に有効なデータがありませんでした。バウンディングボックスや変数名を確認してください。")

    ref_max = float(np.percentile(valid, args.percentile))
    print(f"中国域 第{args.percentile}パーセンタイル値: {ref_max:.4f} (これ以上をrisk=1.0に切り詰め)")

    # to_dataframe()を使うことで、次元(lat/lon)の並び順に依存せず安全に(緯度,経度,値)の組を取り出す
    df = clipped.to_dataframe(name="density").reset_index()
    df = df.dropna(subset=["density"])
    df = df[df["density"] > 0]

    features = []
    for _, row in df.iterrows():
        v = float(row["density"])
        risk = min(1.0, v / ref_max) if ref_max > 0 else 0.0
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(row[lon_name]), float(row[lat_name])]},
            "properties": {"risk": round(risk, 4), "density_raw": round(v, 4)},
        })

    geojson = {"type": "FeatureCollection", "features": features}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False)
    print(f"saved: {args.out} ({len(features)} points)")


if __name__ == "__main__":
    main()
