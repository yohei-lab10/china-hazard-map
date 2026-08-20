"""
地震データ取得スクリプト(バックフィル対応版)
出典: USGS Earthquake Catalog (FDSN Event Web Service) - 無料・APIキー不要
https://earthquake.usgs.gov/fdsnws/event/1/

使い方:
  初回(過去50年をまとめて取り込む):
      python fetch_earthquakes.py --backfill-years 50 --minmag 5.0 --out ../data/earthquakes.geojson

  2回目以降(直近だけ差分取得。既存ファイルに新規分だけ追記):
      python fetch_earthquakes.py --minmag 5.0 --out ../data/earthquakes.geojson

  差分取得時は、既存ファイルの最新イベント時刻から「--overlap-days」分だけ
  さかのぼって再取得し(データ修正・遅延登録に対応するため)、
  IDが重複するものはスキップして新規分だけ追記する。

cronでの定期実行例(毎日午前6時に差分更新):
    0 6 * * * cd /path/to/hazardmap/scripts && python fetch_earthquakes.py --out ../data/earthquakes.geojson
"""
import argparse
import json
import os
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

# 中国全土をカバーする緯度経度の範囲(華東地区含む)
BBOX = {
    "minlatitude": 18,
    "maxlatitude": 54,
    "minlongitude": 73,
    "maxlongitude": 135,
}

USGS_ENDPOINT = "https://earthquake.usgs.gov/fdsnws/event/1/query"
CHUNK_YEARS = 5  # 長期間を一度に問い合わせるとタイムアウトしやすいため分割する


def fetch_range(start: datetime, end: datetime, minmag: float) -> dict:
    params = {
        "format": "geojson",
        "starttime": start.strftime("%Y-%m-%d"),
        "endtime": end.strftime("%Y-%m-%d"),
        "minmagnitude": minmag,
        **BBOX,
    }
    url = USGS_ENDPOINT + "?" + urllib.parse.urlencode(params)
    print(f"[fetch_earthquakes] 取得中: {params['starttime']} 〜 {params['endtime']}", flush=True)
    with urllib.request.urlopen(url, timeout=60) as res:
        return json.loads(res.read().decode("utf-8"))


def fetch_backfill(years: int, minmag: float) -> list:
    """years年分を、CHUNK_YEARSごとに分割して取得する"""
    end = datetime.now(timezone.utc)
    start_all = end - timedelta(days=365 * years)
    all_features = []

    cursor = start_all
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=365 * CHUNK_YEARS), end)
        raw = fetch_range(cursor, chunk_end, minmag)
        all_features.extend(raw.get("features", []))
        cursor = chunk_end

    return all_features


def fetch_incremental(since: datetime, overlap_days: int, minmag: float) -> list:
    start = since - timedelta(days=overlap_days)
    end = datetime.now(timezone.utc)
    raw = fetch_range(start, end, minmag)
    return raw.get("features", [])


def simplify(features: list) -> list:
    """フロントで使う最小限の項目に整形する"""
    simplified = []
    for f in features:
        lon, lat, depth = f["geometry"]["coordinates"]
        p = f["properties"]
        simplified.append({
            "type": "Feature",
            "id": f.get("id"),
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "mag": p.get("mag"),
                "place": p.get("place"),
                "time": p.get("time"),
                "depth_km": depth,
                "url": p.get("url"),
            },
        })
    return simplified


def load_existing(path: str) -> dict:
    """既存ファイルがあれば読み込み、IDをキーにした辞書を返す(無ければ空)"""
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    existing = {}
    for feat in data.get("features", []):
        fid = feat.get("id")
        if fid:
            existing[fid] = feat
    return existing


def latest_time(existing: dict) -> datetime:
    """既存データの中で最も新しいイベント時刻を返す(無ければ30日前)"""
    times = [f["properties"]["time"] for f in existing.values() if f["properties"].get("time")]
    if not times:
        return datetime.now(timezone.utc) - timedelta(days=30)
    return datetime.fromtimestamp(max(times) / 1000, tz=timezone.utc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill-years", type=int, default=None, help="初回バックフィルする年数(指定時のみ全期間を再取得)")
    ap.add_argument("--overlap-days", type=int, default=3, help="差分取得時にさかのぼる日数(データ修正・遅延登録対応)")
    ap.add_argument("--minmag", type=float, default=5.0, help="最小マグニチュード")
    ap.add_argument("--out", type=str, default="../data/earthquakes.geojson")
    args = ap.parse_args()

    existing = load_existing(args.out)
    print(f"[fetch_earthquakes] 既存データ: {len(existing)}件", flush=True)

    if args.backfill_years:
        raw_features = fetch_backfill(args.backfill_years, args.minmag)
    else:
        since = latest_time(existing)
        raw_features = fetch_incremental(since, args.overlap_days, args.minmag)

    new_features = simplify(raw_features)

    added = 0
    for feat in new_features:
        fid = feat.get("id")
        if fid and fid not in existing:
            existing[fid] = feat
            added += 1
        elif fid:
            existing[fid] = feat  # 修正版で上書き(マグニチュード改定などに対応)

    merged = sorted(existing.values(), key=lambda f: f["properties"].get("time") or 0)
    geojson = {"type": "FeatureCollection", "features": merged}

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)

    print(f"[fetch_earthquakes] 新規追加: {added}件 / 合計: {len(merged)}件を {args.out} に保存しました", flush=True)


if __name__ == "__main__":
    main()
