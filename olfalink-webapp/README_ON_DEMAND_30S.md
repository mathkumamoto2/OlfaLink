# OlfaLink v1.1 — ボタン式30秒推論

## このバージョンで実装済み

- 植物モチーフの既存Web UIを維持し、「においを解析する」ボタンを追加
- **画面の5秒間隔ポーリングと自動推論を廃止**。通常表示は保存済みの解析結果のみ取得
- センサーの実測値を `POST /api/v1/live/ingest` へ送る認証付き受信口
- Firestoreへの永続保存。Cloud Runのスケールゼロ/再起動でも最新の記録が残る
- `POST /api/v1/inference/on-demand` の実行時のみ1D-CNN v0.7で暫定的ににおい7分類＋強度0〜5を予測
- 説明辞書による日本語説明。別途非公開LLM推論サービス `LLM_SERVICE_URL` を接続すればLoRAの説明へ切り替え可能
- 30秒以内の連打は原則キャッシュ結果を再利用（単一インスタンス、concurrency=1）

## 厳密な制限

1. **これだけではAWSのライブ転送は始まりません。** 既存AWS受信Lambdaの保存処理の直後で `bridge/aws_lambda_forwarder.py` を呼ぶか、ESP32側から同じJSONを送る必要があります。既存の `/publish` と `/publish/dataset.xlsx` は5分キャッシュなのでリアルタイム用途には使いません。
2. **30秒専用に再学習していません。** CNN v0.7は全セッション学習済みで、ここでは不足している回復相をマスクし、30秒曝露データを入力した暫定推定です。分類・強度とも精度は未確認。安全性判定ではありません。
3. **ベースラインが必要です。** `led=baseline`で基準測定、`led=exposure`で少なくとも30秒の曝露を送るか、連続観測なら `led=ambient` として最低約90秒分を送ってください。後者は実測の前60秒を基準とみなし、最後30秒を対象とする仮の相分けで、分類の妥当性は未検証です。
4. **LoRAはまだCloud Runに常駐していません。** `LLM_SERVICE_URL`が未設定の場合は辞書による自然言語説明を使用します。推論はボタン操作時だけ。Qwen3.5-2B LoRAアダプター単体では実行できず、ベースモデルと別途GPU推論サービスが必要です。
5. **一般公開URL上で誰でも解析ボタンを押せます。** `--max-instances=1`と30秒結果キャッシュは費用の緩和策ですが、利用者認証の代わりではありません。研究チーム以外の公開にはIAP等の認証追加を推奨します。
6. Firestoreに各測定サンプルを書き込みます。書き込み数による料金が発生する可能性があるため、センサーからクラウドへの送信間隔は測定要件の範囲内で抑制し、アラートを設定してください。Firestoreのドキュメントは自動削除されません。長期運用ではTTL等の保持期間設定も必要です。

## GitHubへ適用（PowerShell）

このZIPに含まれる `olfalink-webapp/` の中身を、既存のGitリポジトリ `OlfaLink/olfalink-webapp/` へ上書きします。**`.git` フォルダは触らないでください。**

```powershell
cd C:\Users\KH\Downloads\OlfaLink
git add olfalink-webapp
git diff --cached --check
git commit -m "Add on-demand 30-second CNN inference"
git push origin main
```

## Cloud Runへデプロイ（Cloud Shell）

```bash
cd ~/OlfaLink
git pull --ff-only origin main
cd olfalink-webapp
bash deploy_on_demand.sh
```

スクリプトはFirestoreの `(default)` データベースがなければ東京リージョンに作成し、Cloud Runのサービスアカウントに `roles/datastore.user` を付与します。既存のAWS読み取りシークレットは変更せず、書き込み用の専用シークレット `olfalink-live-ingest-key` がなければ作成します。

**Google Cloudに請求が発生し得ます。** CPU常時起動を避けるため `--min-instances=0`、過剰なインスタンス増加を防ぐため `--max-instances=1` を指定しています。

## AWS受信Lambdaからライブ転送（必須）

`bridge/aws_lambda_forwarder.py` をAWS受信Lambdaに追加し、**AWSが既存処理で測定を保存できた後**に呼びます。

```python
from aws_lambda_forwarder import forward_measurements

# 既存のAWS保存処理に成功した後：
forward_measurements(real_esp32_payload)
```

AWS Lambdaの設定:
- `OLFALINK_CLOUDRUN_URL`: Cloud RunサービスURL `https://olfalink-15647954758.asia-northeast1.run.app`
- `LIVE_INGEST_KEY`: **Google Secret Managerの `olfalink-live-ingest-key` の値**。チャットやGitHubに書かないこと。可能ならAWS Secrets Managerからロードしてください。

ESP32から受信した `real_esp32_payload` は、少なくとも `epoch_ms`、`led`（`baseline`/`exposure`/`refresh`/`ambient`）とセンサー値を含む必要があります。AWSの受信JSON形式が異なる場合は、既存Lambda側の変換コードに合わせてフィールド名をマッピングします。AWS側の既存Lambdaコードが提供されていないため、その変更はここでは実施できません。

## API確認

```
GET  /api/v1/healthz                サーバー稼働
GET  /api/v1/live/status            Firestoreの最新測定時刻と接続状態
POST /api/v1/live/ingest            AWS側からのみ。x-device-keyが必須
GET  /api/v1/dashboard              前回の解析結果。**推論しません**
POST /api/v1/inference/on-demand    ボタンを押したときだけCNN/任意LLM推論
GET  /api/v1/integration/status     AWS履歴APIの診断（必要時のみ呼ぶ）
```

AWS Lambdaからの転送を開始して数分後、`/api/v1/live/status` が `"live_feed_connected":true` になれば受信中です。`phase` が `exposure` で30秒以上の測定値と事前baselineがあるか、`ambient`で約90秒連続データがある状態で「においを解析する」を押してください。

## ローカル検証

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```

`tests/test_on_demand.py` は記録済みの合成測定サンプルを使ってAPIと本物のCNN重みを確認します。AWSやESP32の実機から連続的に転送したエンドツーエンド実証は未実施です。
