"""
地震データ取得スクリプト
出典: USGS Earthquake Catalog (FDSN Event Web Service) - 無料・APIキー不要
https://earthquake.usgs.gov/fdsnws/event/1/

実行例:
    python fetch_earthquakes.py --days 30 --minmag 3.5 --out ../data/earthquakes.geojson

cronでの定期実行例(毎日午前6時に更新):
    0 6 * * * cd /path/to/hazardmap/scripts && python fetch_earthquakes.py --out ../data/earthquakes.geojson
"""
import argparse
import json
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


def fetch(days: int, minmag: float) -> dict:
    start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    params = {
        "format": "geojson",
        "starttime": start,
        "minmagnitude": minmag,
        **BBOX,
    }
    url = USGS_ENDPOINT + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def simplify(raw: dict) -> dict:
    """フロントで使う最小限の項目に整形する"""
    features = []
    for f in raw.get("features", []):
        lon, lat, depth = f["geometry"]["coordinates"]
        p = f["properties"]
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "mag": p.get("mag"),
                "place": p.get("place"),
                "time": p.get("time"),
                "depth_km": depth,
                "url": p.get("url"),
            },
        })
    return {"type": "FeatureCollection", "features": features}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="過去何日分を取得するか")
    ap.add_argument("--minmag", type=float, default=3.5, help="最小マグニチュード")
    ap.add_argument("--out", type=str, default="../data/earthquakes.geojson")
    args = ap.parse_args()

    raw = fetch(args.days, args.minmag)
    simplified = simplify(raw)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(simplified, f, ensure_ascii=False, indent=2)

    print(f"{len(simplified['features'])}件の地震データを {args.out} に保存しました")


if __name__ == "__main__":
    main()
