#!/usr/bin/env python3
"""data/lakes.geojson を HydroLAKES から生成する。

背景
----
外水氾濫(機構1)は rivers.geojson (HydroRIVERS) への近さだけで判定しており、
湖沼は対象外だった。太湖のような大きな湖の湖畔でも外水氾濫スコアが0.00になり、
実態と合わないという指摘を受けて追加した。

なぜHydroLAKESか(経緯)
------------------------
最初はdata/elevation_tiles/(150m標高タイル)から「水面のように平坦なブロック」
を検出する方式(diagnose_lake_tiles.py)を試した。太湖では実際の面積と誤差5%で
良好だったが、洪澤湖(周辺が平坦な農地に囲まれた低地の湖)では閾値をどう
調整しても実際の面積と大きくズレ、単一の閾値では太湖・洪澤湖・青海湖の
3つを同時に満たせないことが実測で判明した(#issueに詳細)。

そこで方針を転換し、著名な湖は緯度経度・面積が既に分かっている既知の対象
であるという前提に立ち、標高データから「発見」するのをやめ、HydroLAKES
(HydroRIVERS/HydroSHEDSと同じLehner氏らのプロジェクト、CC BY 4.0)から
直接抽出する方式にした。HydroLAKES自体が面積(Lake_area属性、km²)を
既に正確に持っているため、自前の面積計算(標高タイル方式で毎回悩んでいた
閾値・投影補正の問題)が丸ごと不要になる。

命名(Lake_name属性)について: HydroLAKESの名前欄は500km²以上の湖、
GRanDに登録された大規模貯水池、GLWDに名前がある一部の小規模湖にしか
入っていない。100km²未満の湖の大半は名前欄が空だが、いずれも目視QAを
経て収録された実在の独立した湖沼である。本スクリプトは名前欄の有無を
問わず、面積0.1km²以上(HydroLAKESの最小収録単位)を全件対象にする。

リポジトリへのコミット方針
--------------------------
HydroLAKESの配布ファイルは全世界で約763MB(shapefile)あるが、これは
生成時に一度だけダウンロード・処理する作業用データであり、リポジトリには
コミットしない(fetch_rainfall.pyがCHIRPSの生データを都度取得して
捨てるのと同じ考え方)。一時ディレクトリで展開・処理し、処理が終わったら
OS側で自動的に削除される。

出力する data/lakes.geojson には、各湖につき面積(area_km2)と間引いた
湖岸の点群(shapely.simplifyで簡略化、coastline_points.jsonと同じ
「間引いて点群にする」発想)だけを持たせ、ファイルサイズを抑える。

使い方
------
    python scripts/fetch_lakes.py \
        --hydrolakes-url "https://data.hydrosheds.org/file/HydroLAKES/HydroLAKES_polys_v10_shp.zip" \
        --out data/lakes.geojson

--hydrolakes-url は配布元のURLが変わる可能性があるため、既定値を埋め込まず
必須の引数にしている。実行前に https://www.hydrosheds.org/products/hydrolakes
で最新の配布URLを確認すること。

出力は GeoJSON FeatureCollection。各Featureが1つの湖で、
geometry は湖岸沿いの間引いた代表点(MultiPoint)、properties.area_km2 が
HydroLAKES由来の面積。rivers.geojsonと同じFeatureCollectionパターンなので、
index.html側は既存のnearbyLakeFeatures/lakeAreaFactorでそのまま読める。
"""

import argparse
import json
import os
import sys
import tempfile
import zipfile

import geopandas as gpd
import requests

# 中国本土の標高タイルが存在する範囲と同じバウンディングボックス(緩衝込み)。
# 国境をまたぐ湖(例: 中露国境のハンカ湖)は、これで一律に「重なっていれば含む」扱いにする。
CHINA_BBOX = (15.0, 55.0, 70.0, 136.0)  # min_lat, max_lat, min_lon, max_lon

MIN_AREA_KM2 = 0.1  # HydroLAKESの最小収録単位(10ヘクタール)と同じ
SIMPLIFY_TOLERANCE_DEG = 0.005  # 約500m。湖岸点群を間引く許容誤差


def download_and_extract(url, workdir):
    """HydroLAKESのzipをダウンロードして展開し、.shpのパスを返す。
    zip自体は展開後すぐに削除してディスクを節約する(TemporaryDirectory全体は
    呼び出し元のwithブロックを抜けた時点でOSが自動削除する)。

    【2026年9月】urllib.request.urlretrieve()はPython標準の素っ気ない
    User-Agent("Python-urllib/3.x")を送るため、配信元(data.hydrosheds.org)に
    403 Forbiddenで弾かれることが実測で判明した。requestsでブラウザ相当の
    User-Agentを付け、ストリーミングダウンロード(820MBを一度にメモリへ
    載せない)に変更した。"""
    zip_path = os.path.join(workdir, 'hydrolakes.zip')
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/120.0.0.0 Safari/537.36'
    }
    print(f'ダウンロード中: {url}')
    with requests.get(url, headers=headers, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get('content-length', 0))
        written = 0
        last_pct = -1
        with open(zip_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                written += len(chunk)
                if total:
                    pct = int(written * 100 / total)
                    if pct != last_pct and pct % 10 == 0:
                        print(f'  {pct}% ({written / (1024*1024):.0f}MB / {total / (1024*1024):.0f}MB)')
                        last_pct = pct
    print(f'ダウンロード完了: {os.path.getsize(zip_path) / (1024*1024):.0f}MB')

    print(f'展開中: {zip_path}')
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(workdir)
    os.remove(zip_path)
    for root, _, files in os.walk(workdir):
        for f in files:
            if f.lower().endswith('.shp'):
                return os.path.join(root, f)
    raise FileNotFoundError('.shpファイルが展開結果の中に見つかりません')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--hydrolakes-url', required=True,
                    help='HydroLAKESのshapefile版(zip)の配布URL')
    ap.add_argument('--out', default='data/lakes.geojson')
    ap.add_argument('--min-area-km2', type=float, default=MIN_AREA_KM2)
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as workdir:
        shp_path = download_and_extract(args.hydrolakes_url, workdir)

        min_lat, max_lat, min_lon, max_lon = CHINA_BBOX
        print('中国本土のバウンディングボックス内だけを読み込みます'
              '(世界全体は読み込まないため、ここは比較的速いはずです)...')
        # geopandasのbbox指定で、読み込み時点で対象範囲だけに絞る
        # (140万件全体を一度メモリに載せることを避ける)。
        gdf = gpd.read_file(shp_path, bbox=(min_lon, min_lat, max_lon, max_lat))
        print(f'バウンディングボックス内: {len(gdf):,} 件')

        if 'Lake_area' not in gdf.columns:
            print(f'エラー: 想定した列名 "Lake_area" が見つかりません。'
                  f'実際の列: {list(gdf.columns)}', file=sys.stderr)
            return 1

        gdf = gdf[gdf['Lake_area'] >= args.min_area_km2]
        print(f'面積{args.min_area_km2}km²以上: {len(gdf):,} 件')

        features = []
        for _, row in gdf.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            geom = geom.simplify(SIMPLIFY_TOLERANCE_DEG, preserve_topology=True)
            # MultiPolygon(離島を持つ湖など)は全パーツの頂点をまとめて1つの
            # 点群にする。湖岸への最短距離判定にしか使わないため、
            # パーツごとに分ける必要はない(river/coastlineと同じ簡略化)。
            if geom.geom_type == 'Polygon':
                polys = [geom]
            elif geom.geom_type == 'MultiPolygon':
                polys = list(geom.geoms)
            else:
                continue
            pts = set()
            for poly in polys:
                for x, y in poly.exterior.coords:
                    pts.add((round(x, 5), round(y, 5)))
            if not pts:
                continue
            features.append({
                'type': 'Feature',
                'properties': {'area_km2': round(float(row['Lake_area']), 2)},
                'geometry': {'type': 'MultiPoint', 'coordinates': sorted(pts)},
            })

        # 出力順を安定させる(差分が読めるように)。面積の大きい順。
        features.sort(key=lambda f: -f['properties']['area_km2'])
        print(f'書き出す湖: {len(features):,} 件')
        if features:
            print('上位5件:')
            for f in features[:5]:
                print(f"  {f['properties']['area_km2']:>10,.1f} km²  "
                      f"({len(f['geometry']['coordinates'])}点)")

        if not features:
            print('エラー: 1件も湖を抽出できませんでした。書き出しを中止します', file=sys.stderr)
            return 1

        geojson = {'type': 'FeatureCollection', 'features': features}
        # 書き込み途中で落ちても既存の正常なファイルを壊さないよう、一時ファイル経由で置換する
        tmp = args.out + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(geojson, f, separators=(',', ':'))
        os.replace(tmp, args.out)
        size_mb = os.path.getsize(args.out) / (1024 * 1024)
        print(f'書き出し完了: {args.out} ({size_mb:.2f} MB)')

    return 0


if __name__ == '__main__':
    sys.exit(main())
