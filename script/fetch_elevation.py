"""
標高データ取得スクリプト(冠水ハザード用)
出典: Open-Elevation API(無料・APIキー不要)
https://open-elevation.com/

考え方:
- 中国の陸地・沿岸エリアに一定間隔のグリッドを敷き、各点の標高を取得
- 標高が低いほど「冠水しやすい」とみなし、0〜1のリスク重みに変換して保存
- 標高データはほぼ変化しないため、雷データと同様に「たまに手動実行」で十分

事前準備:
    pip install requests

実行例:
    python fetch_elevation.py --grid-deg 1.0 --out ../data/elevation_risk.geojson

しきい値の考え方(冠水ハザードの実務的な区分に基づく):
    海抜3m未満   → risk = 1.0(高危険域。高潮・津波に加え内水氾濫・河川氾濫でも容易に冠水)
    海抜3〜5m    → risk 0.75前後(警戒域。床上浸水〜2階到達の恐れ)
    海抜5〜10m   → risk 0.75→0.15へ緩やかに低下(移行域)
    海抜10m以上  → risk = 0.0〜0.15(相対的低危険域。微地形次第で内水リスクは残る)
"""
import argparse
import json
import time
import requests

# 中国の陸地・沿岸エリア(index.htmlのCHINA_LAND_BBOXと合わせている)
BBOX = {"minlat": 18, "maxlat": 41, "minlon": 97, "maxlon": 123}

OPEN_ELEVATION_URL = "https://api.open-elevation.com/api/v1/lookup"
BATCH_SIZE = 100  # 1回のAPIリクエストでまとめて問い合わせる件数
REQUEST_DELAY_SEC = 1.0  # APIへの負荷を抑えるための待機時間


def build_grid(grid_deg: float):
    lats = []
    lat = BBOX["minlat"]
    while lat <= BBOX["maxlat"]:
        lats.append(round(lat, 4))
        lat += grid_deg

    lons = []
    lon = BBOX["minlon"]
    while lon <= BBOX["maxlon"]:
        lons.append(round(lon, 4))
        lon += grid_deg

    return [(la, lo) for la in lats for lo in lons]


def fetch_elevations(points: list) -> list:
    """points: [(lat, lon), ...] -> [{"lat":..,"lon":..,"elevation":..}, ...]"""
    results = []
    for i in range(0, len(points), BATCH_SIZE):
        batch = points[i:i + BATCH_SIZE]
        locations = [{"latitude": la, "longitude": lo} for la, lo in batch]
        print(f"[fetch_elevation] {i}〜{i + len(batch)}件目を取得中(全{len(points)}件)...", flush=True)
        try:
            res = requests.post(OPEN_ELEVATION_URL, json={"locations": locations}, timeout=60)
            res.raise_for_status()
            data = res.json()
            for r in data.get("results", []):
                results.append({"lat": r["latitude"], "lon": r["longitude"], "elevation": r["elevation"]})
        except requests.exceptions.RequestException as e:
            print(f"[fetch_elevation] バッチ取得エラー(スキップ): {e}", flush=True)
        time.sleep(REQUEST_DELAY_SEC)
    return results


def elevation_to_risk(elevation: float) -> float:
    """
    冠水ハザードの実務的な区分に基づく標高→リスク変換。
    - 3m未満: 高危険域 (risk=1.0)
    - 3〜5m: 警戒域 (risk 1.0→0.75)
    - 5〜10m: 移行域 (risk 0.75→0.15)
    - 10m以上: 相対的低危険域 (risk 0.15→0、微地形リスクとして下限を残す)
    """
    if elevation < 3:
        return 1.0
    if elevation < 5:
        # 3〜5m: 1.0 -> 0.75
        return 1.0 - (elevation - 3) / (5 - 3) * 0.25
    if elevation < 10:
        # 5〜10m: 0.75 -> 0.15
        return 0.75 - (elevation - 5) / (10 - 5) * 0.60
    # 10m以上: 0.15 -> 0(30mでほぼ0に収束させる)
    return max(0.0, 0.15 - (elevation - 10) / (30 - 10) * 0.15)


def to_geojson(points: list) -> dict:
    features = []
    for p in points:
        risk = elevation_to_risk(p["elevation"])
        if risk <= 0:
            continue  # リスクほぼ無しの点は間引いてファイルサイズを抑える
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
            "properties": {
                "elevation_m": p["elevation"],
                "risk": round(risk, 3),
            },
        })
    return {"type": "FeatureCollection", "features": features}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid-deg", type=float, default=1.0, help="グリッド間隔(度)。小さいほど精細だがAPI呼び出しが増える")
    ap.add_argument("--out", type=str, default="../data/elevation_risk.geojson")
    args = ap.parse_args()

    grid = build_grid(args.grid_deg)
    print(f"[fetch_elevation] グリッド点数: {len(grid)}", flush=True)

    points = fetch_elevations(grid)
    print(f"[fetch_elevation] 標高取得成功: {len(points)}件", flush=True)

    geojson = to_geojson(points)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)

    print(f"[fetch_elevation] {len(geojson['features'])}件のリスク点を {args.out} に保存しました", flush=True)


if __name__ == "__main__":
    main()
