#!/usr/bin/env python3
"""既知の湖を含む標高タイルの画素値を確認する診断スクリプト(読み取り専用)。

fetch_lakes.py の水域判定方式(絶対値0m基準 / 局所的な平坦さ基準)を
決める前に、実データがどちらの想定に近いか確認するためのもの。
data/ には何も書き込まない。

【2026年9月改訂】青海湖(東西105km・南北63km)は1タイル(1度四方)に
収まらないため、隣接4タイル分を対象に追加し、std<1.0m基準での
面積換算(km²)を実際の湖面積(太湖2,338km²・青海湖4,546km²)と
突き合わせられるようにした。
"""

import argparse
import os

import numpy as np
import rasterio

# (タイル名, 湖名)。タイル名は 'N034E113.tif' 形式(南西角の整数度)。
# 太湖(約31.3N/120.3E)→ N031E120。
# 青海湖(約36.5〜37.2N、99.6〜100.8E)は1タイルに収まらないため4枚を対象にする。
TARGETS = [
    ("N031E120.tif", "太湖(標高約3m)"),
    ("N036E100.tif", "青海湖・東側"),
    ("N036E099.tif", "青海湖・南西"),
    ("N037E100.tif", "青海湖・北東"),
    ("N037E099.tif", "青海湖・北西"),
    ("N033E118.tif", "洪澤湖(標高約12m)"),
]


def diagnose(path, label):
    """タイル1枚を診断し、std<1.0m基準での面積換算(km²)を返す。
    読み込み失敗時は0.0を返す(呼び出し側の合計計算を壊さないため)。"""
    print(f"\n=== {label} ({path}) ===")
    try:
        with rasterio.open(path) as src:
            arr = src.read(1).astype("float32")
    except Exception as e:
        print(f"  読み込み失敗: {e}")
        return 0.0

    print(f"  タイル全体: min={np.nanmin(arr):.2f}  max={np.nanmax(arr):.2f}  "
          f"mean={np.nanmean(arr):.2f}  std={np.nanstd(arr):.2f}")

    h, w = arr.shape
    bh, bw = h // 12, w // 12
    stds = np.zeros((bh, bw))
    means = np.zeros((bh, bw))
    for i in range(bh):
        for j in range(bw):
            block = arr[i*12:(i+1)*12, j*12:(j+1)*12]
            stds[i, j] = np.nanstd(block)
            means[i, j] = np.nanmean(block)

    print(f"  ブロックstdの分布: min={stds.min():.3f} / "
          f"25%tile={np.percentile(stds,25):.3f} / "
          f"50%tile={np.percentile(stds,50):.3f} / "
          f"75%tile={np.percentile(stds,75):.3f} / max={stds.max():.3f}")

    # 1ブロック(12x12画素 ≒ 1.85km四方)の面積(km²)。
    # タイル名の南西角の緯度(整数度)+0.5をタイル中心緯度とみなし、
    # 経度方向だけ cos(緯度) で補正する(緯度方向は場所によらず一定)。
    fname = os.path.basename(path)
    tile_lat = int(fname[1:4])
    if fname[0].upper() == "S":
        tile_lat = -tile_lat
    block_deg = 12 / 720  # = 1/60度
    block_side_lat_km = block_deg * 111.32
    block_side_lon_km = block_deg * 111.32 * np.cos(np.radians(tile_lat + 0.5))
    block_area_km2 = block_side_lat_km * block_side_lon_km

    area_at_1_0 = 0.0
    for th in (0.1, 0.3, 0.5, 1.0, 1.5, 2.0, 3.0):
        flat = stds < th
        n = int(flat.sum())
        med = np.median(means[flat]) if n else float("nan")
        area = n * block_area_km2
        print(f"  std<{th}mのブロック数: {n:>4} / 全{bh*bw}  "
              f"(mean標高中央値: {med:.2f}m, 面積換算: {area:.0f}km²)")
        if th == 1.0:
            area_at_1_0 = area

    return area_at_1_0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tiles", default="data/elevation_tiles")
    args = ap.parse_args()

    # 青海湖は4タイルにまたがるため、std<1.0m基準での面積換算を合計する。
    # 太湖は単独タイルなのでそのまま診断のみ行う。
    qinghai_total_km2 = 0.0
    for name, label in TARGETS:
        area = diagnose(f"{args.tiles}/{name}", label)
        if "青海湖" in label:
            qinghai_total_km2 += area

    print("\n=== 青海湖 4タイル合計(std<1.0m基準) ===")
    print(f"  合計面積: {qinghai_total_km2:.0f} km²(実際の青海湖: 約4546km²)")


if __name__ == "__main__":
    main()
