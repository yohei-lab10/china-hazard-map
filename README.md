# 中国自然災害ハザードマップ データ取り込みスクリプト

## 前提

このスクリプト群は**ネットワーク接続がある環境**(ご自身のPC/サーバー/GitHub Actions等)で実行してください。
Claude.aiのアーティファクト実行環境やコード実行サンドボックスはネットワークアクセスが制限されているため、
ここでは動作しません。

## セットアップ

```bash
pip install pandas requests xarray netCDF4
```

## 実行順序

```bash
cd scripts

# 1. 地震(毎日実行推奨・軽量)
python fetch_earthquakes.py --days 30 --minmag 3.5 --out ../data/earthquakes.geojson

# 2. 台風(月1回で十分・処理に数分かかる場合あり)
python fetch_typhoons.py --years 10 --out ../data/typhoons.geojson

# 3. 洪水(DFOの最新ダウンロードURLを事前に確認してから実行)
python fetch_floods.py --csv-url "https://floodobservatory.colorado.edu/.../archive.csv" --out ../data/floods.geojson

# 4. 雷(NetCDFを事前にZenodoから手動取得してから実行)
python fetch_lightning.py --nc-file wglc_data/WGLC_monthly_climatology.nc --out ../data/lightning.geojson
```

## 定期実行(cron例)

```cron
# 地震: 毎日6時
0 6 * * * cd /path/to/hazardmap/scripts && python fetch_earthquakes.py --out ../data/earthquakes.geojson

# 台風: 毎月1日3時
0 3 1 * * cd /path/to/hazardmap/scripts && python fetch_typhoons.py --out ../data/typhoons.geojson

# 洪水・雷: 更新頻度が低いデータのため、四半期に1回程度の手動実行で十分
```

## フロント側の変更点

`index.html` 内の `TYPHOON_SAMPLE` / `FLOOD_SAMPLE` / `LIGHTNING_SAMPLE` の埋め込み配列を、
`../data/*.geojson` を `fetch()` で読み込む形に差し替えてください。地震は既にUSGS APIを
直接呼び出しているため変更不要です。

```js
const res = await fetch('data/typhoons.geojson');
const geojson = await res.json();
```

## 注意事項

- DFOのウェブサイトは2026年時点でリニューアル中のため、ダウンロードURLは実行前に必ず
  https://floodobservatory.colorado.edu/dfo-wiki/index.php?title=Main_Page で確認してください。
- WGLC(落雷)データはZenodoの利用条件(CC BY-SA 4.0)に従い、出典表記を残してください。
- IBTrACS・USGSはパブリックドメイン/自由利用ですが、公開サイトには出典クレジットの表示を推奨します。
