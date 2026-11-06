#pragma once
// Git に登録するための無害なサンプル設定。
// 実際のWi-Fiパスワード・認証トークンは local_config.h に書く（Git管理対象外）。
// USBシリアル収集だけなら、このままで動作します（Wi-Fi送信は無効）。
#define OLFA_MODE_LOCAL 1
#define OLFA_MODE_CLOUD 2
#define OLFA_UPLOAD_MODE OLFA_MODE_LOCAL
#define OLFA_WIFI_SSID "YOUR_2_4GHZ_SSID"
#define OLFA_WIFI_PASSWORD "YOUR_WIFI_PASSWORD"
#define OLFA_LOCAL_BASE_URL "http://192.168.0.100:8000"
#define OLFA_CLOUD_BASE_URL "https://YOUR-SERVICE.run.app"
#define OLFA_DEVICE_TOKEN "YOUR_LONG_RANDOM_DEVICE_TOKEN"
#define OLFA_DEVICE_ID "esp1"
#define OLFA_SESSION_ID "food_waste_001"
// TGS測定回路の値。実物と照合すること。
#define OLFA_TGS_SENSOR_VCC_V 5.0f
#define OLFA_TGS_RL_KOHM 10.0f
#define OLFA_ADC_TO_VOUT_GAIN 1.0f
