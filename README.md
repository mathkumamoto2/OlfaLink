# OlfaLink — GitHub公開用ソース（v1.9.2）

生活環境のにおいを扱う研究用プロトタイプ。**解析ボタンを押した時だけ**、AWS `/data` API（設定時）の時系列データを取得し、既存のCPU版1D-CNNでにおいの種類・強度（0–5）を推定します。Qwen3.5-2B + LoRAによる説明生成は**オプション**で、利用不可の場合は日本語の説明辞書にフォールバックします。解析中は「判定中です」を表示します。

## ディレクトリ

- `olfalink-webapp/` — FastAPI、UI、AWS JSON変換、CNNの推論と補正、合成デモ、ローカル起動、テスト。
- `olfalink-webapp/models/odor_intensity_multitask_v0_7.pt` — 既存の実験用1D-CNNの重み（小型）。
- `llm-service/` — 非公開Cloud Run GPUサービス用Qwen + LoRA推論コード。**LoRA重みは同梱していません**。

## 現在の処理

1. Web画面で「においを解析する」をクリック。`POST /api/v1/inference/on-demand` を実行。
2. AWSの実測データ（直近120秒の取得）を優先。取得できない、または入力条件を満たさない場合は、デモ設定が有効なら**明示した合成センサー時系列**に切り替える。
3. 時刻による直近90秒の3分割（`baseline` / `exposure` / `refresh` 相当）を使用。**実際の曝露イベントを推定できたわけではない**。
4. TGS2602・TGS2603のADCが負の場合、発表用の暫定措置としてCNN入力前に0へ補正。補正は画面とAPIに表示され、AWSの元データは変更しない。
5. 1D-CNNで種類・強度を推定。LLMが許可・利用可能な場合にのみ説明生成を試行し、失敗時・不適切な入力時には辞書説明へ切り替える。

### 研究上の注意

- 1D-CNNの推定精度は**未検証**。学習時と現在の3区間入力は異なります。
- 負ADCを0へ補正すると当該センサーの情報は失われ、精度はさらに低下し得ます。
- `synthetic_demo` の結果は**実測ではありません**。ラベルや強度を現実のにおいの証拠として利用しないでください。
- 濃度、人体への有害性、ガス漏れ等の安全判断には使えません。
- 現行の公開Web推論APIにはユーザー認証・厳格なレート制限がありません。GPUを起動可能にする場合、第三者が課金対象の推論を呼び出すリスクがあります。

## Windowsローカルで実行

Python 3.11/3.12（64bit）のあるWindowsで `olfalink-webapp/Launch_Local.bat` を実行します。初回はPythonパッケージをインストールし、起動後 `http://127.0.0.1:8765` を開きます。

- ローカルではCPU 1D-CNN＋説明辞書を使用。GPUなし。
- AWS認証なしでも合成デモで動作（初回パッケージ導入時はネット接続が必要）。
- AWSを使う場合は `config.env.example` を `config.env` に複製してキーを設定。`config.env` はGit管理対象外です。

## Google Cloudへ再デプロイ（任意・課金に注意）

**GitHubへのアップロードだけではデプロイされません。** また、停止・課金無効化済みのGoogle Cloudプロジェクトを勝手に再開しないでください。

Google Cloud Shellでクローンした後、AWS読み取りキーがSecret Manager `olfalink-aws-read-key` に登録されている場合に限り、東京のWebアプリを次のようにデプロイできます。

```bash
cd ~/OlfaLink/olfalink-webapp
bash deploy_aws_data_pull.sh
```

これは**Web+CPU CNN**の更新です。LLM GPUサービスの再構築には `llm-service/adapter/` に別途LoRA重みが必要です。公開GitHubにはその重みを置かないでください。GPUの起動やCloud Runビルドには費用が発生する可能性があります。**`olfalink-webapp/start_server_with_gpu.sh` はGPUを1台に固定する発表用スクリプトなので、費用を発生させたくない場合は実行しないでください。**

> 既存の公開Webアプリがオフラインでも、GitHubへのプッシュによって自動的にクラウドが起動することは通常ありません。ただし、Cloud Build/GitHub Actions等の自動デプロイ設定がある場合は別です。

## テスト

```bash
cd olfalink-webapp
python -m pip install -r requirements-dev.txt
python -m pip install --index-url https://download.pytorch.org/whl/cpu 'torch>=2.4,<3'
python -m pytest -q
```

LLMのAPI契約テスト（実際のGPU推論ではありません。非公開LoRA重みの検査1件は公開ソースではスキップ）:

```bash
cd llm-service
python -m pytest -q
```

## 公開前のチェック

- `.env`、`config.env`、AWSキー、個人の認証情報はコミットしない。
- `llm-service/adapter/`、HFからダウンロードした巨大なベースモデル、測定した実機の生データはコミットしない。
- `git diff --cached --check` と `git diff --cached --name-only` で公開対象を確認する。
- この配布物はv1.9.2のWebコード＋v1.9系LLMサービスソースを組み合わせたもので、**ユーザーのCloud Run本番環境への反映は行っていません**。
