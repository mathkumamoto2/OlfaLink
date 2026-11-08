# OlfaLink Web v1.9.2

FastAPI + UI + AWS `/data` の実測値/合成デモ + 1D-CNN + 辞書/LLM説明。

最初に [リポジトリ直下のREADME](../README.md) を参照してください。
実機JSONのネスト形式、負ADCの0補正と限界については [README_ADC_CLAMP_v1.9.2.md](README_ADC_CLAMP_v1.9.2.md) に記載しています。

- `POST /api/v1/inference/on-demand`：ボタンからの推論（AWS取得を含む）
- `GET /api/v1/dashboard`：直近の結果表示（インスタンスメモリ内）
- `GET /api/v1/healthz`：サービス起動確認
- `GET /api/v1/integration/status`：設定状態（実測推論成功の証明ではない）

公開APIに認証がない点、実測／合成デモの区別、GPU利用料金に注意してください。
