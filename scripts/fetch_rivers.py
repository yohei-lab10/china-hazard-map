"""
河川データ取得スクリプト(水害ハザードの「水域近接倍率」用)
出典: HydroRIVERS (WWF / HydroSHEDS)
https://www.hydrosheds.org/products/hydrorivers

**重要な注意**: hydrosheds.orgはボット対策(クラウドIPからの直接アクセスをブロック)を
行っており、GitHub Actions等のクラウド環境から --url でダウンロードしようとすると
403 Forbiddenで失敗することが確認されている。この場合は、ブラウザで手動ダウンロードした
zipファイルを --file オプションでローカルパス指定する運用に切り替えること
(この場合、GitHub Actions上ではなく、手元のパソコンでこのスクリプトを実行し、
生成された data/rivers.geojson を手動でコミット・プッシュする形になる)。

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

実行例(クラウド上、まずはこちらを試す):
    python fetch_rivers.py --url "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_as_shp.zip" --min-order 4 --out ../data/rivers.geojson

実行例(ボット対策で403になった場合、ローカル手動ダウンロード後):
    python fetch_rivers.py --file "./HydroRIVERS_v10_as_shp.zip" --min-order 4 --out ../data/rivers.geojson
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
    ap.add_argument("--url", type=str, default=None,
                     help="hydrosheds.orgで確認したHydroRIVERS Asia地域のダウンロードURL(.zip)。"
                          "GitHub Actions等のクラウド環境からはボット対策でブロックされる場合がある。"
                          "その場合は --file または --shp-dir を使うこと。")
    ap.add_argument("--file", type=str, default=None,
                     help="手動でダウンロード済みのHydroRIVERS zipファイルのローカルパス。--url の代わりに使う。")
    ap.add_argument("--shp-dir", type=str, default=None,
                     help="既に展開済みのShapefile一式が入ったディレクトリ。"
                          "Kaggle CLIの --unzip 等で事前に展開したデータをそのまま使う場合に指定する。"
                          "--url / --file の代わりに使う(zip展開処理をスキップする)。")
    ap.add_argument("--min-order", type=int, default=4,
                     help="河川次数(ORD_STRA)の下限。大きいほど主要な河川のみに絞られファイルが軽くなる")
    ap.add_argument("--simplify-tolerance", type=float, default=0.01,
                     help="geometry簡略化の許容誤差(度)。ファイルサイズ削減用")
    ap.add_argument("--out", type=str, default="../data/rivers.geojson")
    args = ap.parse_args()

    if not args.url and not args.file and not args.shp_dir:
        raise SystemExit("--url / --file / --shp-dir のいずれかを指定してください")

    def find_shp(base_dir):
        for root, _, files in os.walk(base_dir):
            for f in files:
                if f.endswith(".shp"):
                    return os.path.join(root, f)
        return None

    if args.shp_dir:
        print(f"[fetch_rivers] 展開済みディレクトリを使用: {args.shp_dir}", flush=True)
        shp_path = find_shp(args.shp_dir)
        if shp_path is None:
            raise FileNotFoundError(f"{args.shp_dir} 内に.shpファイルが見つかりませんでした")
        print(f"[fetch_rivers] Shapefile読み込み中: {shp_path}", flush=True)
        gdf = gpd.read_file(shp_path)
        print(f"[fetch_rivers] 読み込み完了: {len(gdf)}件, カラム: {list(gdf.columns)}", flush=True)
    else:
        if args.file:
            print(f"[fetch_rivers] ローカルファイルを使用: {args.file}", flush=True)
            with open(args.file, "rb") as f:
                zip_bytes = f.read()
        else:
            print(f"[fetch_rivers] ダウンロード中: {args.url}", flush=True)
            # hydrosheds.orgはプログラムからの直接アクセスをボット対策でブロックすることがある。
            # ブラウザに似せたヘッダーを付けても、GitHub Actions等のクラウドIPそのものが
            # ブロック対象になっている場合は突破できない。その場合は --file / --shp-dir を使うこと。
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.hydrosheds.org/products/hydrorivers",
            }
            res = requests.get(args.url, headers=headers, timeout=600)
            res.raise_for_status()
            zip_bytes = res.content
            print(f"[fetch_rivers] ダウンロード完了: {len(zip_bytes)} bytes", flush=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                zf.extractall(tmpdir)

            shp_path = find_shp(tmpdir)
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
