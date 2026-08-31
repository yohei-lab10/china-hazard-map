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

【サーバー側の拒否への対策(2026年8月追加・改訂)】
20年分を一気に取得しようとした際、CHIRPSサーバー(UCSB)から全日程で
「Connection refused」を返され、5時間半を空振りした事例があった。
その後の再実行では「ConnectTimeout(60秒待っても応答なし)」に変わり、
接続はできるが極端に遅い/不安定という状態が確認された。
このとき1日失敗するごとにリトライで35秒(5+10+20)を消費した結果、
1時間17分かけても1年目の9月までしか進まず、完走が不可能なペースだった。

そのため現在は「失敗した日は潔く諦める」方針に変更している:
  - MAX_RETRIES = 1(リトライなし)。1日欠けても年最大値への影響は軽微という割り切り
  - 接続タイムアウトを60→15秒に短縮(応答がないサーバーを長く待たない)
  - リクエスト間に0.3秒の待機を入れ、サーバーへの負荷を抑える
  - 50日連続で失敗したら処理を中断(無駄な空振りを防ぐ)

【月単位のチェックポイント(2026年8月追加)】
以前は1年分を取得し終えて初めてキャッシュに保存していたため、年の途中で
timeoutになるとその年の進捗が丸ごと失われていた。現在は月ごとに
キャッシュを保存・commit・pushするため、途中で打ち切られても次回は
その続きの月から再開できる。進捗位置はキャッシュ内の "_progress" キーに
{年: 完了した月} の形で記録される。
なお統計(Gumbelフィット)に使うのは12ヶ月すべて揃った年のみで、
途中までの暫定値は自動的に除外される(混ぜると年最大値を過小評価し、
再現期間を危険側に誤るため)。

【実行時間について】
旧版(直近180日)は実測で3分51秒だった(2026年8月、GitHub Actions #39)。
サーバーが安定していれば1日あたり約1.3秒で、10年分(3,650日)で約1.6時間の見込み。
ただしサーバーが不安定な時間帯は大幅に遅くなるため、月単位のチェックポイントを
活かして複数回に分けて実行することを想定している。
サーバーはカリフォルニア(UCSB)にあるため、現地の負荷が低い時間帯を狙うのも有効。

恒久的に接続できなくなった場合の代替案:
  (a) CHIRPSの日次プロダクトではなく、取得量の少ないペンタド(5日ごと)/月別プロダクトで
      近似する(精度は落ちるが取得量は1/5以下になる)
  (b) Google Earth Engine経由(UCSB-CHG/CHIRPS/DAILY)に切り替える。サービスアカウントで
      自動化可能だが、中国国内からは利用できない点に注意
  ※ IRI Data Library(iridl.ldeo.columbia.edu)は2026年8月時点で全ユーザーに
     サインインが必須化されており、自動取得には使えないことを確認済み

使い方:
  python fetch_rainfall.py --years 10 --grid-deg 1.0 --out data/rainfall_risk.geojson
  python fetch_rainfall.py --years 10 --grid-deg 1.0 --out data/rainfall_risk.geojson --cache data/rainfall_annual_max_cache.json
"""

import argparse
import calendar
import json
import math
import os
import subprocess
import sys
import time
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

# --- ダウンロードのリトライ・流量制御(2026年8月追加) ---
# 短時間に大量のリクエストを送るとCHIRPSサーバー側に拒否される事例があったため、
# リトライと待機を入れてサーバーに配慮する。詳細は
# fetch_daily_raster_max_per_cell() のdocstringを参照。
MAX_RETRIES = 1                  # 1日あたりの最大試行回数(2026年8月: 4→1に変更、下記参照)
RETRY_BASE_WAIT_SEC = 5          # 指数バックオフの初期待機秒数(MAX_RETRIES=1では未使用)
REQUEST_INTERVAL_SEC = 0.3       # 成功時も次のリクエストまでこれだけ待つ
CONNECT_TIMEOUT_SEC = 15         # 接続確立の待機上限(2026年8月: 60→15秒に短縮、下記参照)
READ_TIMEOUT_SEC = 60            # 接続後のデータ読み取りの待機上限
# 連続でこの日数分失敗したら、サーバー側の恒久的な問題とみなして処理全体を中断する。
# (2026年8月の事例のように、接続できない状態で数時間空振りし続けるのを防ぐため)
MAX_CONSECUTIVE_FAILURES = 50


def rainfall_to_risk(mm):
    """中国気象局の暴雨分類基準(24時間降水量)に基づくリスク変換。
    入力mmは呼び出し側で決める(2026年8月改訂: 10年分の年最大値の平均を渡す)。"""
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


def git_commit_push(path, message):
    """
    2026年8月追加: 年ごとの処理が終わるたびに、その年のキャッシュ差分だけを
    その場でコミット・pushする。理由: 20年分の取得は数時間かかることがあり、
    途中でGitHub Actionsのtimeoutに達すると強制終了される。従来はワークフロー側の
    「Commit and push if changed」ステップがスクリプト完走後にしか実行されなかったため、
    timeoutで打ち切られると、それまでの進捗(既に取得済みだった年のキャッシュ)が
    リポジトリに一切残らず、次回また1年目からやり直しになってしまっていた。
    毎年ここでコミット・pushしておけば、途中で打ち切られても、そこまでの年は
    保存され、次回実行時はそこから再開できる(save_cacheでの巻き戻り防止と対になる仕組み)。

    git操作に失敗しても(例: 一時的なネットワーク障害、pushの競合)、処理全体は
    止めずに警告を出して次の年に進む。失敗しても最終的にワークフロー側の
    「Commit and push if changed」ステップが最後にもう一度コミットを試みるため、
    多くの場合はそこで回収される。
    """
    try:
        subprocess.run(["git", "add", path], check=True)
        diff_result = subprocess.run(["git", "diff", "--staged", "--quiet"])
        if diff_result.returncode == 0:
            print(f"  (変更なし、コミットをスキップ: {path})")
            return
        subprocess.run(["git", "commit", "-m", message], check=True)
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=True)
        subprocess.run(["git", "push"], check=True)
        print(f"  git commit・push完了: {message}")
    except subprocess.CalledProcessError as e:
        print(f"  [WARN] git操作に失敗しました({e})。この年の進捗はローカルには残っているが、"
              f"リポジトリへの反映は次回の実行(または最終ステップ)に持ち越される。", file=sys.stderr)


def fetch_daily_raster_max_per_cell(day, grid_points):
    """
    指定日のCHIRPS日次ラスタ(.tif.gz)をダウンロードし、各グリッド点における
    降水量(mm)を読み取る。ラスタの読み取りには rasterio を使う。
    リトライを尽くしても取得できなければ None のリストを返す(その日はスキップされる)。

    【2026年8月追加: リトライ・スロットリング】
    20年分(約7,300日)を一気に取得しようとした際、CHIRPSサーバー(UCSB)から
    全日程で「Connection refused」を返され続け、5時間半を空振りした事例があった。
    その2日前に180日版(約180リクエスト、3分51秒)が問題なく成功していたことから、
    恒久的なIPブロックではなく、短時間に大量のリクエストを送ったことによる
    レート制限(あるいは一時的なサーバー障害)と判断した。
    そのため、(1)指数バックオフによるリトライ、(2)リクエスト間の待機、を追加している。
    """
    import rasterio
    from rasterio.io import MemoryFile

    url = f"{CHIRPS_BASE_URL}/{day.year}/chirps-v2.0.{day.strftime('%Y.%m.%d')}.tif.gz"

    resp = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, timeout=(CONNECT_TIMEOUT_SEC, READ_TIMEOUT_SEC))
            # 404は「その日のファイルが存在しない」ため、リトライしても無意味。即座に諦める
            if resp.status_code == 404:
                print(f"  [WARN] {day}: ファイルが存在しない(404)、この日はスキップ", file=sys.stderr)
                return [None] * len(grid_points)
            resp.raise_for_status()
            break  # 成功
        except requests.RequestException as e:
            if attempt < MAX_RETRIES - 1:
                # 指数バックオフ(例: 5秒 → 10秒 → 20秒 …)。サーバー側の一時的な
                # 拒否やレート制限は、時間を空けることで回復することが多い
                wait = RETRY_BASE_WAIT_SEC * (2 ** attempt)
                print(f"  [RETRY] {day}: 取得失敗({type(e).__name__})、{wait}秒待って再試行 "
                      f"({attempt + 1}/{MAX_RETRIES})", file=sys.stderr)
                time.sleep(wait)
            else:
                print(f"  [WARN] {day}: {MAX_RETRIES}回試行しても失敗 ({e})、この日はスキップ", file=sys.stderr)
                return [None] * len(grid_points)

    # サーバーに負荷をかけすぎないよう、成功時も次のリクエストまで少し待つ
    time.sleep(REQUEST_INTERVAL_SEC)

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

    consecutive_failures = 0
    aborted = False
    for year in years_to_fetch:
        if aborted:
            break
        print(f"--- {year}年を処理中 ---")
        ykey = str(year)
        # 【2026年8月改訂: 月単位の保存・再開】
        # 以前は1年分をすべて取得し終えてから初めてキャッシュに保存していたため、
        # 年の途中でtimeoutになるとその年の進捗が丸ごと失われていた(実際、1年目の
        # 9ヶ月目で1時間17分かかり、完走が絶望的になった事例がある)。
        # 現在は月ごとに「その月までの暫定的な年最大値」と「どこまで処理したか」を
        # 保存・commitするため、途中で止まっても翌回はその続きから再開できる。
        # 暫定値はcacheの本体("lat,lon" -> {年: 最大値})にそのまま積み増していく形で、
        # 進捗位置だけを別途 _progress キーに記録する。
        progress = cache.get("_progress", {})
        start_month = progress.get(ykey, 0) + 1  # 1〜12。完了済みの次の月から再開
        if start_month > 12:
            print(f"  {year}年はキャッシュ済みのためスキップ")
            continue
        if start_month > 1:
            print(f"  {start_month}月から再開します(1〜{start_month - 1}月は取得済み)")

        # 既に一部取得済みならその暫定値を引き継ぐ
        year_max = {}
        for pt in grid_points:
            key = f"{pt[0]},{pt[1]}"
            year_max[pt] = cache.get(key, {}).get(ykey)

        for month in range(start_month, 13):
            if aborted:
                break
            d = date(year, month, 1)
            end = date(year, month, calendar.monthrange(year, month)[1])
            while d <= end:
                daily_values = fetch_daily_raster_max_per_cell(d, grid_points)
                if all(v is None for v in daily_values):
                    consecutive_failures += 1
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        # サーバーに繋がらない状態で延々と試行し続けても意味がないため、
                        # ここまでに取得できた分をキャッシュに残して処理を打ち切る。
                        print(f"\n[ABORT] {MAX_CONSECUTIVE_FAILURES}日連続で取得に失敗しました。"
                              f"CHIRPSサーバー側の障害またはレート制限の可能性が高いため、処理を中断します。\n"
                              f"        ここまでの進捗はキャッシュに保存済みです。時間をおいて再実行してください。",
                              file=sys.stderr)
                        aborted = True
                        break
                else:
                    consecutive_failures = 0
                for pt, val in zip(grid_points, daily_values):
                    if val is not None:
                        if year_max[pt] is None or val > year_max[pt]:
                            year_max[pt] = val
                d += timedelta(days=1)

            # 月末(または中断時)に、ここまでの暫定的な年最大値を保存・commitする
            for pt, val in year_max.items():
                key = f"{pt[0]},{pt[1]}"
                cache.setdefault(key, {})[ykey] = val
            if not aborted:
                cache.setdefault("_progress", {})[ykey] = month
            save_cache(args.cache, cache)
            git_commit_push(args.cache, f"chore: rainfall annual max cache — {year}年{month}月まで [auto]")
            print(f"  {year}年{month}月まで完了")

        print(f"  {year}年 {'(中断、部分的)' if aborted else '完了'}")

    # Gumbelフィット + 出力GeoJSON生成
    # 【重要】統計に使うのは「12ヶ月すべて取得できた年」のみに限る。
    # 月単位の中断・再開に対応した結果、キャッシュには途中までしか取得していない年の
    # 暫定値も入りうる。それを年最大値として混ぜると、実際より小さい値でGumbel分布を
    # フィットすることになり、再現期間を過小評価(=危険側に誤る)してしまう。
    progress = cache.get("_progress", {})
    complete_years = [y for y in target_years if progress.get(str(y)) == 12]
    incomplete = [y for y in target_years if progress.get(str(y), 0) not in (0, 12)]
    if incomplete:
        print(f"注意: 取得が未完了の年は統計から除外します: {incomplete}")
    print(f"Gumbelフィットに使用する年: {len(complete_years)}年分 {complete_years}")

    features = []
    for lat, lon in grid_points:
        key = f"{lat},{lon}"
        yearly = cache.get(key, {})
        maxima = [yearly[str(y)] for y in complete_years if yearly.get(str(y)) is not None]

        if not maxima:
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {"no_data": True},
            })
            continue

        fit = fit_gumbel(maxima)
        # 2026年8月改訂: 以前は「直近1年の年最大値」だけでriskを決めていたが、
        # これだと単年のブレ(たまたま雨が少ない/多い年)がそのまま地図の色に出てしまい、
        # gumbel_mu/betaが10年分の統計であるのと整合しない状態だった。
        # 「10年分の年最大値の平均」をCMA基準で分類する方式に変更し、
        # 年ごとのノイズを均した典型値として扱う。
        mean_mm = float(np.mean(maxima))
        risk = rainfall_to_risk(mean_mm)

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
    """
    その年が「完全に取得済み」かを判定する。
    2026年8月改訂: 月単位の中断・再開に対応したため、値が入っているだけでは不十分
    (途中の月まで取得した暫定値かもしれない)。_progressで12月まで完了していることを
    必ず確認する。これを怠ると、部分的にしか取得していない年を「完了済み」とみなして
    スキップしてしまい、その年の年最大値が過小評価されたまま確定してしまう。
    """
    if cache.get("_progress", {}).get(str(year)) != 12:
        return False
    for pt in grid_points:
        key = f"{pt[0]},{pt[1]}"
        if cache.get(key, {}).get(str(year)) is None:
            return False
    return True


if __name__ == "__main__":
    main()
