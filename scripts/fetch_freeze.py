#!/usr/bin/env python3
"""
scripts/fetch_freeze.py

凍結ハザード(水道管凍結・破裂リスク)の全国ヒートマップ用データを生成する。

【設計の要点】(china-hazard-map プロジェクト、2026年9月設計レビュー合意)
  ① 累積結氷度日数 (Freezing Degree Days, FDD) + 最長連続結氷日数
      年ごとに「その年で最もFDDが大きかった一続きの寒波(連続結氷区間)」を
      1つ選び、そのFDDを複数年平均する(年間の全氷点下日を単純合計するの
      ではない)。較正(calibrate_freeze_thresholds.py)が「1回の寒波
      イベントのFDD」を基準にしているため、本番側も同じ「1イベント」単位に
      揃える必要がある。
      2026年9月修正: 当初は日平均気温(T2M)ベースだったが、較正時に南方の
      短時間冷え込み(夜間だけ氷点下、日中は氷点上)を完全に見逃すことが
      判明したため、日最低気温(T2M_MIN)ベースに変更した。
      → 「凍結がどれだけ長く続くか」= 配管内の氷栓による圧力上昇という
        破裂の物理メカニズムに対応する主指標。

      【2026年9月・再修正】年間合計方式だと、較正値(1回の歴史的寒波の
      FDD)を通算20年分の冬の合計があっさり超えてしまい、蘇州・無錫のような
      並の寒さの地点まで「極めて高」に張り付く不具合が生じたため、
      「その年最大の1イベント」方式に変更した(詳細はcompute_indices()内)。

      【2026年9月・再々修正】「その年最大の1イベント」を単純に複数年平均する
      方式でも、較正基準(2008年のような数十年に1度の歴史的事例)と本番側
      (20年平均)の土俵が食い違い、較正した閾値で較正地点自身(貴陽)を
      評価しても「極めて高」に届かないという矛盾が発覚した。
      降雨強度(fetch_rainfall.py)と同じ考え方(年最大値の系列にGumbel分布を
      フィットし、T年再現期間の値を算出)をFDDにも適用し、fdd_t_ref
      (FDD_REFERENCE_RETURN_PERIOD_YEARSの年再現期間相当のFDD)をスコアの
      入力値とした。参照する再現期間は降雨強度のx10に揃えて10年としている
      (2026年9月、当初の25年案から変更。観測期間20年に対し25年は外挿が
      強すぎるため)。calibrate_freeze_thresholds.pyで6較正事例の逆算
      再現期間を確認したところ、貴陽(約43年)・長沙(約68年)は本当に
      歴史的な事例だった一方、南京・上海・武漢・広州は1.5〜2.4年に1度
      程度とその土地では珍しくない頻度であることが判明し、①の絶対閾値
      (FREEZE_FDD_TIER_BOUNDS、index.html側)もこの発見を踏まえて
      再較正した。

      【2026年9月追加】FDD(1イベントの合計値)は「-7℃が1日」も「-2℃が
      非連続で2回」も同じ数字にしてしまい、氷点下が連続していたかどうかを
      区別できない。氷点下の連続日数そのものが圧力上昇の継続時間に直結する
      ため、補助指標として「年間最長連続結氷日数」(①で採用したのと同じ
      イベントの連続日数、複数年平均)を追加した。
      連続日数の区切りは中国気象局の持続低温预警信号が「連続3日以上」を
      構造的な閾値として使っている点を参考にしている(温度の閾値自体は
      北方向けの数値のため転用せず、日数の区切り方のみ参考にした)。
      この指標を統合スコアにどう反映するか(FDDへの補正係数にするか、
      別スコアとして最大値を取るか)は次のステップで検討する。

  ② 寒潮強度(急激な気温降下)
      中央気象台の寒潮予警信号(蓝色/黄色/橙色の3段階)を採用:
        - 蓝色: GB/T 20484-2017の基本寒潮定義(24h降温8℃以上、または48h
          10℃以上、または72h12℃以上、かつ最低気温4℃以下)
        - 橙色: 24h降温12℃以上、かつ最低気温0℃以下、かつ風力6級(10.8m/s)以上
        - 黄色: 蓝色と橙色の間に位置する暫定基準(24h降温10℃以上、かつ
          最低気温4℃以下)。中央気象台の正式な数値基準を確認できなかった
          ための暫定値であり要検証。
      → 「対策(水抜き・保温材の巻き付け等)が間に合わない急な寒さ」を捉える。
        天津のような供暖区内でも局地的に凍結被害が起きる事例を、南北の
        地域区分に頼らず地点固有の実測データとして拾うための指標。

      【2026年9月・再修正】当初は「年間の蓝色相当の発生頻度を、年3回で
      頭打みにして正規化」する方式だったが、実データで検証したところ
      蘇州・無錫(華東、蓝色相当が年3.85回)が広州(亜熱帯、寒潮は稀だが
      深刻)よりも高いスコアになってしまった。①のFDDを「年間合計」から
      「その年最悪の1イベント」に直したのと同じ考え方で、②も「発生回数」
      ではなく「その年に到達した最も深刻な階級(蓝/黄/橙)」を蓝=0.33/
      黄=0.66/橙=1.0として評価し、複数年平均する方式に変更した。
      これにより、発生頻度の頭打ち値という恣意的なパラメータが不要になる。

  ③ 供暖境界(秦嶺・淮河線)
      本スクリプトでは扱わない。static/heating_boundary.geojson として
      別途一度限りで手動生成する(gen_heating_boundary.py 参照)。
      南方(境界より南)にのみ割増し係数として適用する想定。

  ④ 放射冷却リスク 【2026年9月改訂: 各条件を公式な気象基準に揃えた】
      放射性霜の気象学的な条件(快晴・弱風・乾燥な夜に地表が気温以上に
      冷える現象)を、恣意的な数値ではなく既存の公式基準で近似:
        - 快晴: 雲量(CLOUD_AMT) < 0.10 (気象庁の天気予報用語における
          「快晴」= 雲量0〜1/10 に対応)
        - 無風: 風速(WS2M) < 1.5 m/s (風力階級0〜1級。国際的なボーフォート
          風力階級で、中国・日本とも共通)
        - 乾燥: 最小湿度(RH2M) < 40% かつ 実効湿度 < 60%
          (気象庁「乾燥注意報」の代表的な基準。実効湿度は当日を含む直近
          数日の日平均湿度を減衰係数0.7で加重平均した指数で、木材=可燃物
          の乾燥度を表す。ただしNASA POWERは日平均湿度までしか提供せず、
          真の「その日の最低湿度」は取得できないため、「最小湿度」は
          日平均湿度で代用する近似値である点に注意)
        - かつ日平均気温が 0〜5℃ の「氷点際どい」範囲
      を満たす日の年平均発生回数。気温データだけでは捉えられない、
      屋外露出配管の追加冷え込みリスクを補足する。
      なお乾燥注意報は日本の気象庁の基準であり、中国気象局(CMA)には
      対応する公式基準が見当たらなかったための代用である。

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
import math
import os
import sys
import time
from datetime import date

import requests

POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
POWER_PARAMETERS = "T2M,T2M_MIN,WS2M,RH2M,CLOUD_AMT"
POWER_COMMUNITY = "AG"

# 中央気象台の寒潮予警信号(3段階)。蓝色はGB/T 20484-2017の基本寒潮定義。
# 橙色は確認済みの公式基準。黄色は正式な数値基準が確認できなかったための
# 暫定値(蓝色と橙色の中間、要検証)。
COLD_WAVE_BLUE_MIN_TEMP_C = 4.0
COLD_WAVE_BLUE_DROPS = [(1, 8.0), (2, 10.0), (3, 12.0)]  # (何日前と比較するか, 必要な降温幅℃)
COLD_WAVE_YELLOW_MIN_TEMP_C = 4.0   # 【暫定・要検証】
COLD_WAVE_YELLOW_DROP_24H_C = 10.0  # 【暫定・要検証】
COLD_WAVE_ORANGE_MIN_TEMP_C = 0.0
COLD_WAVE_ORANGE_DROP_24H_C = 12.0
COLD_WAVE_ORANGE_WIND_MS = 10.8  # 風力6級の下限(ボーフォート風力階級)
COLD_WAVE_TIER_VALUE = {0: 0.0, 1: 0.33, 2: 0.66, 3: 1.0}  # 蓝=1, 黄=2, 橙=3

# ①FDDのGumbel分布から読み取る参照再現期間(年)。暫定値・要検証。
# calibrate_freeze_thresholds.pyで実被害事例の逆算再現期間を確認してから調整する。
# ①FDDのGumbel分布から読み取る参照再現期間(年)。降雨強度(fetch_rainfall.py)が
# 「観測期間(10年)を超えて外挿しすぎない」という考え方でx10(10年確率)を
# スコアに採用しているのに合わせ、こちらも観測期間(20年)に対して同じ位置づけの
# 10年を採用する(2026年9月、当初の25年は観測期間の1.25倍で外挿が強すぎたため変更)。
FDD_REFERENCE_RETURN_PERIOD_YEARS = 10

# ④放射冷却リスクの暫定閾値(要検証)
RADIATIVE_CLOUD_MAX = 0.10   # 快晴(雲量0〜1/10)。CLOUD_AMTは0〜1のフラクション
RADIATIVE_WIND_MAX_MS = 1.5  # 風力階級0〜1級(ボーフォート風力階級)
RADIATIVE_MIN_HUMIDITY_PCT = 40.0        # 「最小湿度」の代用(実際は日平均湿度)
RADIATIVE_EFFECTIVE_HUMIDITY_PCT = 60.0  # 実効湿度(気象庁「乾燥注意報」代表値)
EFFECTIVE_HUMIDITY_DECAY = 0.7           # 実効湿度の減衰係数(気象庁の一般的な値)
EFFECTIVE_HUMIDITY_LOOKBACK_DAYS = 6     # 遡って加重平均に含める日数
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


def compute_effective_humidity_series(dates_sorted, rh2m, decay=EFFECTIVE_HUMIDITY_DECAY,
                                       lookback_days=EFFECTIVE_HUMIDITY_LOOKBACK_DAYS):
    """気象庁方式の実効湿度を日ごとに算出する。
    実効湿度_t = Σ(r^i × H_{t-i}) / Σ(r^i)  (i=0..lookback_days、tは当日)
    「当日と前日以前の日平均湿度を減衰係数で加重平均する」という定義通りの実装。
    データ系列の先頭付近(遡れる日数が足りない日)は、実際に遡れた日数分だけで
    正規化するため、常に有効な加重平均になる。"""
    eff = {}
    for idx, d in enumerate(dates_sorted):
        weighted_sum, weight_total = 0.0, 0.0
        for i in range(0, lookback_days + 1):
            j = idx - i
            if j < 0:
                break
            v = rh2m.get(dates_sorted[j])
            if not _valid(v):
                continue
            w = decay ** i
            weighted_sum += w * v
            weight_total += w
        if weight_total > 0:
            eff[d] = weighted_sum / weight_total
    return eff


def _cold_wave_tier_for_day(i, dates_sorted, t2m_min, ws2m):
    """i日目(dates_sorted[i])が中央気象台の寒潮予警のどの階級に該当するか。
    0=非該当, 1=蓝色, 2=黄色, 3=橙色。"""
    tmin_today = t2m_min.get(dates_sorted[i])
    if not _valid(tmin_today):
        return 0

    def drop(days_back):
        j = i - days_back
        if j < 0:
            return None
        tmin_prev = t2m_min.get(dates_sorted[j])
        if not _valid(tmin_prev):
            return None
        return tmin_prev - tmin_today

    d1 = drop(1)
    wind_today = ws2m.get(dates_sorted[i])
    # 橙色: 24h降温12℃以上 かつ 最低気温0℃以下 かつ 風力6級(10.8m/s)以上
    if (d1 is not None and d1 >= COLD_WAVE_ORANGE_DROP_24H_C
            and tmin_today <= COLD_WAVE_ORANGE_MIN_TEMP_C
            and _valid(wind_today) and wind_today >= COLD_WAVE_ORANGE_WIND_MS):
        return 3
    # 黄色(暫定・要検証): 24h降温10℃以上 かつ 最低気温4℃以下
    if (d1 is not None and d1 >= COLD_WAVE_YELLOW_DROP_24H_C
            and tmin_today <= COLD_WAVE_YELLOW_MIN_TEMP_C):
        return 2
    # 蓝色: GB/T 20484-2017の基本寒潮定義
    if tmin_today <= COLD_WAVE_BLUE_MIN_TEMP_C:
        for days_back, drop_threshold in COLD_WAVE_BLUE_DROPS:
            dd = drop(days_back)
            if dd is not None and dd >= drop_threshold:
                return 1
    return 0


def fit_gumbel(values):
    """モーメント法によるGumbel分布フィット(fetch_rainfall.pyと同じ手法)。
    values: 年ごとの最大値の系列(このスクリプトでは「年最悪の1連続結氷イベントの
    FDD」を各年1つずつ集めたもの)。2点未満では信頼できるフィットができないためNoneを返す。
    返り値: (mu, beta) または None。"""
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    std = variance ** 0.5
    if std <= 0:
        return None
    beta = (6 ** 0.5) * std / math.pi
    mu = mean - 0.5772157 * beta
    return mu, beta


def gumbel_return_level(mu, beta, return_period_years):
    """T年再現期間に相当する値(x_T)を、Gumbel分布の逆関数から算出する。
    x_T = mu - beta * ln(-ln(1 - 1/T))  ※fetch_rainfall.pyと同じ式。"""
    p = 1 - 1 / return_period_years
    return mu - beta * math.log(-math.log(p))



def compute_indices(daily):
    """1地点分の日次時系列から ①FDD ②寒潮の重症度 ④放射冷却頻度 の年平均を算出。"""
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
    # ①補助: 連続結氷区間(run)を検出し、各runの「開始日から連続した日数」と
    # 「その区間内のFDD合計」を両方記録する。
    runs = []  # [(start_date, length, run_fdd), ...]
    current_start, current_len, current_fdd = None, 0, 0.0
    for d in dates_sorted:
        v = t2m_min.get(d)
        if _valid(v) and v < 0:
            if current_len == 0:
                current_start = d
            current_len += 1
            current_fdd += -v
        else:
            if current_len > 0:
                runs.append((current_start, current_len, current_fdd))
            current_len, current_fdd = 0, 0.0
    if current_len > 0:
        runs.append((current_start, current_len, current_fdd))

    # ① FDD 【2026年9月再修正】年間の全氷点下日を単純合計する方式だと、
    # 較正(calibrate_freeze_thresholds.py)が「1回の寒波イベントのFDD」を
    # 基準にしているのと測定単位が食い違い、通算20年分の冬をすべて足し合わせた
    # 本番の値が較正値を軽く超えてしまい、蘇州・無錫のような並の寒さの地点まで
    # 「極めて高」に張り付く不具合が生じた。
    # 較正と同じ土俵に揃えるため、「その年で最もFDDが大きかった一続きの寒波
    # (連続結氷区間)のFDD」を年ごとに採用し、それを複数年平均する方式に変更した。
    # 年間合計ではなく「年最大の1イベント」を見る点は、台風の年間頻度ではなく
    # 観測史上最大の1イベントで評価する既存の考え方とも整合する。
    max_run_fdd_by_year = {}
    max_consec_by_year = {}
    for start, length, run_fdd in runs:
        y = start[:4]
        if run_fdd > max_run_fdd_by_year.get(y, -1):
            max_run_fdd_by_year[y] = run_fdd
            max_consec_by_year[y] = length  # 同じ(最もFDDが大きかった)runの連続日数
    # ① FDD 【2026年9月・再々修正】単純な複数年平均だと、較正基準(2008年の
    # ような数十年に1度の歴史的事例)と本番側(20年平均)の「土俵」がそもそも
    # 違っていた。貴陽(較正較正値152.3)の実際の20年平均が0.51(High止まり)にしか
    # ならず、較正した閾値で本番地点を評価しても較正地点自身が同じ階級に
    # 達しないという矛盾が発覚した。
    # 降雨強度(fetch_rainfall.py)と同じ考え方(年最大値の系列にGumbel分布を
    # フィットし、T年再現期間の値を算出)をFDDにも適用する。①は既に
    # 「各年最悪の1連続結氷イベントのFDD」を年ごとに1つ算出しており、これは
    # 降雨の「年最大24時間降水量」と全く同じ形の系列のため、そのまま流用できる。
    # 参照する再現期間(FDD_REFERENCE_RETURN_PERIOD_YEARS)は降雨強度(x10)に
    # 揃えて10年としている(2026年9月、当初の25年案から変更)。較正イベントが
    # 実際に何年再現期間に相当するかはcalibrate_freeze_thresholds.pyで
    # 逆算済み(貴陽43年・長沙68年など、事例ごとに稀さが大きく異なることが判明)。
    fdd_series = list(max_run_fdd_by_year.values())
    gumbel = fit_gumbel(fdd_series)
    if gumbel is not None:
        gumbel_mu, gumbel_beta = gumbel
        fdd_t_ref = gumbel_return_level(gumbel_mu, gumbel_beta, FDD_REFERENCE_RETURN_PERIOD_YEARS)
        fdd_t_ref = max(0.0, fdd_t_ref)  # Gumbelは理論上負値も取りうるため、物理的に無意味な負のFDDは0に丸める
    else:
        # 観測年数が少なすぎてGumbelフィットが信頼できない場合は、
        # 単純平均にフォールバックする(降雨強度がyears_used不足の地点で
        # 再現期間の行自体を表示しないのと同様、呼び出し側で扱いを変える余地を残す)
        gumbel_mu, gumbel_beta = None, None
        fdd_t_ref = sum(fdd_series) / len(fdd_series) if fdd_series else 0.0
    fdd_annual_mean = sum(max_run_fdd_by_year.values()) / n_years if max_run_fdd_by_year else 0.0
    max_consec_annual_mean = sum(max_consec_by_year.values()) / n_years if max_consec_by_year else 0.0

    # ② 寒潮強度 【2026年9月・再修正】発生頻度を頭打ちで正規化する方式から、
    # 「その年に到達した最も深刻な階級(蓝/黄/橙)」を複数年平均する方式に変更
    # (詳細な経緯はモジュールdocstring参照)。
    # cold_wave_freq(蓝色相当の年間発生回数)は診断・レポート表示用に維持する。
    cold_wave_count = 0
    max_tier_by_year = {}
    for i, d in enumerate(dates_sorted):
        tier = _cold_wave_tier_for_day(i, dates_sorted, t2m_min, ws2m)
        if tier >= 1:
            cold_wave_count += 1  # 蓝色以上を1回とカウント(診断用、旧来と同じ定義)
        y = d[:4]
        if tier > max_tier_by_year.get(y, 0):
            max_tier_by_year[y] = tier
    cold_wave_annual_freq = cold_wave_count / n_years
    cold_wave_severity = (sum(COLD_WAVE_TIER_VALUE[t] for t in max_tier_by_year.values()) / n_years
                           if max_tier_by_year else 0.0)

    # ④ 放射冷却リスク頻度
    # 【2026年9月改訂】単純なRH<50%から、気象庁「乾燥注意報」に合わせた
    # 「最小湿度」+「実効湿度」の2条件に変更。
    effective_humidity = compute_effective_humidity_series(dates_sorted, rh2m)
    radiative_count = 0
    for d in dates_sorted:
        t, w, rh, c = t2m.get(d), ws2m.get(d), rh2m.get(d), cloud.get(d)
        eh = effective_humidity.get(d)
        if not all(_valid(x) for x in (t, w, rh, c)) or eh is None:
            continue
        if (c < RADIATIVE_CLOUD_MAX and w < RADIATIVE_WIND_MAX_MS
                and rh < RADIATIVE_MIN_HUMIDITY_PCT
                and eh < RADIATIVE_EFFECTIVE_HUMIDITY_PCT
                and RADIATIVE_TEMP_RANGE_C[0] <= t <= RADIATIVE_TEMP_RANGE_C[1]):
            radiative_count += 1
    radiative_annual_freq = radiative_count / n_years

    return {
        "fdd": round(fdd_annual_mean, 1),  # 参考値: 単純な複数年平均(スコアには不使用)
        "fdd_t_ref": round(fdd_t_ref, 1),  # スコアに使用: FDD_REFERENCE_RETURN_PERIOD_YEARS年再現期間相当のFDD
        "fdd_return_period_years": FDD_REFERENCE_RETURN_PERIOD_YEARS,
        "gumbel_mu": round(gumbel_mu, 2) if gumbel_mu is not None else None,
        "gumbel_beta": round(gumbel_beta, 2) if gumbel_beta is not None else None,
        "max_consec_freeze_days": round(max_consec_annual_mean, 1),
        "cold_wave_freq": round(cold_wave_annual_freq, 2),
        "cold_wave_severity": round(cold_wave_severity, 3),
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
            "note": ("①fdd_t_ref(fdd_return_period_years年再現期間相当のFDD。年ごとの"
                     "最悪1連続結氷イベントの系列にGumbel分布をフィットして算出。"
                     "統合スコアはこちらを使用) ①fdd(参考値: 単純な複数年平均、スコア不使用) "
                     "①補助:max_consec_freeze_days(同イベントの連続日数、複数年平均) "
                     "②cold_wave_freq(蓝色相当の年間発生回数、診断用) "
                     "②cold_wave_severity(年最悪の寒潮階級[蓝0.33/黄0.66/橙1.0]の複数年平均、"
                     "統合スコアはこちらを使用) "
                     "④radiative_freq(放射冷却条件の年平均発生回数)。"
                     "③供暖区分は別ファイル heating_boundary.geojson。"
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
