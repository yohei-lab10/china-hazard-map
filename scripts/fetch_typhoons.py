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


def fetch_raw_csv() -> pd.DataFrame:
    res = requests.get(IBTRACS_WP_CSV_URL, timeout=120)
    res.raise_for_status()
    df = pd.read_csv(io.StringIO(res.text), skiprows=[1], low_memory=False)
    return df


def filter_china_typhoons(df: pd.DataFrame, years: int) -> pd.DataFrame:
    df["ISO_TIME"] = pd.to_datetime(df["ISO_TIME"], errors="coerce")
    cutoff = datetime.now() - pd.DateOffset(years=years)
    df = df[df["ISO_TIME"] >= cutoff]

    df["LAT"] = pd.to_numeric(df["LAT"], errors="coerce")
    df["LON"] = pd.to_numeric(df["LON"], errors="coerce")

    in_bbox = (
        (df["LAT"] >= CHINA_COASTAL_BBOX["minlat"]) &
        (df["LAT"] <= CHINA_COASTAL_BBOX["maxlat"]) &
        (df["LON"] >= CHINA_COASTAL_BBOX["minlon"]) &
        (df["LON"] <= CHINA_COASTAL_BBOX["maxlon"])
    )
    relevant_sids = df.loc[in_bbox, "SID"].unique()
    return df[df["SID"].isin(relevant_sids)]


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
    ap.add_argument
