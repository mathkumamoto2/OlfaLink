# OlfaLink v1.4 — LED / stage 非依存・ボタン式30秒推論

## 要点

`GET /data` の返却行に `led=1`, `led=0`, `stage=unknown` などが含まれても、**入力フェーズの解釈には一切利用しません**。Cloud Runがボタンを押したときだけAWSへ直接アクセスし、センサー時系列と `epoch_ms` で入力を組み立てます。

- 最新30秒：におい反応を推定する対象
- その直前60秒：実際のセンサー値に基づく仮基準
- 合計約90秒の連続データが必要。AWSから120秒を取得。
- 入力のセンサー7チャネルは従来と同じ。
- 実際の値を捏造せず、欠損や異常値が多い場合はエラーとして扱う。
- WebのUI、AWS APIキーのSecret Manager設定、LLM専用GPUサービスは変更しない。

**注意：これは「外部LEDラベルへの依存をなくした暫定推論」です。CNN v0.7は学習時にフェーズマスクを使用しており、前処理が内部で固定したマスクを作成します。モデル自体のフェーズ非依存化と信頼できる精度評価には、フェーズを使わない30秒窓で再学習・評価が必要です。**

基準区間にすでににおいが存在する場合、その差分では異常を検出できないことがあります。実際のAWS `/data` 応答は未検証です。

## Cloud Shell（GPUは起動しません）

1. `OlfaLink_PhaseAgnostic_v1.4.zip` をCloud Shellのホームディレクトリへアップロード。
2. 次のコマンドを実行：

```bash
cd ~/OlfaLink
unzip -o -q ~/OlfaLink_PhaseAgnostic_v1.4.zip -d .
cd olfalink-webapp
bash deploy_aws_data_pull.sh
```

アップロードの確認：

```bash
ls -lh ~/OlfaLink_PhaseAgnostic_v1.4.zip
```

Cloud Runに切り替わっても別サービス `olfalink-llm` は変更・起動しません。GPU停止中はLLMによる説明は利用できません。AWS側の正解においラベルも推論には使いません。

## 変更点

- `app/services/aws_live_data.py`：AWSの `led` / `stage` を無視。どんな値でも内部の `ambient` 扱い。
- `app/services/on_demand_inference.py`：常に `select_ambient_30s` を使用。旧キャッシュを無効化する版番号変更。
- `app/main.py`：アプリのバージョンを1.4に変更。
- `tests/test_aws_data_pull.py`：数値LED、未知フェーズ、ラベル不在、実CNN推論テスト。
