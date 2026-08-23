# 洪水・標高・降雨・河川・Aqueduct Floods・雷データの手動更新手順

地震・台風はGitHub Actionsで完全自動更新されますが、それ以外のハザードは
配布元の都合上、完全自動化が難しいため手動でトリガーする必要があります。

いずれも**GitHub Actionsの手動実行(workflow_dispatch)から実行するのが基本**です
(雷のみ、自動化の仕組みに未組み込みのためローカル実行限定)。

## 洪水実績(推奨頻度: 更新があったときのみ。現状のデータは2023年12月まで)

出典: Dartmouth Flood Observatory (DFO) — Global Flood Records (Zenodo, 2026年3月公開)
https://zenodo.org/records/19288171

**用途の注意**: この洪水実績データは、単独のチップとしては表示されません。
「水害ハザード」の中で、リスク値を最大1.5倍まで底上げする**重み計算専用**のデータです。

### 方法A: GitHub Actionsから実行(推奨)

1. Zenodoのページで最新版のGeoPackage URLを確認する:
   https://zenodo.org/records/19288171 → 「Files」欄の `Global_Flood_Records.gpkg` を右クリックしてURLをコピー
2. GitHubの「Actions」タブ →「Update Hazard Data」→「Run workflow」
3. `flood_gpkg_url` 欄に確認したURLを貼り付けて実行

### 方法B: ローカルで実行

```
pip install geopandas requests
python scripts/fetch_floods.py --gpkg-url "https://zenodo.org/records/19288171/files/Global_Flood_Records.gpkg?download=1" --out data/floods.geojson
```

## 標高(推奨頻度: まれに再実行。標高データはほぼ不変)

出典: Open-Elevation API(無料・APIキー不要)

**用途**: 「水害ハザード」の構成要素の1つ(降雨強度リスクとの高い方を採用)。
以前あった「冠水ハザード」単独チップは廃止され、水害ハザードに統合済み。

### 方法A: GitHub Actionsから実行(推奨)

1. GitHubの「Actions」タブ →「Update Hazard Data」→「Run workflow」
2. `run_elevation` にチェックを入れて実行

### 方法B: ローカルで実行

```
pip install requests
python scripts/fetch_elevation.py --grid-deg 1.0 --out data/elevation_risk.geojson
```

## 降雨強度(推奨頻度: 数ヶ月に1回程度)

出典: CHIRPS(UCサンタバーバラ大学 Climate Hazards Center)。無料・APIキー不要。
中国気象局の暴雨分類基準(24時間降水量)でリスクに変換して使用。

**用途**: 「水害ハザード」の構成要素の1つ(標高リスクとの高い方を採用)。

**注意**: CHIRPSは北緯50度〜南緯50度のみカバー。それより北(黒竜江省北端の一部)は
データ欠損として扱われ、水害ハザードのタイルは灰色で表示される
(「リスクなし」ではなく「データなし」を意味する)。

また本スクリプトは直近の指定日数分のみを集計する近似値であり、
長期の真の統計的極値ではない点に留意すること(詳細はスクリプト内コメント参照)。

### 方法A: GitHub Actionsから実行(推奨・必須に近い)

CHIRPSの日次データを大量にダウンロードするため処理時間が長い(実測で約2分〜、
日数を増やすとさらに伸びる)。timeout 120分を設定済み。

1. GitHubの「Actions」タブ →「Update Hazard Data」→「Run workflow」
2. `run_rainfall` にチェックを入れて実行

### 方法B: ローカルで実行

```
pip install rasterio requests numpy
python scripts/fetch_rainfall.py --days 180 --grid-deg 1.0 --out data/rainfall_risk.geojson
```

`--days` で集計対象の日数を調整可能(長くするほど精度は上がるが処理時間も伸びる)。

## 河川データ(推奨頻度: まれに再実行。河川網はほぼ不変)

出典: HydroRIVERS(HydroSHEDSプロジェクト、WWF他)
https://www.hydrosheds.org/products/hydrorivers

**用途**: 「水害ハザード」の構成要素の1つ。標高リスクを算出する際、河川・湖・海への
近接度によってリスクを底上げする「水域近接倍率」の判定に使用する
(降雨強度データとは独立した経路で、標高リスク側にのみ乗算される)。

**データ規模の注意**: 中国域の河川フィーチャーは139,660本(すべてLineString、
`order`属性=河川次数を保持するが現状サイト側では未使用)。この規模になるため、
サイト側(`index.html`)は読み込み直後に約2.2km四方の格子に基づく空間インデックスを
1回だけ構築し、地点検索のたびに周辺3×3格子(約6.6km四方)の候補だけを距離計算する
仕組みになっている(全件スキャンは行わない)。この仕組みはデータ本体の更新頻度とは
無関係に既に組み込み済みなので、`data/rivers.geojson` を配置するだけで有効になる。

現時点では取得・変換を自動化するスクリプト(`fetch_rivers.py`相当)やGitHub Actions
ワークフローは存在しない。実際の運用は、HydroSHEDSからダウンロード・変換した
`rivers.geojson`(約24.8MB)をGitHub Web UIから`data/`フォルダへ直接アップロードする
手動手順のみ。更新する場合は以下の手順で手動生成する。

### 手順(手動)

1. HydroSHEDS公式サイトで中国を含む対象リージョンのHydroRIVERSシェープファイルを
   ダウンロードする(利用にはHydroSHEDSサイトでの登録が必要な場合がある):
   https://www.hydrosheds.org/products/hydrorivers
2. 中国の範囲でクリップし、GeoJSONに変換してから `data/rivers.geojson` として配置する
   (例: QGISでクリップ→エクスポート、またはGDAL/`ogr2ogr`でのコマンド変換)。
   ```
   ogr2ogr -f GeoJSON -clipsrc <china_bbox_or_boundary> data/rivers.geojson HydroRIVERS_v10_as.shp
   ```
3. 生成された `data/rivers.geojson` をコミット・プッシュする

河川網は地形由来のデータであり短期間ではほぼ変化しないため、年1回未満の頻度でも
問題ない。

## Aqueduct Floods(推奨頻度: 年1回程度。WRI側の更新頻度に準じる)

出典: World Resources Institute (WRI) — Aqueduct Floods Hazard Maps
https://www.wri.org/data/aqueduct-floods

**用途**: 「水害ハザード」とは統合せず、**独立したチップ**として表示。
本格的な水文シミュレーション(河川洪水)に基づくデータであり、
簡易的な代理指標である水害ハザードとは性質が異なるため、意図的に分離している。

デフォルトでは「historical(現在の気候)・100年に1度の再現期間」のシナリオを使用。
将来気候シナリオ(2030/2050/2080年)や別の再現期間を使いたい場合は、
`fetch_aqueduct.py` の `--scenario` `--year` `--rp` 引数で切り替え可能
(利用可能な組み合わせはWRI公式サイトのデータセットページで確認すること)。

### 方法A: GitHub Actionsから実行(推奨)

1. GitHubの「Actions」タブ →「Update Hazard Data」→「Run workflow」
2. `run_aqueduct` にチェックを入れて実行

### 方法B: ローカルで実行

```
pip install rasterio requests numpy
python scripts/fetch_aqueduct.py --scenario historical --model 000000000WATCH --year 1980 --rp 100 --grid-deg 1.0 --out data/aqueduct_floods.geojson
```

## 雷(推奨頻度: 年1回程度。データ自体が多年平均の気候値のため)

出典: WWLLN Global Lightning Climatology (WGLC) v2024.0.0(Kaplan & Lau, University of Calgary)
https://doi.org/10.5281/zenodo.10725446 (CC BY-SA 4.0、要クレジット表示)

2010〜2023年の実測(WWLLN)を集計した月別気候値(多年平均・0.5度格子)。**地震のような
個々のイベント記録ではなく、「この場所は年間平均どれくらい雷が多いか」という傾向マップ**
である点に注意(個々の落雷イベントの生データは無料公開されていないため)。

**用途**: 雷チップの地図表示(密度に応じたグラデーション)・地点検索タイルの両方に使用。
中国気象局の暴雨分類のような公的な絶対基準が雷には存在しないため、洪水実績(DFO)と同様に
「中国域内での相対正規化(上位パーセンタイルをrisk=1.0に切り詰め)」を採用している。

`fetch_lightning.py` は実装済みでワークフローに組み込み可能な状態。サイト側
(`index.html`)も、以前の組み込みサンプル配列(`LIGHTNING_SAMPLE`)ではなく
`data/lightning.geojson` を読み込む方式に変更済み。

### 方法A: GitHub Actionsから実行(推奨)

1. GitHubの「Actions」タブ →「Update Hazard Data」→「Run workflow」
2. `run_lightning` にチェックを入れて実行(要ワークフロー追加)

### 方法B: ローカルで実行

```
pip install xarray netCDF4 requests numpy
python scripts/fetch_lightning.py --out data/lightning.geojson
```

NetCDFファイル(約27MB、`wglc_climatology_30m_monthly.nc`)はスクリプトが自動ダウンロードする。
既にダウンロード済みのファイルがあれば `--nc-file` で指定してダウンロードをスキップできる。

気候値データ(多年平均)のため、年1回程度の更新で十分。WGLC本体の更新頻度に準じる。

