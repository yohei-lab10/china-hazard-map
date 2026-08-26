#!/usr/bin/env python3
"""
fetch_rainfall.py (2026年8月改訂版)

【変更点】
旧版は「直近N日(既定180日)の最大24時間降水量」を集計するだけの近似値だった。
これは台風のCategory1+発生頻度と同じ問題を抱えていた: 短期間のスナップショットを
そのまま「危険度」として扱っており、統計的な「何年に一度」という評価になっていなかった。

新版は、水文学で標準的な「年最大値法(Annual Maximum Method)」を使う:
  1. 過去N年(既定20年)、各年の「年間最大24時間降水量」を1個ずつ求める
  2. その年最大値の系列(20年なら20個)にGumbel分布をフィットする
  3. Gumbel分布から、任意の閾値(50/100/250mm)に対する再現期間(年)を逆算できるようにする

出力GeoJSONの各グリッド点には、フィット済みのGumbelパラメータ(mu, beta)を格納する。
再現期間の計算自体はブラウザ側(index.html)で行う(パラメータ2つだけなので軽量)。

【なぜ20年か】
極値統計(Gumbel/GEVフィッティング)には最低20〜30年分が推奨される(WMOガイドライン等の一般的な目安)。
10年だと「10年に一度」相当の再現期間を返すことになり、観測期間ぴったりの外挿になってしまう。
40年(CHIRPSの全アーカイブ、1981年〜)を使わなかったのは、日次データの取得量が
20年でも約7,300日分になり、GitHub Actionsのtimeout(120分)を圧迫するため
(詳細は下記「実行時間について」を参照)。まずは20年で運用し、実行時間に余裕があれば
--years を増やす形を想定している。

【実行時間についての重要な注意】
旧版(直近180日)は実測で約2分〜だったが、20年(約7,300日)は単純計算で約40倍、
つまり数時間規模になる可能性が高い。GitHub Actionsのworkflow timeoutを
大幅に延長するか、下記のいずれかの対策が必要:
  (a) 年ごとに分割し、複数回のworkflow実行に分ける(例: 5年ずつ×4回)
  (b) 取得済みの年別最大値をリポジトリ内にキャッシュし(data/rainfall_annual_max_cache.json)、
      2回目以降は不足分の年だけ追加取得する(本スクリプトはこのキャッシュ機構を実装済み)
  (c) CHIRPSの日次プロダクトではなく、取得量の少ないペンタド(5日ごと)/月別プロダクトで
      近似する(精度は落ちる)
本スクリプトはデフォルトで(b)のキャッシュ機構を使うため、2回目以降の実行は
不足している年(通常は最新1年分のみ)だけを取得すればよく、実行時間は大幅に短縮される。
初回実行時のみ20年分をまとめて取得するため長時間かかる点に留意すること。

使い方:
  python fetch_rainfall.py --years 20 --grid-deg 1.0 --out data/rainfall_risk.geojson
  python fetch_rainfall.py --years 20 --grid-deg 1.0 --out data/rainfall_risk.geojson --cache data/rainfall_annual_max_cache.json
"""

import argparse
import json
import math
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import requests

CHIRPS_MAX_LAT = 50.0  # CHIRPSの有効カバー範囲(北緯50度〜南緯50度)
CHIRPS_BASE_URL = (
    "https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_daily/tifs/p05"
)

# 中国域のおおよそのバウンディングボックス(西/南/東/北)
CHINA_BBOX = (73.0, 18.0, 135.0, 54.0)


def rainfall_to_risk(mm):
    """中国気象局の暴雨分類基準(24時間降水量)に基づくリスク変換(表示用、従来通り)"""
    if mm is None:
        return None
    if mm < 50:
        return 0.0
    if mm < 100:
        return 0.33  # 暴雨
    if mm < 250:
        return 0.66  # 大暴雨
    return 1.0  # 特大暴雨


def fit_gumbel(annual_maxima):
    """
    年最大値の系列(list of float, 単位mm)にGumbel分布をモーメント法でフィットする。
    返り値: (mu, beta) 位置・尺度パラメータ。データが少なすぎる場合はNoneを返す。

    Gumbel分布の累積分布関数: F(x) = exp(-exp(-(x-mu)/beta))
    モーメント法: beta = sqrt(6) * stdev / pi,  mu = mean - 0.5772 * beta (0.5772 = オイラー定数)
    """
    n = len(annual_maxima)
    if n < 5:  # 5年未満ではフィット自体が無意味なため打ち切る
        return None
    arr = np.array(annual_maxima, dtype=float)
    mean = arr.mean()
    stdev = arr.std(ddof=1)  # 不偏標準偏差
    if stdev <= 0:
        return None
    beta = math.sqrt(6) * stdev / math.pi
    mu = mean - 0.5772156649 * beta
    return mu, beta


def return_period_years(mm, mu, beta):
    """Gumbel分布のパラメータから、降水量mmに対応する再現期間(年)を計算する"""
    if beta <= 0:
        return None
    F = math.exp(-math.exp(-(mm - mu) / beta))
    if F >= 1.0:
        return None  # 数値的にオーバーフローする極端な値
    p_exceed = 1 - F
    if p_exceed <= 0:
        return None
    return 1 / p_exceed


def load_cache(cache_path):
    if cache_path and Path(cache_path).exists():
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache_path, cache):
    if not cache_path:
        return
    Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f)


def fetch_daily_raster_max_per_cell(day, grid_points):
    """
    指定日のCHIRPS日次ラスタ(.tif.gz)をダウンロードし、各グリッド点における
    降水量(mm)を読み取る。ラスタの読み取りには rasterio を使う。
    ネットワーク障害時は None のリストを返す(その日はスキップされる)。
    """
    import rasterio
    from rasterio.io import MemoryFile

    url = f"{CHIRPS_BASE_URL}/{day.year}/chirps-v2.0.{day.strftime('%Y.%m.%d')}.tif.gz"
    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [WARN] {day}: ダウンロード失敗 ({e})、この日はスキップ", file=sys.stderr)
        return [None] * len(grid_points)

    import gzip
    import io

    raw = gzip.decompress(resp.content)
    values = []
    with MemoryFile(raw) as memfile:
        with memfile.open() as src:
            for lat, lon in grid_points:
                try:
                    row, col = src.index(lon, lat)
                    val = src.read(1)[row, col]
                    # CHIRPSのno-data値(-9999)を除外
                    values.append(float(val) if val > -9000 else None)
                except (IndexError, ValueError):
                    values.append(None)
    return values


def build_grid_points(bbox, grid_deg):
    west, south, east, north = bbox
    points = []
    lat = south
    while lat <= north:
        lon = west
        while lon <= east:
            if abs(lat) <= CHIRPS_MAX_LAT:
                points.append((round(lat, 4), round(lon, 4)))
            lon += grid_deg
        lat += grid_deg
    return points


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--years", type=int, default=20, help="年最大値を集計する年数(既定20年)")
    parser.add_argument("--grid-deg", type=float, default=1.0, help="グリッドの間隔(度、既定1.0)")
    parser.add_argument("--out", type=str, default="data/rainfall_risk.geojson", help="出力GeoJSONパス")
    parser.add_argument(
        "--cache", type=str, default="data/rainfall_annual_max_cache.json",
        help="年別最大値のキャッシュファイル(2回目以降の実行を高速化する)"
    )
    args = parser.parse_args()

    today = date.today()
    target_years = list(range(today.year - args.years, today.year))  # 直近の完全な年、今年は速報値として別途扱う

    grid_points = build_grid_points(CHINA_BBOX, args.grid_deg)
    print(f"グリッド点数: {len(grid_points)}(間隔{args.grid_deg}度)")
    print(f"対象年: {target_years[0]}〜{target_years[-1]}({len(target_years)}年分)")

    cache = load_cache(args.cache)
    # cache構造: { "lat,lon": { "2006": 88.4, "2007": 120.1, ... } }

    years_to_fetch = [y for y in target_years if not _cache_has_all_points(cache, grid_points, y)]
    if years_to_fetch:
        print(f"未取得の年: {years_to_fetch}(この年数分だけ新規にダウンロードする)")
    else:
        print("すべての年がキャッシュ済み。ダウンロードをスキップして統計計算のみ実行する。")

    for year in years_to_fetch:
        print(f"--- {year}年を処理中 ---")
        year_max = {pt: None for pt in grid_points}
        d = date(year, 1, 1)
        end = date(year, 12, 31)
        while d <= end:
            daily_values = fetch_daily_raster_max_per_cell(d, grid_points)
            for pt, val in zip(grid_points, daily_values):
                if val is not None:
                    if year_max[pt] is None or val > year_max[pt]:
                        year_max[pt] = val
            d += timedelta(days=1)
        for pt, val in year_max.items():
            key = f"{pt[0]},{pt[1]}"
            cache.setdefault(key, {})[str(year)] = val
        save_cache(args.cache, cache)  # 年単位でこまめに保存(途中で落ちても再開できるように)
        print(f"  {year}年 完了")

    # Gumbelフィット + 出力GeoJSON生成
    features = []
    for lat, lon in grid_points:
        key = f"{lat},{lon}"
        yearly = cache.get(key, {})
        maxima = [yearly[str(y)] for y in target_years if yearly.get(str(y)) is not None]

        if not maxima:
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {"no_data": True},
            })
            continue

        fit = fit_gumbel(maxima)
        # 直近の年最大値を「現在のリスク表示」用に引き続き使う(旧版との互換性維持)
        latest_year = max(int(y) for y in yearly.keys()) if yearly else None
        latest_mm = yearly.get(str(latest_year)) if latest_year else None
        risk = rainfall_to_risk(latest_mm)

        props = {
            "risk": risk,
            "years_used": len(maxima),
        }
        if fit is not None:
            mu, beta = fit
            props["gumbel_mu"] = round(mu, 3)
            props["gumbel_beta"] = round(beta, 3)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": props,
        })

    geojson = {"type": "FeatureCollection", "features": features}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(geojson, f)
    print(f"出力完了: {args.out}({len(features)}地点)")


def _cache_has_all_points(cache, grid_points, year):
    for pt in grid_points:
        key = f"{pt[0]},{pt[1]}"
        if cache.get(key, {}).get(str(year)) is None:
            return False
    return True


if __name__ == "__main__":
    main()
