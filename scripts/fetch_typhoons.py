"""
台風(熱帯低気圧)データ取得スクリプト
出典: IBTrACS (International Best Track Archive for Climate Stewardship) - NOAA/WMO
西太平洋バージョン(WP)のCSVを使用。無料・登録不要。
https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/v04r01/access/csv/

事前準備:
    pip install pandas requests

実行例:
    python fetch_typhoons.py --years 10 --out ../data/typhoons.geojson

cronでの定期実行例(月1回・毎月1日午前3時):
    0 3 1 * * cd /path/to/hazardmap/scripts && python fetch_typhoons.py --out ../data/typhoons.geojson

注意:
- IBTrACSの全件データは大容量のため、初回ダウンロードに時間がかかります(西太平洋のみでも数万行)。
- 中国上陸の判定は簡易的に「中国沿岸の緯度経度バウンディングボックス内を通過した経路点」で行っています。
  精緻な上陸判定(海岸線ポリゴンとの交差判定)が必要な場合はshapelyの導入を検討してください。
"""
import argparse
import json
import io
import sys
import requests
import pandas as pd
from datetime import datetime

IBTRACS_WP_CSV_URL = (
    "https://www.ncei.noaa.gov/data/"
    "international-best-track-archive-for-climate-stewardship-ibtracs/"
    "v04r01/access/csv/ibtracs.WP.list.v04r01.csv"
)

# 中国沿岸域(上陸判定用の簡易バウンディングボックス)
CHINA_COASTAL_BBOX = {"minlat": 18, "maxlat": 41, "minlon": 108, "maxlon": 123}

# NCEIのサーバーによってはUser-Agent無しのリクエストを拒否することがあるため明示的に指定
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; china-hazard-map-bot/1.0; "
        "+https://github.com/yohei-lab10/china-hazard-map)"
    )
}


def fetch_raw_csv() -> pd.DataFrame:
    print(f"[fetch_typhoons] IBTrACSへリクエスト送信: {IBTRACS_WP_CSV_URL}", flush=True)
    try:
        res = requests.get(IBTRACS_WP_CSV_URL, headers=REQUEST_HEADERS, timeout=120)
    except requests.exceptions.RequestException as e:
        print(f"[fetch_typhoons] ネットワークエラー: {e}", flush=True)
        raise

    print(f"[fetch_typhoons] HTTPステータス: {res.status_code}, 本文サイズ: {len(res.content)} bytes", flush=True)

    if res.status_code != 200:
        print(f"[fetch_typhoons] 応答本文の先頭300文字: {res.text[:300]!r}", flush=True)
        res.raise_for_status()

    # IBTrACSのCSVは1行目がヘッダ、2行目が単位のため2行目をスキップ
    df = pd.read_csv(io.StringIO(res.text), skiprows=[1], low_memory=False)
    print(f"[fetch_typhoons] CSV読み込み完了: {len(df)}行, カラム: {list(df.columns)[:8]}...", flush=True)
    return df


def filter_china_typhoons(df: pd.DataFrame, years: int) -> pd.DataFrame:
    df["ISO_TIME"] = pd.to_datetime(df["ISO_TIME"], errors="coerce")
    cutoff = datetime.now() - pd.DateOffset(years=years)
    df = df[df["ISO_TIME"] >= cutoff]
    print(f"[fetch_typhoons] 直近{years}年でフィルタ後: {len(df)}行", flush=True)

    df["LAT"] = pd.to_numeric(df["LAT"], errors="coerce")
    df["LON"] = pd.to_numeric(df["LON"], errors="coerce")

    in_bbox = (
        (df["LAT"] >= CHINA_COASTAL_BBOX["minlat"]) &
        (df["LAT"] <= CHINA_COASTAL_BBOX["maxlat"]) &
        (df["LON"] >= CHINA_COASTAL_BBOX["minlon"]) &
        (df["LON"] <= CHINA_COASTAL_BBOX["maxlon"])
    )
    relevant_sids = df.loc[in_bbox, "SID"].unique()
    result = df[df["SID"].isin(relevant_sids)]
    print(f"[fetch_typhoons] 中国沿岸バウンディングボックスに該当する台風: {len(relevant_sids)}個", flush=True)
    return result


def to_geojson(df: pd.DataFrame) -> dict:
    features = []
    for sid, group in df.groupby("SID"):
        group = group.sort_values("ISO_TIME")
        coords = [[float(lon), float(lat)] for lon, lat in zip(group["LON"], group["LAT"]) if pd.notna(lon) and pd.notna(lat)]
        if len(coords) < 2:
            continue
        name = group["NAME"].iloc[0] if "NAME" in group else sid
        max_wind = pd.to_numeric(group.get("WMO_WIND"), errors="coerce").max()
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "sid": sid,
                "name": name,
                "start_time": str(group["ISO_TIME"].min()),
                "end_time": str(group["ISO_TIME"].max()),
                "max_wind_kt": None if pd.isna(max_wind) else float(max_wind),
            },
        })
    return {"type": "FeatureCollection", "features": features}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=10, help="過去何年分を取得するか")
    ap.add_argument("--out", type=str, default="../data/typhoons.geojson")
    args = ap.parse_args()

    try:
        df = fetch_raw_csv()
        df = filter_china_typhoons(df, args.years)
        geojson = to_geojson(df)

        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(geojson, f, ensure_ascii=False, indent=2)

        print(f"[fetch_typhoons] {len(geojson['features'])}件の台風トラックを {args.out} に保存しました", flush=True)
    except Exception:
        import traceback
        print("[fetch_typhoons] エラーが発生しました:", flush=True)
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
