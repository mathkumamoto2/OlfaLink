# OlfaLink v1.9.2 — AWS負のADC値を0に補正する暫定版

## 目的

AWS `/data` のTGS2602やTGS2603にマイナスのADC値が含まれる場合に、
**そのADCだけ `max(0, raw_adc)` としてCNN推論を継続**する。
センサーの生データはAWS上で変更しない。ローカル/サーバーの共通推論ロジックを使用。

## 変更点

- `app/services/aws_live_data.py`: AWS JSONの取り込み時、TGS2602/2603の**有限な負のADCのみ**0に補正。欠損値、非数、その他のチャンネルは従来の厳密な検証を維持する。
- `app/services/live_stream.py`: `baseline`相当区間のTGS ADC基準値が0なら、相対変化を計算しない。そのADCチャンネルだけ正規化系列を全て0とする。0割りを避け、データがない他のセンサーまで捏造しない。
- `app/services/on_demand_inference.py`: 補正件数を `analysis.evidence.uncertainty` に含め、`features.sensor_correction_applied=true` を返す。旧モデルの解析キャッシュはv1.9.2として更新。
- `web/index.html` / `web/app.js` / `web/styles.css`: 補正が適用された場合に「センサー値を補正した暫定結果」と警告表示。通常の実測と合成デモを引き続き区別。
- `/api/v1/healthz` は `1.9.2`。

## サーバーへの反映

1. `OlfaLink_v1.9.2_Server_ADC_Zero_Clamp.zip` をCloud Shellにアップロード。
2. 以下を実行する。

```bash
set -e
mkdir -p ~/OlfaLink
cd ~/OlfaLink
unzip -o -q ~/OlfaLink_v1.9.2_Server_ADC_Zero_Clamp.zip -d .
cd olfalink-webapp
bash deploy_aws_data_pull.sh
curl -fsS https://olfalink-15647954758.asia-northeast1.run.app/api/v1/healthz
```

このスクリプトは東京のWeb/CNNサービスのみを更新し、GPUサービスをデプロイ・起動しない。既存の `OLFALINK_LLM_ENABLED` を変更しない。
GPUがオートスケール可能かつLLM設定が有効な場合は、解析ボタンでGPUが起動する可能性がある。
発表用にGPUを使わず実測CNNだけを確認するなら `OLFALINK_LLM_ENABLED=false` をCloud Runで設定してから解析する。

## ローカル版

ローカル版ZIPを展開して `olfalink-webapp/Launch_Local.bat` を実行（初回はPythonと依存ライブラリのインストールが必要）。
アクセス先は `http://127.0.0.1:8765`。ローカルではGPU/LLMを使用せず、CNN＋辞書で説明。
AWSを利用する場合は `config.env.example` を `config.env` に複製し、ローカルPC上だけで読み取りキーを設定する。GitHubに公開しない。

## 重要な限界

**これは発表用のソフトウェア暫定対処で、センサーの校正やCNNモデルの検証ではありません。**
負の測定値を全件0にすると、TGS2602の信号情報は失われる。
7チャンネルで学習したCNNに実質6チャンネル相当の信号を入力しているので、分類ラベル・強度・LLM説明の正確性は保証されない。
`baseline/exposure/refresh` はLED非依存の90秒機械的分割で、実際の曝露を意味しない。
別チャンネルが欠損していた場合は引き続き409、もしくは設定により明示的な合成デモに切り替える。
ガス漏れ・健康被害・安全性判断に使用してはいけない。

## 検証

`python -m pytest -q` で94件のテスト成功。負のTGS2602（-162〜-8を模擬）→0補正→実際の学習済みCNNで推論成功を検証。
実AWSでのCloud Runデプロイ、Windows実機、GPU/LLM実推論は未検証。
