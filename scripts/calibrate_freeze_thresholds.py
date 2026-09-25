#!/usr/bin/env python3
"""
scripts/calibrate_freeze_thresholds.py

凍結ハザードの①FDD(累積結氷度日数)について、査定担当者が納得できる
「絶対基準」を得るための較正スクリプト。

【背景】
GB/T 20484-2017(寒潮)や中央気象台の寒潮予警信号(蓝/黄/橙)は②の絶対基準
として転用できたが、①FDDには南方の気候帯(断熱のない配管が壊れる0℃前後)
に対応する公式の絶対基準が存在しない。CMAの「持続低温预警信号」は構造
(連続日数×閾値温度)は使えるが、公式の数値(-12℃)は北方向けで転用できない。

【方針・2026年9月時点】
当初は「実被害事例の期間内FDD」をそのまま①の絶対閾値として採用していたが、
これは「1回の歴史的事例の値」と「本番側(fetch_freeze.pyの複数年平均値)」の
土俵が食い違うという問題を引き起こした(較正地点の貴陽自身が、較正した
閾値で評価しても「極めて高」に届かない矛盾が発覚)。
降雨強度(fetch_rainfall.py)と同じ考え方で、①はGumbel分布による
T年再現期間の値(fdd_t_ref)をスコアの入力に使う方式に変更した
(詳細はfetch_freeze.pyのdocstring参照)。
そのため本スクリプトも、各較正都市について
  (a) 実被害事例の期間内FDD(従来通り)
  (b) その地点の2006〜2025年の日次データからGumbel分布をフィットし、
      (a)の値が「何年再現期間に相当するか」を逆算した値
の両方を出力するように変更した。(b)により「2008年の貴陽の寒波は、あの
地点にとって何年に1度の事象だったか」を統計的に確認でき、
fetch_freeze.py側のFDD_REFERENCE_RETURN_PERIOD_YEARS(現在25年、仮置き)が
妥当かどうかを判断する材料になる。

【使い方】
  python scripts/calibrate_freeze_thresholds.py

  ネットワークアクセスが必要(NASA POWERへのAPI呼び出し)。較正都市ごとに
  「事例期間」と「2006〜2025年の全期間」の2回ずつAPIを呼ぶため、
  fetch_power_daily()の呼び出し回数は較正都市数の2倍になる。
  GitHub Actionsのworkflow_dispatchで一時的に実行するか、ローカル環境で
  実行して標準出力の結果を確認し、その数値を index.html /
  fetch_freeze.py の閾値定数に反映する(このスクリプト自体は閾値を
  書き換えない。数値を「見る」ためのツール)。

【較正イベント一覧】
  出典: 香港天文台の回顧記事、《2008年中国南方低温雨雪冰冻灾害》関連の
  学術論文(李双双ほか 2015、张润琼ほか 2009)などから、被害が特に
  深刻だったとされる省・時期を採用。ただし正確な被害範囲・期間は
  複数の一次資料で要確認(このリストは初期案であり、査読前提)。

    - 2008年南方雪害: 貴州省・湖南省が最も深刻とされる。期間は
      2008年1月10日頃〜2月上旬が目安(気象庁資料では2007/12/25〜
      2008/1/28の降水分布が言及されている)。代表都市: 貴陽、長沙。
    - 2016年1月寒潮: 「霸王级寒潮」。広州で記録的な低温・降雪。
      期間は2016年1月20日〜25日が中心。代表都市: 広州、南京。
    - 2021年寒潮: 2020年12月28日に橙色警报(2016年以来初)。
      代表都市: 上海、武漢。

  上記の期間・都市は設計レビュー時点の暫定案。確定させる前に、
  一次資料(中央気象台の公式発表、CMAの災害報告書等)で日付・地域の
  精度を確認することを推奨する。
"""

import json
import math
import sys
import time

import requests

POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
POWER_PARAMETERS = "T2M_MIN"
POWER_COMMUNITY = "AG"
RETRY = 3
RETRY_WAIT_SEC = 10
REQUEST_TIMEOUT_SEC = 60

# fetch_freeze.pyのFDD_REFERENCE_RETURN_PERIOD_YEARSと同じ範囲・値。
# 依存を避けるためこのファイル単体で完結させる(他のfetch_*.pyと同じ方針)。
FULL_HISTORY_START = "20060101"
FULL_HISTORY_END = "20251231"

# (イベント名, 都市名, 緯度, 経度, 開始日 YYYYMMDD, 終了日 YYYYMMDD)
# 緯度経度は各都市庁舎付近の概算値。要検証。
CALIBRATION_EVENTS = [
    ("2008年南方雪害", "貴陽", 26.65, 106.63, "20080110", "20080210"),
    ("2008年南方雪害", "長沙", 28.23, 112.94, "20080110", "20080210"),
    ("2016年1月寒潮", "広州", 23.13, 113.26, "20160118", "20160128"),
    ("2016年1月寒潮", "南京", 32.06, 118.80, "20160118", "20160128"),
    ("2021年寒潮", "上海", 31.23, 121.47, "20201228", "20210105"),
    ("2021年寒潮", "武漢", 30.59, 114.30, "20201228", "20210105"),
]


def fetch_power_daily(lat, lon, start, end):
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
            return resp.json()["properties"]["parameter"]
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < RETRY:
                time.sleep(RETRY_WAIT_SEC)
    print(f"  [WARN] 取得失敗: {last_err}", file=sys.stderr)
    return None


def _valid(v):
    return v is not None and v > -900


def fit_gumbel(values):
    """モーメント法によるGumbel分布フィット(fetch_freeze.pyと同じ手法、
    依存を避けるためここでも複製している)。2点未満はNoneを返す。"""
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


def gumbel_cdf(mu, beta, x):
    return math.exp(-math.exp(-(x - mu) / beta))


def implied_return_period(mu, beta, x):
    """FDD値xが、このGumbel分布のもとで「何年に1度」の事象に相当するかを逆算する。
    CDFが1に極めて近い場合(非常に稀な事象)は再現期間が発散するため、
    表示用に上限を設けて返す。"""
    p = gumbel_cdf(mu, beta, x)
    if p >= 1 - 1e-9:
        return float("inf")
    return 1 / (1 - p)


def yearly_max_event_fdd_series(daily):
    """fetch_freeze.pyのcompute_indices()と同じロジックで、年ごとの
    「最もFDDが大きかった一続きの寒波(連続結氷区間)」のFDDを抽出し、
    年をキーにした辞書で返す。"""
    t2m_min = daily.get("T2M_MIN", {})
    dates_sorted = sorted(t2m_min.keys())
    runs = []
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
    max_run_fdd_by_year = {}
    for start, length, run_fdd in runs:
        y = start[:4]
        if run_fdd > max_run_fdd_by_year.get(y, -1):
            max_run_fdd_by_year[y] = run_fdd
    return max_run_fdd_by_year


def event_fdd(daily):
    """指定期間内で最もFDDが大きかった一続きの寒波(連続結氷区間)のFDDと、
    その連続日数、および期間全体の最低気温の最小値を返す。
    【2026年9月修正】日平均気温(T2M)ベースだと、日中に氷点を上回る南方の
    短時間冷え込み(例: 2016年広州)を完全に見逃すことが分かったため、
    fetch_freeze.pyと同じく日最低気温(T2M_MIN)ベースに変更した。
    【2026年9月・再修正】当初は「期間内の全氷点下日を単純合計」していたが、
    本番側(fetch_freeze.py)を「その年最大の1連続イベント」方式に変更した
    のに合わせ、較正側もここで同じロジックに揃える(較正イベントの期間は
    数週間あり、間に氷点上の日を挟む場合、単純合計だと本番の「1イベント」
    定義とずれるため)。"""
    t2m_min = daily.get("T2M_MIN", {})
    dates_sorted = sorted(t2m_min.keys())
    coldest = None
    current_start, current_len, current_fdd = None, 0, 0.0
    best_len, best_fdd = 0, 0.0
    for d in dates_sorted:
        v = t2m_min.get(d)
        if not _valid(v):
            if current_fdd > best_fdd:
                best_fdd, best_len = current_fdd, current_len
            current_start, current_len, current_fdd = None, 0, 0.0
            continue
        if v < 0:
            if current_len == 0:
                current_start = d
            current_len += 1
            current_fdd += -v
        else:
            if current_fdd > best_fdd:
                best_fdd, best_len = current_fdd, current_len
            current_start, current_len, current_fdd = None, 0, 0.0
        if coldest is None or v < coldest:
            coldest = v
    if current_fdd > best_fdd:
        best_fdd, best_len = current_fdd, current_len
    return best_fdd, best_len, coldest


def main():
    print("=== 凍結ハザード ①FDD・最長連続結氷日数 較正結果 ===")
    print("(既知の実被害事例の期間内で、最もFDDが大きかった一続きの寒波の値)\n")
    results = []
    for event, city, lat, lon, start, end in CALIBRATION_EVENTS:
        daily = fetch_power_daily(lat, lon, start, end)
        time.sleep(1.0)
        if daily is None:
            print(f"{event} / {city}: 取得失敗(事例期間)")
            continue
        fdd, max_consec, coldest = event_fdd(daily)
        print(f"{event} / {city} ({start}-{end}): FDD={fdd:.1f}℃・日, "
              f"最長連続結氷日数={max_consec}日, 期間最低気温={coldest}℃")

        # この地点の2006〜2025年の全期間からGumbel分布をフィットし、
        # 上記のイベントFDDが何年再現期間に相当するかを逆算する。
        full_daily = fetch_power_daily(lat, lon, FULL_HISTORY_START, FULL_HISTORY_END)
        time.sleep(1.0)
        return_period = None
        gumbel_mu = gumbel_beta = None
        if full_daily is None:
            print(f"  [WARN] {city}: 全期間データの取得失敗のため再現期間は算出不可")
        else:
            yearly_series = list(yearly_max_event_fdd_series(full_daily).values())
            gumbel = fit_gumbel(yearly_series)
            if gumbel is None:
                print(f"  [WARN] {city}: 年数不足等によりGumbelフィット不可(年数={len(yearly_series)})")
            else:
                gumbel_mu, gumbel_beta = gumbel
                return_period = implied_return_period(gumbel_mu, gumbel_beta, fdd)
                rp_label = "∞" if return_period == float("inf") else f"{return_period:.1f}"
                print(f"  → {city}の気候値(2006-2025)から見ると、この事例のFDDは"
                      f"約{rp_label}年に1度の事象に相当(μ={gumbel_mu:.1f}, β={gumbel_beta:.1f})")

        results.append({
            "event": event, "city": city, "fdd": round(fdd, 1),
            "max_consec_freeze_days": max_consec, "coldest": coldest,
            "gumbel_mu": round(gumbel_mu, 2) if gumbel_mu is not None else None,
            "gumbel_beta": round(gumbel_beta, 2) if gumbel_beta is not None else None,
            "implied_return_period_years": (
                None if return_period is None
                else (None if return_period == float("inf") else round(return_period, 1))
            ),
        })

    print("\n--- 参考 ---")
    print("2026年9月の設計見直しにより、①(持続型)と②(寒潮強度=急変型)は")
    print("別スコアとして算出し、最大値を取る方式にした。そのため①の絶対閾値は、")
    print("①が主因と考えられる事例(FDD・最長連続結氷日数がともに大きい事例)を")
    print("基準にすべきで、②が主因の事例(例: 広州のようにFDDが際立って小さい")
    print("事例)は①の閾値較正からは除外するのが妥当(要: 人間による最終判断)。")
    print("\n各事例の「implied_return_period_years」(この事例が地点固有の気候値")
    print("から見て何年に1度の事象か)を見比べ、fetch_freeze.pyの")
    print("FDD_REFERENCE_RETURN_PERIOD_YEARS(現在25年、仮置き)をどの値に")
    print("揃えるべきか判断する材料にすること。")

    with open("freeze_calibration_result.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\n結果を freeze_calibration_result.json に保存しました。")


if __name__ == "__main__":
    main()
