# OlfaLink v1.8.2 Cloud Run 起動対策

## 修正したこと

`APP_MODE=aws_pull` のとき、従来はアプリ import 時に `FirestoreOnDemandStore()` がインスタンス化され、Cloud Run起動がFirestore認証/初期化に依存していました。v1.8.2では一時的な結果保存を `MemoryOnDemandStore` に変更しました。AWS `/data` 取得、CNN実推論、辞書フォールバック、LLM接続、LED非依存、デモデータUIは同じです。HTTP `PORT` は既に環境変数対応済みです。

**制限:** 保存済みの解析結果はCloud Runの停止・再起動後には残りません。解析ボタンを押せば新しく推論できます。AWSの過去のデータを変更しません。これは発表用の安定化であり、Firestoreの永続化が必要なら後から再導入できます。

**注意:** 今回の失敗原因はまだログ未確認です。この対策が必ず解決するとは断定できません。失敗したリビジョンのログを確認してください。

## エラーの調査（GPU起動なし）

```bash
cd ~/OlfaLink/olfalink-webapp
bash diagnose_failed_revision.sh olfalink-00009-6g6
```

## Webのみ再デプロイ（GPU起動なし）

```bash
cd ~/OlfaLink/olfalink-webapp
bash deploy_aws_data_pull.sh
curl -fsS https://olfalink-15647954758.asia-northeast1.run.app/api/v1/healthz
```

## Web更新＋GPU起動（起動課金あり）

```bash
cd ~/OlfaLink/olfalink-webapp
bash start_server_with_gpu.sh
```

このスクリプトはWebの健康状態が確認できるまでGPUを起動しません。GPUの手動スケーリング1は**リクエストがなくても課金**されます。発表後に `bash stop_server_gpu.sh` を必ず実行してください。
