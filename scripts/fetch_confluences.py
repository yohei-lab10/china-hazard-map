#!/usr/bin/env python3
"""data/river_confluences.json を data/rivers.geojson から生成する。

背景
----
保険会社の水災リスクチェックリストが「敷地は河川の合流部の近くにあるか」を問うため、
外水氾濫(機構1)に合流部係数を導入した。合流部から500m以内なら河川スコアを1.15倍する。

HydroRIVERS の線分は下流向きに並んでいる。したがって「複数の線分の下流端が同じ座標に
集まる点」が合流部にあたる。座標は約11m(小数点以下4桁)に丸めて一致判定する。
HydroRIVERS の位置精度は15秒グリッド(約500m)なので、これより細かく見ても意味がない。

このスクリプトが無かった間、river_confluences.json は手作業で生成されており
再現手段がリポジトリに存在しなかった。その状態を解消するために追加した。

使い方
------
    python scripts/fetch_confluences.py \
        --rivers data/rivers.geojson \
        --out data/river_confluences.json

出力形式は GeoJSON ではなく {"points": [[経度, 緯度], ...]} の独自形式である点に注意。
index.html 側は fetchGeojsonSafe ではなく fetchPointsSafe で読む。
"""

import argparse
import collections
import json
import os
import sys

# 座標の丸め桁数。4桁 = 約11m。HydroRIVERS の位置精度(約500m)より十分細かく、
# かつ浮動小数の誤差で同一点が別物になることを防げる粒度。
COORD_DECIMALS = 4

# 何本の下流端が集まったら合流部とみなすか
MIN_BRANCHES = 2


def endpoint_key(coord):
    return (round(coord[0], COORD_DECIMALS), round(coord[1], COORD_DECIMALS))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--rivers', default='data/rivers.geojson')
    ap.add_argument('--out', default='data/river_confluences.json')
    args = ap.parse_args()

    if not os.path.exists(args.rivers):
        print(f'エラー: {args.rivers} がありません。'
              f'先に scripts/fetch_rivers.py で生成してください', file=sys.stderr)
        return 1

    with open(args.rivers, encoding='utf-8') as f:
        gj = json.load(f)

    feats = gj.get('features')
    if not feats:
        print(f'エラー: {args.rivers} に features がありません', file=sys.stderr)
        return 1
    print(f'河川: {len(feats):,} 本')

    # 下流端(座標列の最後)ごとに、そこへ流れ込む線分の order を集める
    ends = collections.defaultdict(list)
    skipped = 0
    for feat in feats:
        geom = feat.get('geometry') or {}
        coords = geom.get('coordinates')
        # MultiLineString が混ざっている場合は最初のパートの終端を使う
        if geom.get('type') == 'MultiLineString' and coords:
            coords = coords[0]
        if not coords or len(coords) < 2:
            skipped += 1
            continue
        ends[endpoint_key(coords[-1])].append((feat.get('properties') or {}).get('order'))

    if skipped:
        print(f'  座標が不足していてスキップ: {skipped} 本')

    conf = {k: v for k, v in ends.items() if len(v) >= MIN_BRANCHES}

    # 何本合流かの内訳。過去の生成では 2本=5,113 / 3本=21 / 4本=3 だった。
    # 大きく変わっていたら rivers.geojson 側の変更を疑うこと。
    breakdown = collections.Counter(len(v) for v in conf.values())
    print(f'合流部: {len(conf):,} 点')
    for n in sorted(breakdown):
        print(f'  {n}本合流: {breakdown[n]:,} 点')

    if not conf:
        print('エラー: 1点も抽出できませんでした。書き出しを中止します', file=sys.stderr)
        return 1

    # 出力順を安定させる(差分が読めるように)
    points = sorted(([lon, lat] for lon, lat in conf), key=lambda p: (p[1], p[0]))

    # 書き込み途中で落ちても既存の正常なファイルを壊さないよう、一時ファイル経由で置換する
    tmp = args.out + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'points': points}, f, separators=(',', ':'))
    os.replace(tmp, args.out)
    print(f'書き出し完了: {args.out} ({os.path.getsize(args.out) / 1024:.0f} KB)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
