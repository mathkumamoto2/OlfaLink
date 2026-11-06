# OlfaLink v0.7 — 1D-CNN：におい分類＋強度推定

OlfaLink の **研究用** マルチタスクモデルです。

- 入力：**1セッションの全時系列CSV（23列または40列形式）**
- 推論①：におい7分類 → **LLMへ渡す分類ラベル**
- 推論②：強度0〜5の連続値＋整数値 → **UIへ直接表示**
- LLMによる説明生成そのものは**まだ接続していません**。APIの `llm_input` は次の接続のために出力します。
- ESP32からの自動逐次推論・リアルタイム監視ではなく、Webの「AI解析」からの**セッションCSV手動アップロード**に対応します。

## 実行例（Windows PowerShell）

```powershell
cd olfalink-webapp
python -m pip install -r requirements-ml.txt
python -m ml.predict_cnn_multitask --csv "C:\\path\\to\\session.csv"

$env:EXPERIMENTAL_ODOR_API_KEY = python -c "import secrets; print(secrets.token_urlsafe(32))"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

ブラウザー：`http://127.0.0.1:8080/#analysis`。モデルに「1D-CNN v0.7」を選択してアップロードしてください。

API：`POST /api/v1/experimental/odor/multitask/cnn/predict-csv` にCSVバイトを送信し、ヘッダー `X-Odor-API-Key` に秘密キーを付与します。2 MBまで。認証が未設定なら503を返します。CSVの内容を保存する実装ではありません。

## 推論結果の項目

- `predicted_odor_label` / `predicted_odor_ja`：分類ヘッドの予測
- `predicted_intensity_0_5`：UI用整数0〜5
- `predicted_intensity_continuous`：回帰ヘッドの予測を0〜5に制限した値
- `classification_score_uncalibrated`：**未校正**の分類スコア。信頼度や的中確率ではない
- `llm_input`：一般的な説明生成に使用する**予測ラベルのみ**の入力オブジェクト。強度はLLMに推定させない
- `warning_ja`：結果の不確実性

## 再学習

合成データZIP `OlfaLink_Synthetic_TimeSeries_1100_20261106.zip` を別途用意します（ZIPには含めません）。

```powershell
python -m ml.train_cnn_multitask --data "C:\\path\\to\\OlfaLink_Synthetic_TimeSeries_1100_20261106.zip" --diagnostic-epochs 32 --final-epochs 23
```

- 元セッション9件の合成データ869件で最終モデルを学習。
- 100件は強度ラベルなし、100件は曝露なし、31件は品質不合格として除外。
- 診断モデルでは **ROOM_R02 と CH3COOH_Z05 に由来する合成200件** を丸ごと学習から除外。
- 診断の分類正解率は0%、強度MAEは1.5888。未知の測定への性能は不足しています。
- 最終モデルの学習データに対する分類正解率100%は**テスト精度ではありません**。
- 強度1の教師データなし。種類と強度の交絡あり。7クラス以外のにおいを「未知」と識別する機能なし。

詳細は [MODEL_CARD_MULTITASK.md](MODEL_CARD_MULTITASK.md) を参照。

## 安全上の注意

このモデルは研究用プロトタイプです。実生活の悪臭判定、衛生・毒性・ガス漏れ・空気の安全性の判定などには利用しないでください。LLM側は分類結果を確定した化学成分のように扱わないでください。
