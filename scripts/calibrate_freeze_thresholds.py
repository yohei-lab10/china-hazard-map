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

【方針】
実際に配管凍結被害が報告された過去の寒波(2008年南方雪害、2016年1月寒潮、
2021年寒潮)について、被害地域の代表都市のNASA POWER気温データから
「その期間のFDD」を実際に算出し、それを①の「高リスク」ティアの絶対的な
下限値として採用する。降雨強度の設計で実測値ベースの絶対閾値(CMA基準)に
切り替えた際と同じ考え方。

【使い方】
  python scripts/calibrate_freeze_thresholds.py

  ネットワークアクセスが必要(NASA POWERへのAPI呼び出し)。
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
import sys
import time

import requests

POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
POWER_PARAMETERS = "T2M,T2M_MIN"
POWER_COMMUNITY = "AG"
RETRY = 3
RETRY_WAIT_SEC = 10
REQUEST_TIMEOUT_SEC = 60

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


def event_fdd(daily):
    """指定期間中のFDD(累積結氷度日数、期間合計)と、最低気温の最小値を返す。"""
    t2m = daily.get("T2M", {})
    t2m_min = daily.get("T2M_MIN", {})
    fdd_total = 0.0
    coldest = None
    for d, v in t2m.items():
        if _valid(v) and v < 0:
            fdd_total += -v
    for v in t2m_min.values():
        if _valid(v) and (coldest is None or v < coldest):
            coldest = v
    return fdd_total, coldest


def main():
    print("=== 凍結ハザード ①FDD 較正結果 ===")
    print("(既知の実被害事例における、期間中のFDD合計と最低気温)\n")
    results = []
    for event, city, lat, lon, start, end in CALIBRATION_EVENTS:
        daily = fetch_power_daily(lat, lon, start, end)
        time.sleep(1.0)
        if daily is None:
            print(f"{event} / {city}: 取得失敗")
            continue
        fdd, coldest = event_fdd(daily)
        print(f"{event} / {city} ({start}-{end}): FDD={fdd:.1f}℃・日, 期間最低気温={coldest}℃")
        results.append({"event": event, "city": city, "fdd": round(fdd, 1), "coldest": coldest})

    print("\n--- 参考: これらの最小値を①「高リスク」ティアの下限として採用する案 ---")
    if results:
        min_fdd = min(r["fdd"] for r in results)
        print(f"較正イベント中の最小FDD: {min_fdd:.1f}℃・日")
        print("→ 実被害が報告された事例のうち最も『軽微』だったものと同水準以上を")
        print("  高リスクとみなす、という設計になる(要: 人間による最終確認)。")

    with open("freeze_calibration_result.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\n結果を freeze_calibration_result.json に保存しました。")


if __name__ == "__main__":
    main()
