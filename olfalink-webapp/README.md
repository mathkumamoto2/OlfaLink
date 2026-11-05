# OlfaLink Web App — v0.4

OlfaLink は、におい強度（0〜5）とにおいの自然言語説明を表示するレスポンシブWebアプリです。植物をモチーフとしたUIを維持し、本番画面からデモ用の説明文・表示例を削除しています。

## 起動（Windows CMD）

```bat
py -3 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install -r requirements-dev.txt
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

ブラウザで http://127.0.0.1:8000 を開いてください。

## 表示の仕様

- **接続待ち：** におい強度は `— / 5`。説明欄は「測定結果を待っています。」
- **実測中：** 接続済みデバイスの新しい実測値のみ `0〜5` で表示。レベル変換の校正式は別途検証が必要。
- **解析結果あり：** `analysis.status = ready` かつ該当測定の根拠IDが付いた結果のみ表示。
- **解析結果なし：** AIが未実装または分析不能なときは文章を生成したふりをせず、「においの特徴を解析できません。」
- **APP_MODE=demo：** バックエンドテスト用にサンプル値を保有しますが、通常のWeb UIには表示しません。

## テスト

```bat
python -m pytest -q
```

## アーキテクチャ

- `web/`：レスポンシブUI、HTML/CSS/JavaScript
- `app/main.py`：FastAPI、REST API、Cloud Runでの静的ファイル配信
- `app/services/storage.py`：メモリ保存デモとFirestore保存
- `app/services/inference.py`：将来のSensor Encoder、POM、LLM連携用インターフェース
- `Dockerfile`：Cloud Run用コンテナ

### Cloud Run

Cloud Runでは `0.0.0.0:${PORT}` で起動します。Firestore保存の本番運用には `APP_MODE=firestore`、Firestoreの作成、適切なサービスアカウント権限、`DEVICE_INGEST_KEY` のシークレット設定が必要です。**利用者認証はまだ実装されていないため、実測データを匿名アクセス可能なCloud Runサービスとして公開しないでください。**

本番向けの最終実装には、センサー値の校正、ユーザー認証と権限分離、AI推論モデルとLLM統合、運用監視が必要です。現在のバージョンはデザインを本番用に整えた段階であり、すべての本番機能を完成させたものではありません。
