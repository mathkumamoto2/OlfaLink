# OlfaLink — 1D-CNNによるにおい強度推定（v0.6、研究用）

**学習済みモデルがあります。ただし、新しい測定への精度はまだ低く、実運用には使わないでください。**

## 入出力

- 入力：1回の測定CSV全体。**CSVの各行は独立サンプルではありません。**
- 対応：旧40列ESP1形式（`led`, `bme_gas_ohm`, `sgp_voc`等）と新23列ESP1形式（`stage`, `bme_gas_kohm`, `sgp_voc_raw`等）。
- 出力：強度0〜5の連続推定値と整数（0.5以上を切り上げ）。
- 共通の7チャネル：BMEガス抵抗(kΩ)、TGS2602 ADC、TGS2603 ADC、SGP VOC raw、SGP NOx raw、温度、相対湿度。
- 前処理：各チャネルの曝露前ベースラインに対する相対変化／差 → 各区間を24+64+40ビンに圧縮 → 学習データから算出したチャネルスケールで正規化。
- 3つのマスクチャネルを追加（計10チャネル）。生ごみは元ログの順序が `baseline → refresh → exposure` であり、前半の `refresh` は曝露前参照として扱い、**曝露後回復を未測定（マスク0）**とします。

## 生成データからの教師あり学習

配布済み `OlfaLink_Synthetic_TimeSeries_1100_20261106.zip` を入力として、次のように処理しました。

| 項目 | 件数 |
|---|---:|
| 合成CSV総数 | 1,100 |
| ラベル未指定（酢）を除外 | 100 |
| 曝露なし（部屋①）を除外 | 100 |
| 物理的に不自然な合成波形を除外 | 31 |
| **学習に採用した合成CSV** | **869** |
| 元の独立した実測セッション | **9** |

学習ラベルは0、2、3、4、5。**強度1の教師データはありません。** 合成ノイズで強度ラベルそのものは変えていません。

## 正しく分けた診断

未知の元セッション3件（室内0、靴3、生ごみ4）とそこから生成された300件は、診断モデルの学習から完全に除外しました。実測3件への診断モデルの推定値は **1.663、1.587、0.743** で、正解は **0、3、4**。連続値MAEは **2.1108**、丸め後3件すべて不正解でした。

これは厳しい結果ですが、データ漏洩を避けた評価です。元の測定セッションはわずか9件であり、においの種類・収集条件・強度が交絡しています。この数字自体も3測定に対する小規模な診断で、将来の精度を推定するにはサンプル数が足りません。

配布する最終モデルは品質チェック後の**869件すべて**で再学習しています。学習データ上の丸め後100%一致は、未知測定への正解率ではありません。

## Windowsで推論（PowerShell）

```powershell
cd olfalink-webapp
python -m pip install -r requirements-ml.txt
python -m ml.predict_cnn_intensity --csv "tests/fixtures/FOOD_WASTE_F01_intensity_4_SYN_001.csv"
```

手元の実測CSVでも `--csv` にパスを指定すれば推論できます。入力は最大2MB、3つの測定区間（ただし生ごみのような曝露前refreshを含む例外は可）が必要です。

## WebアプリでCSVをアップロード

```powershell
$env:EXPERIMENTAL_ODOR_API_KEY = python -c "import secrets; print(secrets.token_urlsafe(32))"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

`http://127.0.0.1:8080/#analysis` を開き、推論方式で「1D-CNN」を選択します。研究用API：
`POST /api/v1/experimental/odor/intensity/cnn/predict-csv`、`X-Odor-API-Key`ヘッダー必須。外部公開には別途認証・監視・レート制限が必要です。

## 自分で再学習する

```powershell
python -m ml.train_cnn_intensity_v06 --data "C:\path\to\OlfaLink_Synthetic_TimeSeries_1100_20261106.zip"
```

学習済み重み：`models/intensity_cnn_v0_6.pt`。報告：`models/intensity_cnn_training_report_v0_6.json`。
診断用に元実測3件がある場合は、`--real-zip` と `--food-waste-csv` を併用できます（学習には使いません）。

テスト：`python -m pip install -r requirements-dev.txt; python -m pytest -q`。

## 研究の次の課題

同じにおいについて0〜5の強度別に、別日・別濃度・別条件で複数セッション測定してください。**強度1は未取得**です。同一元測定から生成したノイズ派生はすべて同じグループに置いて、元測定単位で学習・検証を分けてください。推定強度は有害ガス濃度や人体の安全性を保証しません。
