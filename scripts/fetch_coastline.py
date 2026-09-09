#!/usr/bin/env python3
"""data/coastline_points.json を data/elevation_tiles/ から生成する。

背景
----
高潮(機構3)は「海岸線までの距離」を必要とする。index.html には陸/海の粗い判定用に
30頂点の CHINA_COASTLINE_POLYGON があるが、これは渤海湾や杭州湾を横切っているため、
距離の計測には使えない(天津が「海岸から329km」と判定され、高潮の15km判定が
全国どこでも成立しなかった)。

そこで AW3D30 では海面が標高0で埋まっていることを利用し、150m標高タイルから
海岸線を抽出する。720x720画素のタイルを12x12画素(約1.85km)のブロックに集約し、
「海ブロック」に隣接する陸ブロックの中心を海岸線の代表点として書き出す。

このスクリプトが無かった間、coastline_points.json は手作業で生成されており
再現手段がリポジトリに存在しなかった。その状態を解消するために追加した。

使い方
------
    python scripts/fetch_coastline.py \
        --tiles data/elevation_tiles \
        --out data/coastline_points.json

出力形式は GeoJSON ではなく {"points": [[経度, 緯度], ...]} の独自形式である点に注意。
index.html 側は fetchGeojsonSafe ではなく fetchPointsSafe で読む。
"""

import argparse
import json
import os
import sys

import numpy as np
import rasterio

TILE_PX = 720          # 1タイル = 720x720画素(1画素 = 1/720度 ≒ 150m)
BLOCK_PX = 12          # 12画素 = 1/60度 ≒ 1.85km を1ブロックとする
BLOCKS_PER_DEG = TILE_PX // BLOCK_PX   # = 60

# 海ブロックの判定条件。AW3D30 は海面を0で埋めるが、波打ち際の画素が
# わずかに正になることがあるため、最大値と平均の両方で見る。
SEA_MAX_M = 0.0
SEA_MEAN_M = 0.05

# AW3D30 の無効値。無効画素は「海」とも「陸」とも判定しない。
NODATA_THRESHOLD_M = -1000.0
# ブロック内の有効画素がこの割合を下回ったら、そのブロックは判定しない
MIN_VALID_FRACTION = 0.5

# 既定の対象範囲(中国本土の海岸線が存在する範囲)。
# 範囲を絞らないと内陸の湖沼や国外の海岸まで拾ってしまう。
DEFAULT_BBOX = (17.5, 41.5, 107.5, 126.5)   # min_lat, max_lat, min_lon, max_lon


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


def classify_tile(path):
    """タイル1枚を読み、ブロック単位で「海かどうか」を返す。

    戻り値は {(グローバルブロック緯度index, 経度index): True(海)/False(陸)}。
    判定不能(無効画素だらけ)のブロックは辞書に含めない。
    """
    with rasterio.open(path) as src:
        arr = src.read(1).astype('float32')

    if arr.shape != (TILE_PX, TILE_PX):
        raise ValueError(f'{path}: 期待するサイズは {TILE_PX}x{TILE_PX} ですが {arr.shape} でした')

    valid = arr > NODATA_THRESHOLD_M
    # 無効画素は集計から外すため NaN に置き換える
    arr = np.where(valid, arr, np.nan)

    # (60, 12, 60, 12) に変形し、軸1と軸3(ブロック内の画素)で集計する
    b = arr.reshape(BLOCKS_PER_DEG, BLOCK_PX, BLOCKS_PER_DEG, BLOCK_PX)
    v = valid.reshape(BLOCKS_PER_DEG, BLOCK_PX, BLOCKS_PER_DEG, BLOCK_PX)

    valid_frac = v.mean(axis=(1, 3))
    with np.errstate(invalid='ignore'):
        block_max = np.nanmax(b, axis=(1, 3))
        block_mean = np.nanmean(b, axis=(1, 3))

    is_sea = (block_max <= SEA_MAX_M) & (block_mean <= SEA_MEAN_M)
    usable = valid_frac >= MIN_VALID_FRACTION

    lat0, lon0 = parse_tile_name(path)
    out = {}
    for i in range(BLOCKS_PER_DEG):      # i = 0 がタイル北端
        for j in range(BLOCKS_PER_DEG):  # j = 0 がタイル西端
            if not usable[i, j]:
                continue
            # グローバルなブロック格子(1/60度刻み)のインデックスに変換する。
            # 行0はタイル北端なので、緯度indexは (lat0+1)*60 - i - 1 になる。
            gj = (lat0 + 1) * BLOCKS_PER_DEG - i - 1
            gi = lon0 * BLOCKS_PER_DEG + j
            out[(gj, gi)] = bool(is_sea[i, j])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--tiles', default='data/elevation_tiles',
                    help='150m標高タイル(*.tif)のディレクトリ')
    ap.add_argument('--out', default='data/coastline_points.json')
    ap.add_argument('--bbox', nargs=4, type=float, metavar=('MINLAT', 'MAXLAT', 'MINLON', 'MAXLON'),
                    default=list(DEFAULT_BBOX),
                    help='出力する点の範囲。既定は中国本土の海岸線を含む範囲')
    args = ap.parse_args()

    min_lat, max_lat, min_lon, max_lon = args.bbox

    tiles = sorted(f for f in os.listdir(args.tiles) if f.lower().endswith('.tif'))
    if not tiles:
        print(f'エラー: {args.tiles} に .tif がありません', file=sys.stderr)
        return 1
    print(f'{len(tiles)} 枚のタイルを走査します')

    # タイルをまたぐ隣接判定のため、まず全ブロックの海/陸を1つの辞書に集める。
    # タイル単位で完結させると、タイル境界にある海岸線を取りこぼす。
    sea_map = {}
    for n, name in enumerate(tiles, 1):
        addr = parse_tile_name(name)
        if addr is None:
            print(f'  スキップ(ファイル名が想定形式ではない): {name}')
            continue
        try:
            sea_map.update(classify_tile(os.path.join(args.tiles, name)))
        except Exception as e:            # 1枚壊れていても全体を止めない
            print(f'  スキップ(読み込み失敗): {name} — {e}')
        if n % 200 == 0:
            print(f'  {n}/{len(tiles)} 枚')

    print(f'ブロック総数: {len(sea_map):,}')

    # 陸ブロックのうち、上下左右いずれかが海ブロックであるものを海岸とする。
    # 斜めは含めない(海岸線が二重に太るのを避けるため)。
    points = []
    for (gj, gi), is_sea in sea_map.items():
        if is_sea:
            continue
        neighbours = ((gj + 1, gi), (gj - 1, gi), (gj, gi + 1), (gj, gi - 1))
        if not any(sea_map.get(nb) for nb in neighbours):
            continue
        lat = (gj + 0.5) / BLOCKS_PER_DEG
        lon = (gi + 0.5) / BLOCKS_PER_DEG
        if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
            continue
        points.append([round(lon, 5), round(lat, 5)])

    # 出力順を安定させる(差分が読めるように)
    points.sort(key=lambda p: (p[1], p[0]))
    print(f'海岸線の点: {len(points):,} 点')

    if not points:
        print('エラー: 1点も抽出できませんでした。書き出しを中止します', file=sys.stderr)
        return 1

    # 書き込み途中で落ちても既存の正常なファイルを壊さないよう、一時ファイル経由で置換する
    tmp = args.out + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'points': points}, f, separators=(',', ':'))
    os.replace(tmp, args.out)
    print(f'書き出し完了: {args.out} ({os.path.getsize(args.out) / 1024:.0f} KB)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
