#!/usr/bin/env python3
"""
scripts/fetch_freeze.py

凍結ハザード(水道管凍結・破裂リスク)の全国ヒートマップ用データを生成する。

【設計の要点】(china-hazard-map プロジェクト、2026年9月設計レビュー合意)
  ① 累積結氷度日数 (Freezing Degree Days, FDD) + 最長連続結氷日数
      FDD = Σ max(0, 0 - 日最低気温)  ※氷点下の日のみ加算し、年間積算を複数年平均
      2026年9月修正: 当初は日平均気温(T2M)ベースだったが、較正時に南方の
      短時間冷え込み(夜間だけ氷点下、日中は氷点上)を完全に見逃すことが
      判明したため、日最低気温(T2M_MIN)ベースに変更した。
      → 「凍結がどれだけ長く続くか」= 配管内の氷栓による圧力上昇という
        破裂の物理メカニズムに対応する主指標。

      【2026年9月追加】FDD(合計値)は「-7℃が1日」も「-2℃が非連続で2回」も
      同じ数字(7℃・日 vs 4℃・日相当)にしてしまい、氷点下が連続していたか
      どうかを区別できない。氷点下の連続日数そのものが圧力上昇の継続時間に
      直結するため、補助指標として「年間最長連続結氷日数」
      (日最低気温が連続して0℃を下回った最長日数、複数年平均)を追加した。
      連続日数の区切りは中国気象局の持続低温预警信号が「連続3日以上」を
      構造的な閾値として使っている点を参考にしている(温度の閾値自体は
      北方向けの数値のため転用せず、日数の区切り方のみ参考にした)。
      この指標を統合スコアにどう反映するか(FDDへの補正係数にするか、
      別スコアとして最大値を取るか)は次のステップで検討する。

  ② 寒潮強度(急激な気温降下)
      中国気象局《冷空気等级》国家標準 GB/T 20484-2017 の寒潮定義をそのまま採用:
        - 24時間以内に日最低気温が8℃以上低下、または
        - 48時間以内に10℃以上低下、または
        - 72時間以内に12℃以上低下
        - かつ、その日の最低気温が4℃以下
      → 「対策(水抜き・保温材の巻き付け等)が間に合わない急な寒さ」を捉える。
        天津のような供暖区内でも局地的に凍結被害が起きる事例を、南北の
        地域区分に頼らず地点固有の実測データとして拾うための指標。

  ③ 供暖境界(秦嶺・淮河線)
      本スクリプトでは扱わない。static/heating_boundary.geojson として
      別途一度限りで手動生成する(gen_heating_boundary.py 参照)。
      南方(境界より南)にのみ割増し係数として適用する想定。

  ④ 放射冷却リスク
      放射性霜の気象学的な条件(快晴・弱風・乾燥な夜に地表が気温以上に
      冷える現象)を近似:
        - 雲量(CLOUD_AMT) < 0.3 (30%)
        - 風速(WS2M) < 3 m/s
        - 相対湿度(RH2M) < 50%
        - かつ日平均気温が 0〜5℃ の「氷点際どい」範囲
      を満たす日の年平均発生回数。気温データだけでは捉えられない、
      屋外露出配管の追加冷え込みリスクを補足する。

【データソース】
  NASA POWER Daily Point API(認証・APIキー不要・無料)
  https://power.larc.nasa.gov/api/temporal/daily/point
  取得パラメータ: T2M, T2M_MIN, WS2M, RH2M, CLOUD_AMT (community=AG)

【グリッド】
  中国の陸域境界を独自に再定義せず、既存の data/elevation_risk.geojson
  (AW3D30由来、1度グリッド、中国陸域のみに絞り込み済み)の各セルの
  座標をそのまま再利用する。海上・国外への無駄なAPI呼び出しを避けるため。

【暫定値・要検証】
  ②の最低気温閾値(4℃)は国家標準どおりだが、④の閾値(雲量30%/風速3m/s/
  湿度50%)および最終スコアへの重み付け(α, β, γ)は設計レビュー時点の
  仮置き。実データのヒストグラムを確認してから調整する前提
  (降雨強度で10年再現期間を採用した際と同じプロセスを踏襲)。
  本スクリプトは①②④の生値のみを出力し、統合スコアの計算はUI側
  (index.html)に委ねる(rainfallIntensityScoreと同じ設計方針)。
"""

import argparse
import json
import os
import sys
import time
from datetime import date

import requests

POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
POWER_PARAMETERS = "T2M,T2M_MIN,WS2M,RH2M,CLOUD_AMT"
POWER_COMMUNITY = "AG"

# GB/T 20484-2017《冷空气等级》寒潮定義
COLD_WAVE_MIN_TEMP_C = 4.0
COLD_WAVE_DROPS = [(1, 8.0), (2, 10.0), (3, 12.0)]  # (何日前と比較するか, 必要な降温幅℃)

# ④放射冷却リスクの暫定閾値(要検証)
RADIATIVE_CLOUD_MAX = 0.3   # CLOUD_AMTは0〜1のフラクション
RADIATIVE_WIND_MAX_MS = 3.0
RADIATIVE_RH_MAX_PCT = 50.0
RADIATIVE_TEMP_RANGE_C = (0.0, 5.0)

RETRY = 3
RETRY_WAIT_SEC = 10
REQUEST_TIMEOUT_SEC = 60
INTER_REQUEST_SLEEP_SEC = 1.0  # NASA POWER側への負荷配慮

# 【安全フィルタ】中国本土のおおよそのバウンディングボックス。
# elevation_risk.geojsonをそのまま再利用する設計だが、実行結果を確認したところ
# 北海道(例: 41.5N/143.5E)やフィリピン海(例: 20.5N/144.5E)など、明らかに
# 中国国外の地点が同ファイルに含まれていることが判明した(elevation_risk.geojson
# 側の生成ロジックに起因すると見られ、fetch_freeze.py固有の問題ではない可能性が
# 高いが、要調査)。矩形なので国境の細部までは正確ではない(西方の隣国の一部を
# わずかに含みうる)が、少なくとも今回確認された東側への漏れは経度の上限だけで
# 確実に除外できるため、これを暫定的な安全策として追加する。
CHINA_BBOX = {"lon_min": 73.0, "lon_max": 135.5, "lat_min": 18.0, "lat_max": 54.0}


def load_grid_points(elevation_path):
    """既存の elevation_risk.geojson から中国陸域の1度グリッド座標を読み込む。
    ただし中国本土のバウンディングボックス外の点は除外する(CHINA_BBOX参照)。"""
    with open(elevation_path, "r", encoding="utf-8") as f:
        geojson = json.load(f)
    points = []
    excluded = []
    for feat in geojson["features"]:
        geom = feat["geometry"]
        if geom["type"] == "Point":
            lon, lat = geom["coordinates"][0], geom["coordinates"][1]
        elif geom["type"] == "Polygon":
            ring = geom["coordinates"][0]
            lon = sum(c[0] for c in ring) / len(ring)
            lat = sum(c[1] for c in ring) / len(ring)
        else:
            continue
        lon, lat = round(lon, 2), round(lat, 2)
        if not (CHINA_BBOX["lon_min"] <= lon <= CHINA_BBOX["lon_max"]
                and CHINA_BBOX["lat_min"] <= lat <= CHINA_BBOX["lat_max"]):
            excluded.append((lon, lat))
            continue
        points.append((lon, lat))
    if excluded:
        print(f"[INFO] バウンディングボックス外として除外: {len(excluded)}地点 "
              f"(例: {excluded[:5]})")
    return sorted(set(points))


def fetch_power_daily(lat, lon, start, end):
    """1地点・1期間分の日次データを取得する(リトライ付き)。失敗時はNone。"""
    params = {
        "parameters": POWER_PARAMETERS,
        "community": POWER_COMMUNITY,
        "longitude": lon,
        "latitude": lat,
        "start": start,
        "end": end,
        "format": "JSON",
    }
    last_err = None
    for attempt in range(1, RETRY + 1):
        try:
            resp = requests.get(POWER_URL, params=params, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            data = resp.json()
            return data["properties"]["parameter"]
        except Exception as e:  # noqa: BLE001 - 外部APIの失敗形態は多様なため広く受ける
            last_err = e
            if attempt < RETRY:
                time.sleep(RETRY_WAIT_SEC)
    print(f"  [WARN] lat={lat} lon={lon}: 取得失敗 ({last_err})", file=sys.stderr)
    return None


def _valid(v):
    """NASA POWERの欠測値(-999系のフィル値)を除外する。"""
    return v is not None and v > -900


def compute_indices(daily):
    """1地点分の日次時系列から ①FDD ②寒潮頻度 ④放射冷却頻度 の年平均を算出。"""
    t2m = daily.get("T2M", {})
    t2m_min = daily.get("T2M_MIN", {})
    ws2m = daily.get("WS2M", {})
    rh2m = daily.get("RH2M", {})
    cloud = daily.get("CLOUD_AMT", {})

    dates_sorted = sorted(t2m_min.keys())
    if not dates_sorted:
        return None
    n_years = len(set(d[:4] for d in dates_sorted))
    if n_years == 0:
        return None

    # ① FDD 【2026年9月修正】日平均気温(T2M)ベースから日最低気温(T2M_MIN)ベースに変更。
    # 較正(2016年広州)で、日平均では0℃を下回らない一方、夜間だけ-1.66℃まで
    # 下がっていたケースがFDD=0.0と判定されてしまうことが判明したため。
    # 南方の被害は夜間だけの短時間の冷え込みで起きることが多く、日平均では
    # それを完全に見逃してしまう。日最低気温を使うことで、その日のうちに
    # 一度でも氷点下に達したかどうかを正しく反映する。
    fdd_total = 0.0
    for d in dates_sorted:
        v = t2m_min.get(d)
        if _valid(v) and v < 0:
            fdd_total += -v
    fdd_annual_mean = fdd_total / n_years

    # ①補助: 年間最長連続結氷日数(日最低気温が連続して0℃を下回った最長日数)。
    # FDDの合計値だけでは「連続していたか」を区別できないための補助指標。
    # 連続区間(run)を検出し、各runの開始日が属する年に、その年最大値として帰属させる。
    runs = []
    current_start, current_len = None, 0
    for d in dates_sorted:
        v = t2m_min.get(d)
        if _valid(v) and v < 0:
            if current_len == 0:
                current_start = d
            current_len += 1
        else:
            if current_len > 0:
                runs.append((current_start, current_len))
            current_len = 0
    if current_len > 0:
        runs.append((current_start, current_len))
    max_consec_by_year = {}
    for start, length in runs:
        y = start[:4]
        if length > max_consec_by_year.get(y, 0):
            max_consec_by_year[y] = length
    max_consec_annual_mean = sum(max_consec_by_year.values()) / n_years

    # ② 寒潮頻度(GB/T 20484-2017)
    cold_wave_count = 0
    for i, d in enumerate(dates_sorted):
        tmin_today = t2m_min.get(d)
        if not _valid(tmin_today) or tmin_today > COLD_WAVE_MIN_TEMP_C:
            continue
        for days_back, drop_threshold in COLD_WAVE_DROPS:
            if i - days_back < 0:
                continue
            tmin_prev = t2m_min.get(dates_sorted[i - days_back])
            if _valid(tmin_prev) and (tmin_prev - tmin_today) >= drop_threshold:
                cold_wave_count += 1
                break  # 同日の多重カウントを防ぐ
    cold_wave_annual_freq = cold_wave_count / n_years

    # ④ 放射冷却リスク頻度
    radiative_count = 0
    for d in dates_sorted:
        t, w, rh, c = t2m.get(d), ws2m.get(d), rh2m.get(d), cloud.get(d)
        if not all(_valid(x) for x in (t, w, rh, c)):
            continue
        if (c < RADIATIVE_CLOUD_MAX and w < RADIATIVE_WIND_MAX_MS
                and rh < RADIATIVE_RH_MAX_PCT
                and RADIATIVE_TEMP_RANGE_C[0] <= t <= RADIATIVE_TEMP_RANGE_C[1]):
            radiative_count += 1
    radiative_annual_freq = radiative_count / n_years

    return {
        "fdd": round(fdd_annual_mean, 1),
        "max_consec_freeze_days": round(max_consec_annual_mean, 1),
        "cold_wave_freq": round(cold_wave_annual_freq, 2),
        "radiative_freq": round(radiative_annual_freq, 2),
        "years_used": n_years,
    }


def main():
    parser = argparse.ArgumentParser(description="凍結ハザード(freeze_risk.geojson)を生成する")
    parser.add_argument("--elevation", default="data/elevation_risk.geojson",
                         help="グリッド定義を再利用する既存ファイル")
    parser.add_argument("--start-year", type=int, default=2006)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--out", default="data/freeze_risk.geojson")
    parser.add_argument("--limit", type=int, default=None,
                         help="デバッグ用: 先頭N地点のみ処理して動作確認する")
    args = parser.parse_args()

    points = load_grid_points(args.elevation)
    if args.limit:
        points = points[: args.limit]
    print(f"対象グリッド点数: {len(points)}")

    start, end = f"{args.start_year}0101", f"{args.end_year}1231"
    features = []
    for i, (lon, lat) in enumerate(points, 1):
        print(f"[{i}/{len(points)}] lat={lat} lon={lon}")
        daily = fetch_power_daily(lat, lon, start, end)
        time.sleep(INTER_REQUEST_SLEEP_SEC)
        if daily is None:
            continue
        idx = compute_indices(daily)
        if idx is None:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": idx,
        })

    geojson = {
        "type": "FeatureCollection",
        "properties": {
            "generated": date.today().isoformat(),
            "source": "NASA POWER (power.larc.nasa.gov), community=AG",
            "period": f"{args.start_year}-{args.end_year}",
            "note": ("①fdd(累積結氷度日数の年平均) ①補助:max_consec_freeze_days"
                     "(年間最長連続結氷日数の複数年平均) ②cold_wave_freq(GB/T20484-2017"
                     "寒潮の年平均発生回数) ④radiative_freq(放射冷却条件の年平均"
                     "発生回数)。③供暖区分は別ファイル heating_boundary.geojson。"
                     "統合スコアの算出はindex.html側で行う。"),
        },
        "features": features,
    }

    # 他スクリプトと同じアトミック書き込みパターン(一時ファイル→os.replace)
    tmp_path = args.out + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False)
    os.replace(tmp_path, args.out)
    print(f"完了: {len(features)}/{len(points)} 地点を {args.out} に出力")


if __name__ == "__main__":
    main()
