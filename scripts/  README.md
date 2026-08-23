洪水・標高・降雨・河川・Aqueduct Floods・雷データの手動更新手順
地震・台風はGitHub Actionsで完全自動更新されますが、それ以外のハザードは
配布元の都合上、完全自動化が難しいため手動でトリガーする必要があります。
いずれもGitHub Actionsの手動実行(workflow_dispatch)から実行するのが基本です
(雷のみ、自動化の仕組みに未組み込みのためローカル実行限定)。
洪水実績(推奨頻度: 更新があったときのみ。現状のデータは2023年12月まで)
出典: Dartmouth Flood Observatory (DFO) — Global Flood Records (Zenodo, 2026年3月公開)
https://zenodo.org/records/19288171
用途の注意: この洪水実績データは、単独のチップとしては表示されません。
「水害ハザード」の中で、リスク値を最大1.5倍まで底上げする重み計算専用のデータです。
方法A: GitHub Actionsから実行(推奨)
	1.	Zenodoのページで最新版のGeoPackage URLを確認する:
https://zenodo.org/records/19288171 → 「Files」欄の Global_Flood_Records.gpkg を右クリックしてURLをコピー
	2.	GitHubの「Actions」タブ →「Update Hazard Data」→「Run workflow」
	3.	flood_gpkg_url 欄に確認したURLを貼り付けて実行
方法B: ローカルで実行
