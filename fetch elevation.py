#!/usr/bin/env python3
"""data/elevation_risk.geojson を data/elevation_tiles/(AW3D30 150m)から生成する。

背景
----
このファイルは全国ヒートマップの色分けに使う1度グリッドの点群である。

旧 fetch_elevation.py は Open-Elevation(SRTM由来・250m)を叩いてこれを生成していた。
2026年9月に標高データを JAXA AW3D30 へ移行した際、elevation_risk.geojson だけは
手作業で作り直したが、スクリプト側は Open-Elevation のまま残っていた。そのため
Actions の update-elevation ジョブを実行すると、AW3D30由来のファイルが
Open-Elevation の値で静かに上書きされる状態になっていた(ジョブは削除済み)。

このスクリプトは、リポジトリ内の150mタイルだけを読んで同じファイルを再生成する。
外部APIには一切アクセスしない。

集約方法について
----------------
地点検索側(21x21グリッド)は建物・樹木を除くために最小値集約を使っているが、
このファイルは1度(約110km四方)という粗い格子の代表値であり、建物の高さは
ほぼ影響しない。むしろ最小値を採ると、1度の中にひとつでも海面や谷底があれば
その格子全体が最低標高になってしまう。そのため既定は平均(mean)とする。
--agg min で最小値に切り替えられるようにはしてある。

なお、この値はあくまで概算である。正確な標高は地点検索側が150mタイルから直接読む。

使い方
------
    python scripts/fetch_elevation.py \
        --tiles data/elevation_tiles \
        --out data/elevation_risk.geojson
"""

import argparse
import json
import os
import sys

import numpy as np
import rasterio

TILE_PX = 720
NODATA_THRESHOLD_M = -1000.0

# 海抜の閾値とリスク値。index.html の elevFromGridProps() が
# risk 1.0/0.75/0.4/0.1 を代表標高 2/4/7/50m に読み戻すため、
# ここを変える場合は index.html 側も合わせること。
RISK_STEPS = [(3.0, 1.0), (5.0, 0.75), (10.0, 0.4)]
RISK_DEFAULT = 0.1


def parse_tile_name(name):
    """'N034E113.tif' -> (34, 113)。タイルの南西角の整数度を返す。"""
    stem = os.path.splitext(os.path.basename(name))[0]
    if len(stem) < 8:
        return None
    try:
        lat = int(stem[1:4]) * (1 if stem[0].upper() == 'N' else -1)
        lon = int(stem[5:8]) * (1 if stem[4].upper() == 'E' else -1)
    except ValueError:
        return None
    return lat, lon


def elev_to_risk(elev_m):
    for threshold, risk in RISK_STEPS:
        if elev_m <= threshold:
            return risk
    return RISK_DEFAULT


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--tiles', default='data/elevation_tiles')
    ap.add_argument('--out', default='data/elevation_risk.geojson')
    ap.add_argument('--agg', choices=['mean', 'min'], default='mean',
                    help='1タイル内の代表標高の取り方(既定: mean。理由は本スクリプト冒頭の説明を参照)')
    args = ap.parse_args()

    tiles = sorted(f for f in os.listdir(args.tiles) if f.lower().endswith('.tif'))
    if not tiles:
        print(f'エラー: {args.tiles} に .tif がありません', file=sys.stderr)
        return 1
    print(f'{len(tiles)} 枚のタイルを走査します(集約: {args.agg})')

    features = []
    failed = []
    for n, name in enumerate(tiles, 1):
        addr = parse_tile_name(name)
        if addr is None:
            print(f'  スキップ(ファイル名が想定形式ではない): {name}')
            continue
        lat0, lon0 = addr
        try:
            with rasterio.open(os.path.join(args.tiles, name)) as src:
                arr = src.read(1).astype('float32')
        except Exception as e:
            failed.append(name)
            print(f'  スキップ(読み込み失敗): {name} — {e}')
            continue

        valid = arr[arr > NODATA_THRESHOLD_M]
        if valid.size == 0:
            # 無効値だけのタイルは、0m(=最高リスク)と誤解されないよう出力しない。
            # 「データが無い」は「安全」でも「危険」でもない。
            print(f'  スキップ(有効画素なし): {name}')
            continue

        elev = float(np.mean(valid)) if args.agg == 'mean' else float(np.min(valid))
        features.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [lon0 + 0.5, lat0 + 0.5]},
            'properties': {
                'risk': elev_to_risk(elev),
                'mean_elev_m': round(elev, 1),
                'agg': args.agg,
            },
        })
        if n % 200 == 0:
            print(f'  {n}/{len(tiles)} 枚')

    print(f'生成した地点: {len(features):,}')
    if failed:
        print(f'読み込みに失敗したタイル: {len(failed)} 枚 — {", ".join(failed[:10])}')

    # タイルが大量に落ちた状態で上書きすると、ヒートマップに穴が空いたまま
    # 気づかない。1割以上落ちたら書き出さない。
    if len(features) < len(tiles) * 0.9:
        print(f'エラー: 生成できた地点が少なすぎます({len(features)}/{len(tiles)})。'
              f'書き出しを中止します', file=sys.stderr)
        return 1

    # 書き込み途中で落ちても既存の正常なファイルを壊さないよう、一時ファイル経由で置換する
    tmp = args.out + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'type': 'FeatureCollection', 'features': features}, f, separators=(',', ':'))
    os.replace(tmp, args.out)
    print(f'書き出し完了: {args.out} ({os.path.getsize(args.out) / 1024:.0f} KB)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
