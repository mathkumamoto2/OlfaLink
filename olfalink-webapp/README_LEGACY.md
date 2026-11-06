# OlfaLink Web Application — MVP v0.1.0

**目的:** 複数のセンサーから得られる反応を **0〜5の整数レベル**として表示し、将来 POM・AI モデル・LLM を組み合わせて「どのようなにおいか」を自然言語で説明するWebアプリの土台。

> **現時点のスコープ:** 画面・Web API・デモデータ・認証付き入力・Firestore対応・AI/LLM拡張口。**実デバイスの常時接続、検証済みAI、POM変換、LLMによる文章生成、クラウドへのデプロイはまだ行っていません。別途、実測セッション3件から作成した未検証の時系列テンプレートモデルを実験用APIに接続しています。**

## Architecture

```text
Browser (desktop / mobile)
    ├─ HTML / CSS / modular JavaScript
    └─ same-origin REST API (/api/v1)
           │
           ▼
     FastAPI on Cloud Run (single container)
           ├─ GET /dashboard        ← read measurement + status
           ├─ GET /readings         ← measurement history
           ├─ POST /readings        ← device key required
           ├─ ReadingStore         ← demo / Firestore
           └─ AnalysisPipeline     ← future
                ├─ OdorModel       ← future Sensor Encoder, POM, descriptors
                └─ OdorNarrator    ← future LLM explanation
```

Cloud Run は使い捨てインスタンスなので、`APP_MODE=demo` のメモリ履歴は永続化・共有されません。本番測定は Firestore を使用してください。

## Design decisions

- `intensity`: 0〜5の**整数**（6段階）。現段階ではセンサー反応の暫定指標であり、臭気指数・臭気濃度・安全性の指標ではありません。
- `channels`: 生センサー値を意味づけせず保持します。センサーの測定単位と校正条件を確定後に拡張します。
- Reactなどの外部JavaScript依存はなく、FastAPI + 単一コンテナでアプリとAPIを配信します。
- 将来、Web UIのみReact/TypeScriptに置換しても、`/api/v1` の契約は維持できます。
- AI/LLM は本番ダッシュボードでは無効です。実測セッションを使ったテンプレート照合モデルは、別の認証付き実験用 API にのみ接続しています。
- 推定値は出典・モデルバージョン・根拠・不確実性を一緒に扱うデータ構造を用意しています。
- UIは自動更新30秒、モバイル対応。デモデータは必ず「デモ」と表示します。

## Requirements

Python 3.12+ / Docker (Cloud Run deployment only)

## Run locally (Windows PowerShell)

```powershell
cd olfalink-webapp
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8080
```

Open <http://127.0.0.1:8080>. API docs: <http://127.0.0.1:8080/api/docs>.

Run tests:

```powershell
pytest -q
```

The same command works on Linux/macOS after activating `.venv/bin/activate`, using `python3 -m venv .venv`.

## API schema

- `GET /api/v1/healthz` — runtime health
- `GET /api/v1/dashboard` — latest 0-5 level, history, device state, AI/LLM availability flags
- `GET /api/v1/readings?limit=60` — history; limit 1..240
- `POST /api/v1/readings` — sensor ingestion; **disabled until `DEVICE_INGEST_KEY` is set**

Example device upload (**only when a secure device key is configured**):

```json
{
  "device_id": "olfalink-01",
  "timestamp": "2026-11-05T00:00:00+09:00",
  "intensity": 3,
  "channels": {
    "bme688_raw": 12450.0,
    "sgp41_raw": 15320.0
  }
}
```

The HTTP request must include the `X-Device-Key` header whose value matches `DEVICE_INGEST_KEY`. The example values are invented test data and are **not** validated sensor measurements. **No formula is provided here to map raw gas sensor values to the 0〜5 scale:** that mapping requires a defined baseline and experimental calibration.

## Deploy to Google Cloud Run (demo only)

Install Google Cloud CLI, authenticate, and select an existing Google Cloud project. From the project root:

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
gcloud run deploy olfalink-web --source . --region asia-northeast1 --allow-unauthenticated --set-env-vars APP_MODE=demo,AI_PROVIDER=disabled,LLM_PROVIDER=disabled
```

The command above creates a **public demo** containing **synthetic measurements only**. The `Dockerfile` handles all dependencies and starts Uvicorn on `0.0.0.0:${PORT}` as required by Cloud Run. Deployment may incur charges based on usage.

### Persistent measurements (Firestore)

1. Create a Firestore database in **Native mode** in the GCP project and choose the desired regional location.
2. Create/use a Cloud Run service account with the least privileges required for reading/writing the target collection (typically **Cloud Datastore User**, subject to your IAM policy).
3. Set `APP_MODE=firestore`, `FIRESTORE_COLLECTION=olfalink_readings` and `DEVICE_ID=olfalink-01`.
4. Store the ingestion key in **Secret Manager**, grant the Cloud Run service account permission to access the specific secret, and bind it with the `--set-secrets DEVICE_INGEST_KEY=SECRET_NAME:VERSION` option.
5. **Do not enable public access to a real-data deployment yet.** The current `GET` endpoints have no end-user authentication or tenant isolation. Use Cloud Run IAM for private testing and add end-user authorization (e.g., Firebase Auth + verified ID tokens) before exposing real measurements publicly.

Example private deployment structure (customize the service account and the secret name/version):

```bash
gcloud run deploy olfalink-web --source . --region asia-northeast1 --no-allow-unauthenticated --service-account=olfalink-runtime@YOUR_PROJECT_ID.iam.gserviceaccount.com --set-env-vars APP_MODE=firestore,FIRESTORE_COLLECTION=olfalink_readings,AI_PROVIDER=disabled,LLM_PROVIDER=disabled --set-secrets DEVICE_INGEST_KEY=olfalink-device-key:1
```

> For private Cloud Run services, access from a normal browser requires suitable identity/proxy handling. IAM-protected production deployment is **not yet** the finished public web authentication architecture.

## AI / LLM integration roadmap

**Current (`app/services/inference.py`):**

- `OdorModel` protocol: receives a list of sensor readings, returns validated `DescriptionEvidence`.
- `DescriptionEvidence`: description scores (0-1), uncertainty, model version, supporting measurement IDs.
- `OdorNarrator` protocol: receives only evidence (not a free-form prompt), returns a natural-language summary.
- `AnalysisPipeline`: orchestrates both, refuses to generate descriptions if evidence is missing.
- `DisabledAnalysis`: default. Returns an explicit unimplemented status. **No fake predictions on the public dashboard.**

**Next:**

1. Specify **precise 0-5 intensity calibration**, including sensor normalization, humidity/temperature compensation, baseline and reference labels.
2. Collect and version sensor time series with specimen metadata, hardware IDs, sampling intervals, environment conditions, and sensor units.
3. Implement and validate the encoder (e.g. 1D-CNN) against real labeled data; separate inference quality from language quality.
4. Add a measured-vs-predicted flag and model confidence/calibration, then POM/descriptor inference.
5. Implement an LLM adapter, ideally with strict structured evidence input, limited scope, output checks, and a refusal/uncertain state.
6. Add end-user authentication, tenancy/privacy controls, observability, retention policy and alerts **before** real public release.

## Key files

```text
app/main.py                 HTTP API, security headers, Cloud Run entrypoint
app/schemas.py              0-5 validation and future inference contracts
app/services/storage.py     demo and Firestore persistence adapters
app/services/inference.py   AI + LLM extension protocols
web/index.html              accessible responsive page templates
web/styles.css              web UI / desktop-mobile layouts
web/app.js                  navigation, chart, API rendering
web/api.js                  HTTP client
Dockerfile                  Cloud Run deployment image
tests/test_api.py           backend contract and security tests
```

## Current security limitations

- Public demo contains only synthetic data. **No user login exists yet.**
- Device ingestion uses one shared secret with constant-time comparison; it is appropriate for an internal prototype, not a fleet-scale credential strategy.
- Use TLS from Cloud Run, Secret Manager for API keys, IAM restricted roles, and an abuse/rate-limiting strategy for exposed endpoints.
- The 0-5 scale must never be used to judge the safety of a living environment, toxic gas presence, or gas leaks.

### Official references

- [Cloud Run container contract](https://docs.cloud.google.com/run/docs/container-contract)
- [Cloud Run stateless instances](https://docs.cloud.google.com/run/docs/overview/what-is-cloud-run)
- [Cloud Run secrets](https://docs.cloud.google.com/run/docs/configuring/services/secrets)


## 実験用：実測セッション全体によるにおい分類・強度ラベル参照（v0.2）

- 元データの **各CSVが1つの独立した時系列サンプル** です。CSV内の行や時間窓を独立学習例にはしません。
- 教師ラベルはCSVファイル末尾の `_0`/`_3`/`_4` から抽出しました。ラベル管理CSVは旧版で同梱しましたが、v0.5配布物からはプライバシー保護のため除外しています。
- センサーの7チャネルを `baseline → exposure → refresh` の区間ごとに正規化・時間軸圧縮し、**全セッションの時系列形状**を128×7の配列として保存します。
- 未知の1セッションが来ると、3件の参照テンプレートとの距離で最も近いものを返します。強度は近い参照セッションに付いている値（0,3,4）のみを返し、**強度1,2,5の推定器は存在しません**。
- 各におい・強度が1回ずつしかないため、**分類と強度を独立には学習していません**。検証精度は未測定です。「同じCSVを再入力したとき同じラベルを返す」ことは汎化の証明になりません。
- モデルファイルは `models/odor_session_templates.json`、学習記録は `models/training_report.json`。生センサーログと個人識別用フィールドは学習モデルに含めていません。

### 学習をやり直す

測定用ZIPはこのアプリZIPに含まれていません。元の測定ZIPを手元に配置して、次のように実行します。

```powershell
cd olfalink-webapp
pip install -r requirements-dev.txt
python -m ml.train --data "C:\path\to\smell_esp1_sessions.zip"
```

### CSV 1セッションまたはZIP全体を推論

```powershell
python -m ml.predict --model models/odor_session_templates.json --csv "C:\path\to\new_session.csv"
python -m ml.predict --model models/odor_session_templates.json --zip "C:\path\to\smell_esp1_sessions.zip"
```

### 実験用API（本番の表示には反映しない）

起動前に、シェル環境変数 `EXPERIMENTAL_ODOR_API_KEY` に高エントロピーな鍵を設定してください。
`POST /api/v1/experimental/odor/predict` に3つの配列 `baseline`, `exposure`, `refresh` を送ります。
各配列の各レコードには `bme_gas_ohm`, `tgs2602_adc`, `tgs2603_adc`, `sgp_voc`, `sgp_nox`, `bme_t`, `bme_rh` の数値7項目が必須です。
認証ヘッダーは `X-Odor-API-Key` です。鍵が未設定ならAPIは503で無効です。
返り値に `predicted_odor_class`, `predicted_intensity_0_5`, `nearest_template_distance`, `independent_validation=false` を含めています。`nearest_template_distance` は確率ではありません。

**安全上の注意：** この未検証の実験モデルを生活環境の安全判定や化学物質濃度測定に使用しないでください。
実用化には、同一においの異なる強度、異なる日の独立セッション、温湿度の変化、未知のにおい、異なるセンサー個体などを含む検証データが必要です。

---

## 2026-11-05 更新: 強度0〜5の暫定推定 v0.3

以前の `ml.predict` は「類似する測定セッションの **0/3/4** をそのまま返す」分類テンプレートです。今回追加した `ml.predict_intensity` は**それとは独立**した強度推定プロトタイプです。未測定の1/2/5も数値として出せます（ただし独立した正解データによる精度検証はできていません）。

### モデル設計

- **入力:** baseline → exposure → refresh を含む完全な1CSV。CSVの1行/一部の時間窓を別サンプルとは数えません。
- **前処理:** ガス5チャネルをbaseline末尾の中央値を基準として相対変化へ変換。センサーごと・区間ごとに時間ビンの中央値へ変換。各区間の時間順は保持されます。
- **特徴:** exposure区間の持続的な相対応答の大きさ（対数比、中央値と75パーセンタイルの組合せ）。強度を曝露中の反応と定義するためrefreshは回復診断に使います。
- **校正:** 各CSV名の末尾を強度教師ラベルとして、応答スコアに単調な区分線形補間/外挿を当てます。学習セッションは室内0・靴3・酢4の**計3件**です。
- **出力:** `predicted_intensity_0_5`（0〜5整数）, `predicted_intensity_continuous`（連続値）, `sensor_response_score`（無次元の暫定スコア）, `outside_calibrated_response_range`（外挿のフラグ）, 診断値。
- **限界:** においの種類と強度ラベルが交絡しています。未知のにおい・強度1/2/5・個体差・環境差の正確性を確認した結果ではありません。「AIモデルの高い精度」「人間の嗅覚強度」「臭気濃度」「安全性」を意味しません。強度1/2は単調補間、5は外挿なので**現時点の数値は試験表示**です。

### 1. 再学習

Windows PowerShell:

```powershell
cd olfalink-webapp
pip install -r requirements-dev.txt
python -m ml.train_intensity --data 'C:\path\to\smell_esp1_sessions.zip'
```

再学習により `models/intensity_calibration.json` と `models/intensity_training_report.json` が生成されます。実行中のAPIがモデルをキャッシュしているため、再学習後はサーバーを再起動してください。

### 2. CSVを1件推論

```powershell
python -m ml.predict_intensity --csv 'C:\path\to\new_session.csv'
```

学習した元データの3CSVを入力すると、0/3/4に対応することを確認できます。ただし**訓練データの再入力であり精度評価ではありません**。

### 3. Web画面からCSVを推論

シェルでAPIキーを設定してから起動します（例の値は必ず置き換えてください）。

```powershell
$env:EXPERIMENTAL_ODOR_API_KEY = 'replace-with-a-long-random-secret'
uvicorn app.main:app --host 127.0.0.1 --port 8080 --reload
```

<http://127.0.0.1:8080/#analysis> を開き、実測CSVとAPIキーを入力して「強度を推定する」を押してください。リクエスト側でAPIキーを保存しません。データは推論APIへ転送されますが、モデルはファイルを永続保存しません。**プライベート環境で実施してください。**

生CSVをPOSTするAPI：`POST /api/v1/experimental/odor/intensity/predict-csv`。ヘッダーは `X-Odor-API-Key: <secret>`、本文はCSVファイル全体（上限2MB）。

3区間のJSONを渡すAPI：`POST /api/v1/experimental/odor/intensity/predict`。既存の `POST /api/v1/experimental/odor/predict` は後方互換で、強度フィールドだけ新モデルの値へ差し替えます。両方とも認証必須です。

通常のダッシュボードに表示する「デモの強度」と、この実験用の強度推定は別物です。既存のダッシュボード値を学習済み結果に見せかけて置換していません。

### 4. 本当に精度を評価するためのデータ

同じにおい（例：酢）で強度0〜5を独立に複数セッション収集し、濃度・温湿度・測定日・センサー個体を記録してください。独立したセッション単位で訓練と検証を分け、MAE（絶対誤差平均）と「±1段階以内の割合」を報告します。現時点の **検証MAEは未算出** です。

### 5. CSVを追加して再校正する場合

`COFFEE_<session-id>_2.csv` のように末尾 `_0`〜`_5` の形式で人間が付けた強度を記録すれば、未知のにおい名でも**強度校正用**に読み込めます。におい名の文字列は強度計算には使用しません。校正データ全体で「強度が大きいほど応答スコアも大きい」という条件が破れた場合は、モデルを無理に当てはめず学習を中断します。センサーがにおいの種類ごとに異なる応答をするため、この中断は重要な警告です。

`MAX_CSV_BYTES` はHTTPアップロード1件2MB上限、学習時ZIP内CSVの合計は100MB上限です。測定生データそのものは配布用アプリZIPには含めていません。モデルは計測時の氏名・MACアドレス・所在地を特徴量として取り込みません。

---

# OlfaLink 時系列特徴量抽出 v0.4

## 目的

実際の `baseline → exposure → refresh` を含む **1つのCSVを独立した1セッション** として扱い、機械学習用の記述特徴量を作ります。CSV内の約1,000点を別々の教師データと数えることはありません。

### 出力データ

- `derived_features/features_full.csv`: 3セッション × **115特徴量**（内訳は7チャネル×16個＋3つの区間時間）。`sample_id`, `odor_code`, `intensity_0_5` は特徴量数に含めません。
- `derived_features/features_core.csv`: 3セッション × **27個の暫定候補特徴量**。将来の学習の際の候補であり、現時点でその29個の性能が実証されているわけではありません。
- `derived_features/feature_dictionary.csv`: 特徴量名、説明、候補選択の有無。
- `derived_features/feature_summary.json`: 独立セッション数と強度ラベル。
- `extract_features.py`: 再現可能な特徴量抽出スクリプト。
- `tests/`: 抽出手順のユニットテスト。

## 実行手順

Python 3.10以上を使用します。

```powershell
pip install -r requirements-features.txt
python extract_features.py --input "C:\path\to\smell_esp1_sessions.zip" --out-dir "derived_features"
python -m unittest discover -s tests -v
```

統合版（`OlfaLink_Intensity_Features_v0.4.zip`）では、アプリの `olfalink-webapp` ディレクトリで次のコマンドを使います。

```powershell
pip install -r requirements-features.txt
python -m ml.export_features --input "C:\path\to\smell_esp1_sessions.zip" --out-dir "derived_features"
```

## 数値の意味

各チャネルの前処理:

1. phaseを分け、タイムスタンプ単調増加と必要点数を検証する。
2. 明らかに不正なセンサー値・欠損値は **同一phase内のみ** 補間する。各phaseで有効率80%未満なら停止する。
3. baselineの**最後の最大60点**の中央値を基準値 $b$ とする。
4. ガス5チャネルでは、変化率 $r(t)=(x(t)-b)/|b|$ を求め、範囲制限後 $z(t)=\log(1+r(t))$ に変換する。
5. 温度と相対湿度は $z(t)=x(t)-b$ とする。温度は°C、相対湿度はパーセンテージポイントに相当する差分である。
6. ガス・温湿度のそれぞれに、baselineの変動、曝露時のピーク・中央値・AUC・傾き・到達時間、refresh残留量を計算する。

AUCの特徴量は **時間で割った面積** として計算し、20分の曝露が10分の曝露より単純に2倍の値になることを抑えます。ただし、異なる曝露時間による応答形状の変化を完全には排除できません。

`exposure_peak_abs`などの値は、原センサー値そのものではなく、**baselineからの変化を変換した後の無次元数（温湿度は差分単位）**です。

## 注意（研究上の制限）

- **3セッションのみ**（室内0／靴3／酢4）。特徴量が115個になっても独立サンプル数は3です。現段階で115次元の教師あり回帰モデルを学習・検証しても汎化性能を証明できません。
- 強度ラベルとにおいの種類が完全に交絡しています。別のにおい・未測定の強度1/2/5に一般化するかは不明です。
- 酢では曝露中の湿度が大きく変化しています。濃度だけではなく、湿度・温度・センサードリフトを含む複合的なセンサー反応が特徴に入ります。
- 測定の曝露・回復の時間はセッションごとに異なります。`duration_*`を説明変数に使うと実験プロトコルを学習してしまう可能性があるため、原則として監査・品質管理指標として使用し、独立測定で評価するまで予測モデルに投入しないでください。
- ここで追加されたのは **特徴量の算出** のみです。既存の`ml.train_intensity`の校正式やWebの推定結果は変えていません。モデル改善は独立セッション収集後に、セッション単位の検証で判断してください。
- ガスチャネルのADC・生値は物理的な濃度単位ではありません。製造元の校正や環境補償なしに臭気濃度や健康安全判定には利用できません。
- 末尾 `_0.csv`〜`_5.csv` のファイル名を **セッション単位の教師強度** と読み取ります。ラベルが正しく収集されたことは別途確認してください。
- 氏名、MACアドレス、住所、正確な時刻は出力に含めず、匿名の `session_001` などに置き換えています。

## 今後の学習計画

同じにおいについて、強度0〜5を異なる日・測定条件で独立に複数回取得します。測定日や同じ試料からの繰り返しをグループ化して評価します。最初は、固定の少数特徴量による回帰・分類をベースラインとして、十分なサンプルがそろってから1D-CNN・TCNと比較します。0〜5は順序尺度なので、MAEだけでなく±1段階以内の割合や順序回帰も検討できます。

### リアルタイム判定との違い

本スクリプトは完整セッションの `refresh` 終了後の**事後解析**です。曝露の最中に結果を出す場合は、未来の `refresh` 特徴量を入力しない別モデルを作る必要があります。
