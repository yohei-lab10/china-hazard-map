# china-hazard-map

中国本土を対象とした自然災害(地震・台風・水害・凍結)の過去実績ベース・ハザードマップ。
サーバーを持たない静的サイト(Cloudflare Workers + GitHub Actions)として構成している
(2026年9月、GitHub PagesからCloudflare Workersへ移行。認証にはCloudflare Access
(@ms-ins.com / @ms-ins.com.cnのメールのみ許可)も追加済み。旧GitHub Pagesは安定確認の
ため並行稼働中で、停止予定。経緯・検証根拠は`CHANGELOG.md`参照)。

2026年9月後半、雷ハザードを削除し凍結ハザード(水道管凍結・破裂リスク)に置き換えた。
算出基準・計算式の詳細は`china_hazard_map_manual`(p.26〜28)、設計変遷は`CHANGELOG.md`
を参照。雷の復活が必要になった場合は`LIGHTNING_HANDOVER.md`を参照。

- **設計の全体像・算出式・データソースの詳細・引き継ぎ情報**: `china_hazard_map_manual_v23.pptx`
- **変更履歴(なぜ今の形になったか)**: `CHANGELOG.md`
- **このファイル(README.md)**: 「今どう運用するか」だけを載せる手順書

---

## 目次

1. [構成](#構成)
2. [サイトの初期設定(必須)](#サイトの初期設定必須)
3. [データ更新の一覧](#データ更新の一覧)
4. [GitHub Actionsでの手動実行](#github-actionsでの手動実行)
5. [ローカルで実行する場合](#ローカルで実行する場合)
6. [再現用スクリプト(通常は実行不要)](#再現用スクリプト通常は実行不要)
7. [トラブルシューティング](#トラブルシューティング)

---

## 構成

```
china-hazard-map/
├── index.html                  サイト本体・全ロジック
├── .github/workflows/
│   └── update-data.yml         定期実行・手動実行の定義
├── scripts/                    データ取得・再生成スクリプト
│   ├── fetch_earthquakes.py
│   ├── fetch_typhoons.py
│   ├── fetch_rainfall.py
│   ├── fetch_freeze.py
│   ├── calibrate_freeze_thresholds.py  較正専用の一時診断スクリプト(コミットなし)
│   ├── fetch_floods.py
│   ├── fetch_rivers.py
│   ├── fetch_coastline.py      再生成用(通常は実行不要)
│   ├── fetch_confluences.py    再生成用(通常は実行不要)
│   ├── fetch_elevation.py      再生成用(通常は実行不要、AW3D30版)
│   ├── fetch_lakes.py          再生成用(通常は実行不要、HydroLAKES版)
│   └── requirements.txt
└── data/                       生成済みデータ(リポジトリに同梱)
    ├── earthquakes.geojson
    ├── typhoons.geojson
    ├── rainfall_risk.geojson
    ├── rainfall_annual_max_cache.json
    ├── freeze_risk.geojson          凍結ハザード(1度グリッド、FDD・寒潮階級・放射冷却頻度)
    ├── heating_boundary.geojson     秦嶺・淮河線(供暖境界)。手動作成の静的ファイル、生成スクリプトなし
    ├── floods.geojson              単独表示なし。水害の実績重み専用
    ├── rivers.geojson               単独表示なし。外水氾濫の計算専用
    ├── lakes.geojson                単独表示なし。外水氾濫の計算専用(湖沼、HydroLAKES由来)
    ├── coastline_points.json        単独表示なし。高潮の計算専用
    ├── river_confluences.json       単独表示なし。外水氾濫の合流部補正専用
    ├── elevation_risk.geojson       全国ヒートマップ用(1度グリッド)。凍結ハザードのグリッドもこれを再利用
    └── elevation_tiles/             地点検索用(150mタイル1,578枚・0.646GB)
```

**`update-elevation`ジョブは存在しない(2026年9月に削除)。** 標高タイルは基本的に更新不要のため、
再生成が必要な場合のみ手動で`fetch_elevation.py`を実行する(後述)。

**`lakes.geojson`はHydroLAKES(0.1km²以上、中国国内24,518件)を面積で6段階に区分している。**
10ヘクタール未満の農業用の小さな溜池などは対象外(HydroLAKES自体の最小収録単位のため)。
経緯・閾値の検討過程は`CHANGELOG.md`参照。

**`freeze_risk.geojson`は`elevation_risk.geojson`と同じ1度グリッドを再利用している**が、
経度73〜135.5度・緯度18〜54度の範囲外(中国国外)の地点は`fetch_freeze.py`側で除外する
安全フィルタを追加済み(実行時に除外件数がログ出力される)。

---

## サイトの初期設定(必須)

`index.html`をデプロイする前に、以下の2箇所を確認・設定すること。

### ログイン情報

サイトを開くとID/パスワードを求める簡易的な入口が表示される(社内関係者以外が
偶然URLを開いて閲覧するのを防ぐためのもので、本格的なアクセス制御ではない。
ページのソースを読める人には効果が無い。2026年9月からはCloudflare Accessによる
メールドメイン認証が別レイヤーとして追加されており、こちらが実質的なアクセス制御を担う)。

- 初期値: ID `msichina` / パスワード `msichina`(いずれも同じ)
- 変更する場合: ブラウザのコンソールで以下を実行し、出力されたハッシュ値を
  `index.html`内の`LOGIN_HASH`に貼り替える

```js
crypto.subtle.digest('SHA-256', new TextEncoder().encode('新ID:新パスワード'))
  .then(b => console.log([...new Uint8Array(b)].map(x=>x.toString(16).padStart(2,'0')).join('')))
```

`LOGIN_ID_FALLBACK`/`LOGIN_PW_FALLBACK`(file://等、`crypto.subtle`が使えない
環境向けの平文フォールバック)も、変更する場合はあわせて書き換えること。

### CARTOのAPIキー(地図の見た目)

`index.html`内の`BASEMAP.cartoApiKey`にCARTOのAPIキーを設定すると、白×紺の
ベクター地図(Positron)が表示される。未設定の場合は、キー不要のOpenStreetMap
標準タイルに自動でフォールバックする(見た目は簡易的なものになるが、地図としては
機能する)。現在の本番`index.html`には2026年9月に取得済みのキーが既に設定されている。
再取得・再設定が必要になった場合のみ以下を参照。

- 取得先: https://carto.com/basemaps/apikey (アカウント登録不要、1分程度)
- `BASEMAP.cartoStyle`で`positron`(既定・白基調)/ `voyager`(色付き)/
  `dark-matter`(黒基調)を切り替え可能

---

## データ更新の一覧

| ハザード | 更新方式 | 頻度 |
|---|---|---|
| 地震 | GitHub Actions 自動 | 毎日 |
| 台風 | GitHub Actions 自動 | 週3回(月・水・金) |
| 降雨強度 | GitHub Actions 手動実行(`run_rainfall`) | 必要なとき(通常は年1回未満) |
| 凍結(NASA POWER) | GitHub Actions 手動実行(`run_freeze`) | 気候値のためほぼ不要 |
| 洪水実績(DFO) | GitHub Actions 手動実行(`flood_gpkg_url`を入力) | DFOが新しいGeoPackageを公開したとき |
| 河川(HydroRIVERS) | 手動(下記参照) | ほぼ不要。地形データのため時間で変化しない |
| 湖沼(HydroLAKES) | GitHub Actions 手動実行(`run_lakes`+`hydrolakes_url`) | ほぼ不要。地形データに近い性質のため時間で変化しない |
| 標高(AW3D30) | 手動(下記参照) | ほぼ不要。地形データのため時間で変化しない |
| 供暖境界(秦嶺・淮河線) | 手動(一度限り、生成スクリプトなし) | 境界の定義自体を変更する場合のみ座標を手で書き換える |
| 海岸線・河川合流部 | 手動(下記参照) | rivers.geojsonやelevation_tilesを更新したときのみ |

地震・台風以外は**自動実行のスケジュールに乗っていない**。理由や検討経緯は`CHANGELOG.md`を参照。

---

## GitHub Actionsでの手動実行

GitHubリポジトリの **Actions → Update Hazard Data → Run workflow** から実行する。

入力できる項目は7つ:

| 入力 | 用途 |
|---|---|
| `flood_gpkg_url` | DFOの新しいGeoPackage(.gpkg)のURLを入力すると`floods.geojson`を更新。空欄なら何もしない |
| `run_rainfall` | チェックすると`rainfall_risk.geojson`を更新(所要時間の目安: 約1.6時間/10年分。タイムアウト300分) |
| `run_freeze` | チェックすると`freeze_risk.geojson`を更新(NASA POWER、認証・APIキー不要。1,516地点で約45分) |
| `run_freeze_calibration` | チェックすると①FDDの絶対閾値を実被害事例から較正する一時診断を実行(`freeze_calibration_result.json`、コミットなし。詳細はCHANGELOG参照) |
| `run_lakes` | チェックすると`lakes.geojson`を再生成(HydroLAKESから中国分を抽出。初回生成後は基本的に再実行不要) |
| `hydrolakes_url` | `run_lakes`実行時に必須のテキスト入力。**HydroSHEDSの配布サーバー(data.hydrosheds.org)がGitHub Actions実行環境からのダウンロードを403で拒否するため、直接のURLは使えない。** 一度手元の端末でHydroLAKESのshapefile(.zip、約820MB)をダウンロードし、GitHub Releasesに添付してから、そのReleaseのダウンロードURLをここに入力する運用にしている(実例・経緯は`CHANGELOG.md`参照) |

地震・台風はスケジュール実行に加えて、上記フォームからいつでも手動実行できる(入力欄は不要)。

---

## ローカルで実行する場合

```bash
pip install -r scripts/requirements.txt --break-system-packages

# 地震(日次自動更新と同じ処理)
python scripts/fetch_earthquakes.py --minmag 5.0 --out data/earthquakes.geojson

# 台風
python scripts/fetch_typhoons.py --out data/typhoons.geojson

# 降雨強度(CHIRPS。10年分で1〜2時間程度)
python scripts/fetch_rainfall.py --grid-deg 1.0 \
    --out data/rainfall_risk.geojson \
    --cache data/rainfall_annual_max_cache.json

# 凍結ハザード(NASA POWER。認証・APIキー不要。elevation_risk.geojsonのグリッドを再利用、約45分)
python scripts/fetch_freeze.py --elevation data/elevation_risk.geojson --out data/freeze_risk.geojson

# 凍結①FDDの閾値較正(実被害事例の期間だけを対象に診断。何もコミットしない)
python scripts/calibrate_freeze_thresholds.py

# 洪水実績(DFOのGeoPackage URLが必要)
python scripts/fetch_floods.py --gpkg-url "<DFOのURL>" --out data/floods.geojson
```

いずれも一時ファイル経由で書き込むため、途中で止めても既存の正常なファイルは壊れない
(calibrate_freeze_thresholds.pyは診断専用でファイルを書き換えない)。

### 河川(HydroRIVERS)— 完全自動化はしていない

```bash
python scripts/fetch_rivers.py
```

hydrosheds.org からのダウンロードは自動化していない(ライセンス上の理由と、ほぼ更新不要なため)。
`scripts/fetch_rivers.py`の冒頭コメントに手動ダウンロードの手順を記載している。生成後は
GitHub Web UIから`data/rivers.geojson`を直接アップロードする。

### 標高タイル(AW3D30)— 初回のみ手動ダウンロード

`data/elevation_tiles/`の150mタイルはJAXA公式サイトから手動でダウンロードしたものである
(自動化不可。詳細は`china_hazard_map_manual`p.8)。通常はこのタイル自体を
更新する必要はない。

### 供暖境界(秦嶺・淮河線)— 生成スクリプトなしの静的ファイル

`data/heating_boundary.geojson`は、秦嶺・淮河線を北緯33度の直線で簡略近似した
座標を手作業で記述した静的ファイルで、対応する取得・生成スクリプトは存在しない。
境界の定義自体(直線近似の緯度、あるいは折れ線化など)を見直す場合のみ、座標を
直接書き換える。詳細・検討経緯は`china_hazard_map_manual`p.26と`CHANGELOG.md`参照。

---

## 再現用スクリプト(通常は実行不要)

以下の3つは、**既存のデータファイルを作り直すためのスクリプト**である。今のファイルは
正しく動いているので、壊れたり更新元(`rivers.geojson`や`elevation_tiles/`)が変わったりしない限り、
実行する必要はない。

```bash
# 海岸線の点群(elevation_tiles/ から抽出。高潮ハザードの計算に使用)
python scripts/fetch_coastline.py \
    --tiles data/elevation_tiles \
    --out data/coastline_points.json

# 河川の合流部(rivers.geojson から抽出。外水氾濫の補正に使用)
python scripts/fetch_confluences.py \
    --rivers data/rivers.geojson \
    --out data/river_confluences.json

# 全国ヒートマップ用の標高グリッド(elevation_tiles/ から生成。AW3D30版)
python scripts/fetch_elevation.py \
    --tiles data/elevation_tiles \
    --out data/elevation_risk.geojson
```

実行後は、出力内の点数・合流本数の内訳などをコンソール表示で確認すること(各スクリプトが
自動で表示する)。大きく数字がずれた場合は、`rivers.geojson`や`elevation_tiles/`側の変更を疑う。

**このスクリプト群を実行しても、通常運用中のスコア計算やUIの挙動は変わらない。**
既存ファイルを同じ手順で再生成できるようにするためのものである。

---

## トラブルシューティング

- **GitHub Actionsが「成功」と表示されたのに数値がおかしい**: `run_rainfall`は途中で
  タイムアウトしても正常終了したように見える設計。`rainfall_annual_max_cache.json`の
  `_progress`キーで完走を確認すること(詳細は`CHANGELOG.md`)。
- **地震・台風・水害のハザードが急に「データなし」表示になった**: 該当するGeoJSONの取得に
  失敗している。2026年9月以降、取得失敗時は0や「影響なし」ではなく、明示的に
  「評価不能」と表示する設計にしてある(詳細は`china_hazard_map_manual`p.17)。
- **湖の近くなのに外水氾濫のスコアが低い(0.00に近い)**: `lakes.geojson`(HydroLAKES由来)は
  面積0.1km²(10ヘクタール)未満の池・小規模な貯水池を収録していない。この場合は
  「地形データの解像度による制約」であり、取得失敗ではない。
- **凍結ハザードのスコアが特定の地域だけ不自然に高い/低い**: ①FDDの区分閾値、②寒潮の
  黄色基準、④放射冷却の補正係数(β)・南方割増し係数(γ)はいずれも実被害事例からの較正値、
  または仮置きの暫定値であり、実データのヒストグラム検証を経て今後調整される可能性がある
  (現時点の値・根拠は`china_hazard_map_manual`p.26〜28のPARAMETER REGISTER参照)。
- **凍結ハザードの地点に日本・フィリピン海付近が混じる**: `fetch_freeze.py`は
  `elevation_risk.geojson`のグリッドを再利用しており、同ファイル自体に中国国外の地点が
  含まれる場合はバウンディングボックス(経度73〜135.5度・緯度18〜54度)で除外される。
  それでも混入する場合は`elevation_risk.geojson`側の生成ロジックを確認すること。
- **設計判断の理由が知りたい**: まず`china_hazard_map_manual`を確認し、
  経緯の詳細が要る場合のみ`CHANGELOG.md`を日付で検索する。
