# china-hazard-map

中国本土を対象とした自然災害(地震・台風・水害・雷)の過去実績ベース・ハザードマップ。
サーバーを持たない静的サイト(GitHub Pages + GitHub Actions)として構成している。

- **設計の全体像・算出式・データソースの詳細・引き継ぎ情報**: `china_hazard_map_manual_v5.pptx`
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
│   ├── fetch_lightning.py
│   ├── fetch_floods.py
│   ├── fetch_rivers.py
│   ├── fetch_coastline.py      再生成用(通常は実行不要)
│   ├── fetch_confluences.py    再生成用(通常は実行不要)
│   ├── fetch_elevation.py      再生成用(通常は実行不要、AW3D30版)
│   └── requirements.txt
└── data/                       生成済みデータ(リポジトリに同梱)
    ├── earthquakes.geojson
    ├── typhoons.geojson
    ├── rainfall_risk.geojson
    ├── rainfall_annual_max_cache.json
    ├── lightning.geojson
    ├── floods.geojson              単独表示なし。水害の実績重み専用
    ├── rivers.geojson               単独表示なし。外水氾濫の計算専用
    ├── coastline_points.json        単独表示なし。高潮の計算専用
    ├── river_confluences.json       単独表示なし。外水氾濫の合流部補正専用
    ├── elevation_risk.geojson       全国ヒートマップ用(1度グリッド)
    └── elevation_tiles/             地点検索用(150mタイル1,578枚・0.646GB)
```

**`update-elevation`ジョブは存在しない(2026年9月に削除)。** 標高タイルは基本的に更新不要のため、
再生成が必要な場合のみ手動で`fetch_elevation.py`を実行する(後述)。

---

## サイトの初期設定(必須)

`index.html`をデプロイする前に、以下の2箇所を確認・設定すること。

### ログイン情報

サイトを開くとID/パスワードを求める簡易的な入口が表示される(社内関係者以外が
偶然URLを開いて閲覧するのを防ぐためのもので、本格的なアクセス制御ではない。
ページのソースを読める人には効果が無い)。

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
機能する)。

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
| 雷 | GitHub Actions 手動実行(`run_lightning`) | 気候値のためほぼ不要 |
| 洪水実績(DFO) | GitHub Actions 手動実行(`flood_gpkg_url`を入力) | DFOが新しいGeoPackageを公開したとき |
| 河川(HydroRIVERS) | 手動(下記参照) | ほぼ不要。地形データのため時間で変化しない |
| 標高(AW3D30) | 手動(下記参照) | ほぼ不要。地形データのため時間で変化しない |
| 海岸線・河川合流部 | 手動(下記参照) | rivers.geojsonやelevation_tilesを更新したときのみ |

地震・台風以外は**自動実行のスケジュールに乗っていない**。理由や検討経緯は`CHANGELOG.md`を参照。

---

## GitHub Actionsでの手動実行

GitHubリポジトリの **Actions → Update Hazard Data → Run workflow** から実行する。

入力できる項目は3つ:

| 入力 | 用途 |
|---|---|
| `flood_gpkg_url` | DFOの新しいGeoPackage(.gpkg)のURLを入力すると`floods.geojson`を更新。空欄なら何もしない |
| `run_rainfall` | チェックすると`rainfall_risk.geojson`を更新(所要時間の目安: 約1.6時間/10年分。タイムアウト300分) |
| `run_lightning` | チェックすると`lightning.geojson`を更新(約27MBのNetCDFを取得) |

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

# 雷(WGLC気候値)
python scripts/fetch_lightning.py --out data/lightning.geojson

# 洪水実績(DFOのGeoPackage URLが必要)
python scripts/fetch_floods.py --gpkg-url "<DFOのURL>" --out data/floods.geojson
```

いずれも一時ファイル経由で書き込むため、途中で止めても既存の正常なファイルは壊れない。

### 河川(HydroRIVERS)— 完全自動化はしていない

```bash
python scripts/fetch_rivers.py
```

hydrosheds.org からのダウンロードは自動化していない(ライセンス上の理由と、ほぼ更新不要なため)。
`scripts/fetch_rivers.py`の冒頭コメントに手動ダウンロードの手順を記載している。生成後は
GitHub Web UIから`data/rivers.geojson`を直接アップロードする。

### 標高タイル(AW3D30)— 初回のみ手動ダウンロード

`data/elevation_tiles/`の150mタイルはJAXA公式サイトから手動でダウンロードしたものである
(自動化不可。詳細は`china_hazard_map_manual_v5.pptx` p.8)。通常はこのタイル自体を
更新する必要はない。

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
  「評価不能」と表示する設計にしてある(詳細は`china_hazard_map_manual_v5.pptx` p.17)。
- **設計判断の理由が知りたい**: まず`china_hazard_map_manual_v5.pptx`を確認し、
  経緯の詳細が要る場合のみ`CHANGELOG.md`を日付で検索する。
