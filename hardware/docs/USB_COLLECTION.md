# USB CSV データ収集手順（OlfaLink v8）

Windows + ESP32 + COM7 を例にしています。**Wi-Fi/サーバーは不要**です。

1. ESP32をUSB接続して電源を入れる。Arduino IDEのシリアルモニタで元コードのBME/ADS/SGPが動くことを確認。
2. センサーをウォームアップし、シリアルモニタを**閉じる**。
3. PowerShellで `py -m pip install -r .\hardware\tools\requirements.txt`（初回のみ）。
4. 次のコマンドで記録開始：

```powershell
py .\hardware\tools\olfalink_usb_logger.py --port COM7 --session food_waste_001 --out-dir "$env:USERPROFILE\Downloads\OlfaLink_Data\measurements"
```

5. `[SAVE] CSV:`と`[SAVE] RAW:`に実際の保存先が表示される。
6. キー `b` で Baseline、`e` で Exposure、`r` で Refresh、`i` で Idle。キー送信はWindowsターミナルがアクティブなとき有効。
7. 終了は`Ctrl + C`。2回目は `food_waste_002` に変更して再実行。データは上書きせず日時入りの別ファイルになる。

**研究上の注意**: Baseline/Exposure/Refreshの順序と時間、測定距離、温湿度、食材組成、測定日時を別途実験ノートに記録。ロガーのCSVはシリアル出力からパースした項目に限られ、元の40列フル形式ではありません。シリアル通信途絶やパース失敗を追跡できるよう、`.log`も同時保存します。

ファイル名例：`olfalink_food_waste_001_20261107_103000.csv`。
