"""
河川データ取得スクリプト(水害ハザードの「水域近接倍率」用)
出典: HydroRIVERS (WWF / HydroSHEDS)
https://www.hydrosheds.org/products/hydrorivers

**重要な注意**: HydroRIVERSはhydrosheds.org公式サイトの「Explore the Data」ページから、
大陸(Asia)ごとのダウンロードリンクを都度確認する必要がある(CHIRPS/Aqueductのような
恒久的な直接URLパターンが確認できなかったため)。実行前に、そのページでAsia地域の
Shapefile(.zip)のダウンロードURLを取得し、--url引数に渡すこと。

考え方:
- HydroRIVERSは川の流量・集水面積(ORD_STRA列=河川次数)ごとに階層化された
  ベクトル線データ。中国全土をそのまま使うとファイルサイズ・処理量が膨大になるため、
  --min-order で指定した次数以上の(=ある程度の規模を持つ)河川のみに絞り込む
- 中国域にクリップし、GeoJSON(LineString)として軽量化して保存する
- 実際の「水域近接倍率」の距離計算(500m/1000m/1500m/2000m区分)は
  index.html側でこのGeoJSONを読み込んだ上でその場で計算する(サーバー側では計算しない)

ライセンス: 非商用・商用問わず利用可能(出典明記が必要)。HydroSHEDS技術文書の引用を推奨。

事前準備:
    pip install geopandas requests

実行例:
    python fetch_rivers.py --url "<hydrosheds.orgで確認したAsia地域HydroRIVERSのURL>" --min-order 4 --out ../data/rivers.geojson
"""
import argparse
import io
import json
import zipfile
import tempfile
import os

import geopandas as gpd
import requests

CHINA_BBOX = {"minlat": 18, "maxlat": 54, "minlon": 73, "maxlon": 135}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", type=str, required=True,
                     help="hydrosheds.orgで確認したHydroRIVERS Asia地域のダウンロードURL(.zip)")
    ap.add_argument("--min-order", type=int, default=4,
                     help="河川次数(ORD_STRA)の下限。大きいほど主要な河川のみに絞られファイルが軽くなる")
    ap.add_argument("--simplify-tolerance", type=float, default=0.01,
                     help="geometry簡略化の許容誤差(度)。ファイルサイズ削減用")
    ap.add_argument("--out", type=str, default="../data/rivers.geojson")
    args = ap.parse_args()

    print(f"[fetch_rivers] ダウンロード中: {args.url}", flush=True)
    res = requests.get(args.url, timeout=600)
    res.raise_for_status()
    print(f"[fetch_rivers] ダウンロード完了: {len(res.content)} bytes", flush=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
            zf.extractall(tmpdir)

        shp_path = None
        for root, _, files in os.walk(tmpdir):
            for f in files:
                if f.endswith(".shp"):
                    shp_path = os.path.join(root, f)
                    break
        if shp_path is None:
            raise FileNotFoundError("ダウンロードしたzip内に.shpファイルが見つかりませんでした")

        print(f"[fetch_rivers] Shapefile読み込み中: {shp_path}", flush=True)
        gdf = gpd.read_file(shp_path)
        print(f"[fetch_rivers] 読み込み完了: {len(gdf)}件, カラム: {list(gdf.columns)}", flush=True)

    # 中国域のバウンディングボックスでクリップ
    gdf = gdf.cx[CHINA_BBOX["minlon"]:CHINA_BBOX["maxlon"], CHINA_BBOX["minlat"]:CHINA_BBOX["maxlat"]]
    print(f"[fetch_rivers] 中国域にクリップ後: {len(gdf)}件", flush=True)

    # 河川次数で絞り込み(小さすぎる支流を除外してファイルサイズを抑える)
    order_col = "ORD_STRA" if "ORD_STRA" in gdf.columns else None
    if order_col:
        gdf = gdf[gdf[order_col] >= args.min_order]
        print(f"[fetch_rivers] 河川次数{args.min_order}以上に絞り込み後: {len(gdf)}件", flush=True)

    # geometry簡略化でファイルサイズを削減
    gdf["geometry"] = gdf["geometry"].simplify(args.simplify_tolerance)

    features = []
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        coords = list(geom.coords)
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[round(x, 4), round(y, 4)] for x, y in coords]},
            "properties": {
                "order": int(row[order_col]) if order_col else None,
            },
        })

    geojson = {"type": "FeatureCollection", "features": features}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False)

    print(f"[fetch_rivers] {len(features)}本の河川を {args.out} に保存しました", flush=True)


if __name__ == "__main__":
    main()
