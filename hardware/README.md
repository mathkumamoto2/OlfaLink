# OlfaLink — ESP32 hardware / USB logging (v8)

このフォルダは既存のOlfaLink Gitリポジトリ直下へ `hardware/` として置くためのものです。
**BME688の測定が成功したv8** を基準に、センサー処理は変更せず、**認証情報の定義だけ**をプライベート設定ファイルへ分離しています。

## 構成

```text
hardware/
├── .gitignore
├── README.md
├── firmware/
│   └── OlfaLink_ESP32_AdafruitStable_v8/
│       ├── OlfaLink_ESP32_AdafruitStable_v8.ino
│       └── local_config.example.h
├── tools/
│   ├── olfalink_usb_logger.py
│   └── requirements.txt
└── docs/
    ├── WIRING.md
    └── USB_COLLECTION.md
```

## 概要

- ESP32 I2C 共通バス: **SDA=GPIO32 / SCL=GPIO33**、100 kHz。
- ADS1115 (`0x48`): A0=TGS2602、A1=TGS2603。
- BME688 (`0x76`/`0x77`): **Adafruit BME680** ドライバ。温度・湿度・気圧・ガス抵抗。
- SGP41 (`0x59`): VOC/NOx raw。Sensirion Gas Index AlgorithmがあればIndex計算を有効化。
- シリアル: **115200 baud**。学習データは **USB COM7 → PC-local CSV** で収集。
- `b/e/r/i` = baseline/exposure/refresh/idle。`Ctrl+C`でログ終了。
- **BSEC2は使わない**。そのため正式なBosch IAQ/eCO2/bVOCは出力しない。

## Arduino IDEで開く

`firmware/OlfaLink_ESP32_AdafruitStable_v8/OlfaLink_ESP32_AdafruitStable_v8.ino` を開く。Arduino公式仕様に合わせて、フォルダ名と `.ino` 名は同じにしています。

必須ライブラリは `Adafruit ADS1X15` / `Adafruit BME680` / `Sensirion I2C SGP41`（後者の依存ライブラリを含む）。VOC/NOx Index用 `Sensirion Gas Index Algorithm` は任意です。

### ローカルUSB収集

**設定ファイルを作らなくても**スケッチの公開デフォルトはWi-Fi送信無効なので、USBでのデータ収集ができます。設定の初期値は `local_config.example.h` です。

### 将来Wi-Fi / Cloud Run送信を使う場合

`local_config.example.h` を同じフォルダに `local_config.h` としてコピーし、そのファイルに実際のSSID・パスワード・トークンなどを設定してください。`local_config.h` は `.gitignore` によりGit登録対象外です。**ただし、別の場所にコピペした値や、過去にコミットした値は保護されません。**

## PCに学習データを保存 (Windows)

PowerShellをリポジトリ直下で開き、Arduino IDEのシリアルモニタを閉じて実行：

```powershell
py -m pip install -r .\hardware\tools\requirements.txt
py .\hardware\tools\olfalink_usb_logger.py --port COM7 --session food_waste_001 --out-dir "$env:USERPROFILE\Downloads\OlfaLink_Data\measurements"
```

- CSV/生ログは `C:\Users\KH\Downloads\OlfaLink_Data\measurements` に保存（Windowsユーザー KH の場合）。
- 実験ごとに `food_waste_002`, `food_waste_003` と番号を変える。
- モニタを閉じ、COM7をPythonから排他的に開く。COM7は接続環境で変わることがあります。
- ESP32を開き直すと再起動し得るため、初期化を確認してから baseline 開始。
- CSVにはシリアル出力から読み取れる項目のみ保存し、BSEC IAQの値は作りません。

詳しくは `docs/USB_COLLECTION.md` を参照。

## GitHub への追加（既存リポジトリ）

1. このZIPを解凍し、中の `hardware` フォルダを**OlfaLinkリポジトリのルート**へコピー。
2. `git status` で追加対象を確認。
3. リポジトリのルートで次を実行：

```powershell
git add hardware
git diff --cached --name-only
git diff --cached --check
git commit -m "Add ESP32 v8 hardware firmware and USB CSV logger"
git push
```

重要: `git diff --cached --name-only` に `local_config.h`, `.csv`, `.log`, `.db` が**含まれていないこと**を確認してからcommitする。
`git push`にはあらかじめGitHubリモートと認証設定が必要です。リポジトリ未作成の場合は先に作成。

## 注意

- TGSのヒーター/測定回路の5Vと、ESP32のI2C GPIOの3.3Vは別です。ESP32 GPIOに5Vを直接印加しないこと。
- ADS1115の電源電圧とアナログ入力の絶対最大定格を確認すること。ADS1115を5V駆動してI2Cプルアップが5Vの場合はESP32側に適切なレベル変換が必要です。
- 既存の稼働中v8を更新しなくてもUSBロガーは利用できます。新しいスケッチで上書きする前に元ファイルを保存してください。
- このパッケージは元のv8からの機密設定分離とフォルダ整理を行ったもので、**実機コンパイル・実機書込の再検証は行っていません**。
