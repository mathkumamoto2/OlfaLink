# OlfaLink LLM（オプション / GPU）

Qwen3.5-2B + LoRAによる日本語説明生成サービス。Web側が推定したラベル、強度、未校正スコアと `demo_mode` を受け取る設計です。**生センサー時系列を直接LLMへ入力するモデルではありません。**

- `service.py`：FastAPI の `/generate` 等
- `src/`：LoRA推論ロジック
- `Dockerfile`：Qwenベースモデルをビルド時に取得し、ローカルの `adapter/` をコピー

## 重みについて

公開GitHubには**ファインチューニング済みLoRAアダプターは含めません**。デプロイする場合のみ、自分で管理する重みファイルを `llm-service/adapter/adapter_model.safetensors` 等へ配置します。必要なベースモデルは `Dockerfile` に指定されています。`adapter/` は `.gitignore` で除外しています。

GPUサービスの実行・再デプロイには課金が発生する可能性があります。停止・課金無効化中はそのまま維持してください。`min-instances=0` は即時無課金を意味しません。

Webアプリに対してCloud Runサービス間認証（OIDC + `roles/run.invoker`）が必要です。公開解析エンドポイントの認証・レート制限も、本番運用前に実装してください。

このソースはGPU上の実モデル推論が成功したことを保証するものではありません。詳細は [ルートREADME](../README.md) を参照してください。
