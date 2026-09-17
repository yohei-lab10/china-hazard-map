#!/usr/bin/env python3
"""既知の湖を含む標高タイルの画素値を確認する診断スクリプト(読み取り専用)。

fetch_lakes.py の水域判定方式(絶対値0m基準 / 局所的な平坦さ基準)を
決める前に、実データがどちらの想定に近いか確認するためのもの。
data/ には何も書き込まない。
"""

import argparse
import numpy as np
import rasterio

# (タイル名, 湖名)。タイル名は 'N034E113.tif' 形式(南西角の整数度)。
# 太湖(約31.3N/120.3E)→ N031E120。青海湖(約36.8N/100.1E)→ N036E100。
TARGETS = [
    ("N031E120.tif", "太湖(標高約3m)"),
    ("N036E100.tif", "青海湖(標高約3196m)"),
]


def diagnose(path, label):
    print(f"\n=== {label} ({path}) ===")
    try:
        with rasterio.open(path) as src:
            arr = src.read(1).astype("float32")
    except Exception as e:
        print(f"  読み込み失敗: {e}")
        return

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

    for th in (0.1, 0.3, 0.5, 1.0):
        flat = stds < th
        n = flat.sum()
        med = np.median(means[flat]) if n else float("nan")
        print(f"  std<{th}mのブロック数: {n:>4} / 全{bh*bw}  "
              f"(該当ブロックのmean標高の中央値: {med:.2f}m)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tiles", default="data/elevation_tiles")
    args = ap.parse_args()
    for name, label in TARGETS:
        diagnose(f"{args.tiles}/{name}", label)


if __name__ == "__main__":
    main()
