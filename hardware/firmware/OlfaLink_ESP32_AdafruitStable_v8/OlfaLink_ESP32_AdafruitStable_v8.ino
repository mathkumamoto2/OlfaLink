/*
  OlfaLink ESP32 v8 - reliable original-driver edition

  Shared I2C, unchanged from the working sensor-only sketch:
    ESP32 GPIO32 SDA -> ADS1115, BME688, SGP41 SDA
    ESP32 GPIO33 SCL -> ADS1115, BME688, SGP41 SCL
    0x48 ADS1115 (A0 TGS2602, A1 TGS2603)
    0x76/0x77 BME688
    0x59 SGP41

  Required sensor libraries (same as your WORKING test):
    Adafruit ADS1X15, Adafruit BME680, Sensirion I2C SGP41
  Plus ESP32 Arduino core: WiFi, HTTPClient, Wire.
  OPTIONAL: Sensirion Gas Index Algorithm; auto-enabled if installed.
  NO Bosch bsec2/BME68x, NO ArduinoJson.

  BSEC proprietary IAQ/eCO2/bVOC cannot be derived honestly from raw gas
  resistance alone. Those optional JSON fields are omitted, not made up.

  Safety: TGS sensing circuits may use 5V, but ESP32 SDA/SCL are 3.3V.
  Do not apply >ADC's supply voltage (+ small absolute max margin) to ADS inputs.
*/

#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <WiFiClient.h>
#include <HTTPClient.h>
#include <Adafruit_ADS1X15.h>
#include <Adafruit_BME680.h>
#include <SensirionI2CSgp41.h>
#include <math.h>
#include <sys/time.h>
#include <time.h>
#include <stdlib.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>

// Gas Index Algorithm is independent of the Bosch BSEC2 driver.
// To use only the three original sensor libraries, set this to 0.
#define OLFA_ENABLE_GAS_INDEX 1
#if OLFA_ENABLE_GAS_INDEX && __has_include(<VOCGasIndexAlgorithm.h>) && __has_include(<NOxGasIndexAlgorithm.h>)
  #include <VOCGasIndexAlgorithm.h>
  #include <NOxGasIndexAlgorithm.h>
  #define OLFA_HAS_GAS_INDEX 1
#else
  #define OLFA_HAS_GAS_INDEX 0
#endif

// ----- PUBLIC CONFIGURATION (never commit Wi-Fi secrets) -----
// If local_config.h exists, use private values on this PC.
// Otherwise, use non-secret placeholders and run USB-only logging.
#if __has_include("local_config.h")
  #include "local_config.h"
#else
  #include "local_config.example.h"
#endif
// -----------------------------------------------------------

#if OLFA_UPLOAD_MODE != OLFA_MODE_LOCAL && OLFA_UPLOAD_MODE != OLFA_MODE_CLOUD
  #error "Invalid OLFA_UPLOAD_MODE"
#endif

#if OLFA_UPLOAD_MODE == OLFA_MODE_CLOUD
#include <WiFiClientSecure.h>
// Trust anchors for default *.run.app certificates (GTS). Keep them updated.
// If you use a custom domain with another CA, replace this bundle.
static const char OLFA_GTS_ROOT_CERTS[] PROGMEM = R"OLFA_ROOTS(
-----BEGIN CERTIFICATE-----
MIIFVzCCAz+gAwIBAgINAgPlk28xsBNJiGuiFzANBgkqhkiG9w0BAQwFADBHMQsw
CQYDVQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZpY2VzIExMQzEU
MBIGA1UEAxMLR1RTIFJvb3QgUjEwHhcNMTYwNjIyMDAwMDAwWhcNMzYwNjIyMDAw
MDAwWjBHMQswCQYDVQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZp
Y2VzIExMQzEUMBIGA1UEAxMLR1RTIFJvb3QgUjEwggIiMA0GCSqGSIb3DQEBAQUA
A4ICDwAwggIKAoICAQC2EQKLHuOhd5s73L+UPreVp0A8of2C+X0yBoJx9vaMf/vo
27xqLpeXo4xL+Sv2sfnOhB2x+cWX3u+58qPpvBKJXqeqUqv4IyfLpLGcY9vXmX7w
Cl7raKb0xlpHDU0QM+NOsROjyBhsS+z8CZDfnWQpJSMHobTSPS5g4M/SCYe7zUjw
TcLCeoiKu7rPWRnWr4+wB7CeMfGCwcDfLqZtbBkOtdh+JhpFAz2weaSUKK0Pfybl
qAj+lug8aJRT7oM6iCsVlgmy4HqMLnXWnOunVmSPlk9orj2XwoSPwLxAwAtcvfaH
szVsrBhQf4TgTM2S0yDpM7xSma8ytSmzJSq0SPly4cpk9+aCEI3oncKKiPo4Zor8
Y/kB+Xj9e1x3+naH+uzfsQ55lVe0vSbv1gHR6xYKu44LtcXFilWr06zqkUspzBmk
MiVOKvFlRNACzqrOSbTqn3yDsEB750Orp2yjj32JgfpMpf/VjsPOS+C12LOORc92
wO1AK/1TD7Cn1TsNsYqiA94xrcx36m97PtbfkSIS5r762DL8EGMUUXLeXdYWk70p
aDPvOmbsB4om3xPXV2V4J95eSRQAogB/mqghtqmxlbCluQ0WEdrHbEg8QOB+DVrN
VjzRlwW5y0vtOUucxD/SVRNuJLDWcfr0wbrM7Rv1/oFB2ACYPTrIrnqYNxgFlQID
AQABo0IwQDAOBgNVHQ8BAf8EBAMCAYYwDwYDVR0TAQH/BAUwAwEB/zAdBgNVHQ4E
FgQU5K8rJnEaK0gnhS9SZizv8IkTcT4wDQYJKoZIhvcNAQEMBQADggIBAJ+qQibb
C5u+/x6Wki4+omVKapi6Ist9wTrYggoGxval3sBOh2Z5ofmmWJyq+bXmYOfg6LEe
QkEzCzc9zolwFcq1JKjPa7XSQCGYzyI0zzvFIoTgxQ6KfF2I5DUkzps+GlQebtuy
h6f88/qBVRRiClmpIgUxPoLW7ttXNLwzldMXG+gnoot7TiYaelpkttGsN/H9oPM4
7HLwEXWdyzRSjeZ2axfG34arJ45JK3VmgRAhpuo+9K4l/3wV3s6MJT/KYnAK9y8J
ZgfIPxz88NtFMN9iiMG1D53Dn0reWVlHxYciNuaCp+0KueIHoI17eko8cdLiA6Ef
MgfdG+RCzgwARWGAtQsgWSl4vflVy2PFPEz0tv/bal8xa5meLMFrUKTX5hgUvYU/
Z6tGn6D/Qqc6f1zLXbBwHSs09dR2CQzreExZBfMzQsNhFRAbd03OIozUhfJFfbdT
6u9AWpQKXCBfTkBdYiJ23//OYb2MI3jSNwLgjt7RETeJ9r/tSQdirpLsQBqvFAnZ
0E6yove+7u7Y/9waLd64NnHi/Hm3lCXRSHNboTXns5lndcEZOitHTtNCjv0xyBZm
2tIMPNuzjsmhDYAPexZ3FL//2wmUspO8IFgV6dtxQ/PeEMMA3KgqlbbC1j+Qa3bb
bP6MvPJwNQzcmRk13NfIRmPVNnGuV/u3gm3c
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFVzCCAz+gAwIBAgINAgPlrsWNBCUaqxElqjANBgkqhkiG9w0BAQwFADBHMQsw
CQYDVQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZpY2VzIExMQzEU
MBIGA1UEAxMLR1RTIFJvb3QgUjIwHhcNMTYwNjIyMDAwMDAwWhcNMzYwNjIyMDAw
MDAwWjBHMQswCQYDVQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZp
Y2VzIExMQzEUMBIGA1UEAxMLR1RTIFJvb3QgUjIwggIiMA0GCSqGSIb3DQEBAQUA
A4ICDwAwggIKAoICAQDO3v2m++zsFDQ8BwZabFn3GTXd98GdVarTzTukk3LvCvpt
nfbwhYBboUhSnznFt+4orO/LdmgUud+tAWyZH8QiHZ/+cnfgLFuv5AS/T3KgGjSY
6Dlo7JUle3ah5mm5hRm9iYz+re026nO8/4Piy33B0s5Ks40FnotJk9/BW9BuXvAu
MC6C/Pq8tBcKSOWIm8Wba96wyrQD8Nr0kLhlZPdcTK3ofmZemde4wj7I0BOdre7k
RXuJVfeKH2JShBKzwkCX44ofR5GmdFrS+LFjKBC4swm4VndAoiaYecb+3yXuPuWg
f9RhD1FLPD+M2uFwdNjCaKH5wQzpoeJ/u1U8dgbuak7MkogwTZq9TwtImoS1mKPV
+3PBV2HdKFZ1E66HjucMUQkQdYhMvI35ezzUIkgfKtzra7tEscszcTJGr61K8Yzo
dDqs5xoic4DSMPclQsciOzsSrZYuxsN2B6ogtzVJV+mSSeh2FnIxZyuWfoqjx5RW
Ir9qS34BIbIjMt/kmkRtWVtd9QCgHJvGeJeNkP+byKq0rxFROV7Z+2et1VsRnTKa
G73VululycslaVNVJ1zgyjbLiGH7HrfQy+4W+9OmTN6SpdTi3/UGVN4unUu0kzCq
gc7dGtxRcw1PcOnlthYhGXmy5okLdWTK1au8CcEYof/UVKGFPP0UJAOyh9OktwID
AQABo0IwQDAOBgNVHQ8BAf8EBAMCAYYwDwYDVR0TAQH/BAUwAwEB/zAdBgNVHQ4E
FgQUu//KjiOfT5nK2+JopqUVJxce2Q4wDQYJKoZIhvcNAQEMBQADggIBAB/Kzt3H
vqGf2SdMC9wXmBFqiN495nFWcrKeGk6c1SuYJF2ba3uwM4IJvd8lRuqYnrYb/oM8
0mJhwQTtzuDFycgTE1XnqGOtjHsB/ncw4c5omwX4Eu55MaBBRTUoCnGkJE+M3DyC
B19m3H0Q/gxhswWV7uGugQ+o+MePTagjAiZrHYNSVc61LwDKgEDg4XSsYPWHgJ2u
NmSRXbBoGOqKYcl3qJfEycel/FVL8/B/uWU9J2jQzGv6U53hkRrJXRqWbTKH7QMg
yALOWr7Z6v2yTcQvG99fevX4i8buMTolUVVnjWQye+mew4K6Ki3pHrTgSAai/Gev
HyICc/sgCq+dVEuhzf9gR7A/Xe8bVr2XIZYtCtFenTgCR2y59PYjJbigapordwj6
xLEokCZYCDzifqrXPW+6MYgKBesntaFJ7qBFVHvmJ2WZICGoo7z7GJa7Um8M7YNR
TOlZ4iBgxcJlkoKM8xAfDoqXvneCbT+PHV28SSe9zE8P4c52hgQjxcCMElv924Sg
JPFI/2R80L5cFtHvma3AH/vLrrw4IgYmZNralw4/KBVEqE8AyvCazM90arQ+POuV
7LXTWtiBmelDGDfrs7vRWGJB82bSj6p4lVQgw1oudCvV0b4YacCs1aTPObpRhANl
6WLAYv7YTVWW4tAR+kg0Eeye7QUd5MjWHYbL
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICCTCCAY6gAwIBAgINAgPluILrIPglJ209ZjAKBggqhkjOPQQDAzBHMQswCQYD
VQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZpY2VzIExMQzEUMBIG
A1UEAxMLR1RTIFJvb3QgUjMwHhcNMTYwNjIyMDAwMDAwWhcNMzYwNjIyMDAwMDAw
WjBHMQswCQYDVQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZpY2Vz
IExMQzEUMBIGA1UEAxMLR1RTIFJvb3QgUjMwdjAQBgcqhkjOPQIBBgUrgQQAIgNi
AAQfTzOHMymKoYTey8chWEGJ6ladK0uFxh1MJ7x/JlFyb+Kf1qPKzEUURout736G
jOyxfi//qXGdGIRFBEFVbivqJn+7kAHjSxm65FSWRQmx1WyRRK2EE46ajA2ADDL2
4CejQjBAMA4GA1UdDwEB/wQEAwIBhjAPBgNVHRMBAf8EBTADAQH/MB0GA1UdDgQW
BBTB8Sa6oC2uhYHP0/EqEr24Cmf9vDAKBggqhkjOPQQDAwNpADBmAjEA9uEglRR7
VKOQFhG/hMjqb2sXnh5GmCCbn9MN2azTL818+FsuVbu/3ZL3pAzcMeGiAjEA/Jdm
ZuVDFhOD3cffL74UOO0BzrEXGhF16b0DjyZ+hOXJYKaV11RZt+cRLInUue4X
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICCTCCAY6gAwIBAgINAgPlwGjvYxqccpBQUjAKBggqhkjOPQQDAzBHMQswCQYD
VQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZpY2VzIExMQzEUMBIG
A1UEAxMLR1RTIFJvb3QgUjQwHhcNMTYwNjIyMDAwMDAwWhcNMzYwNjIyMDAwMDAw
WjBHMQswCQYDVQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZpY2Vz
IExMQzEUMBIGA1UEAxMLR1RTIFJvb3QgUjQwdjAQBgcqhkjOPQIBBgUrgQQAIgNi
AATzdHOnaItgrkO4NcWBMHtLSZ37wWHO5t5GvWvVYRg1rkDdc/eJkTBa6zzuhXyi
QHY7qca4R9gq55KRanPpsXI5nymfopjTX15YhmUPoYRlBtHci8nHc8iMai/lxKvR
HYqjQjBAMA4GA1UdDwEB/wQEAwIBhjAPBgNVHRMBAf8EBTADAQH/MB0GA1UdDgQW
BBSATNbrdP9JNqPV2Py1PsVq8JQdjDAKBggqhkjOPQQDAwNpADBmAjEA6ED/g94D
9J+uHXqnLrmvT/aDHQ4thQEd0dlq7A/Cr8deVl5c1RxYIigL9zC2L7F8AjEA8GE8
p/SgguMh1YQdc4acLa/KNJvxn7kjNuK8YAOdgLOaVsjh4rsUecrNIdSUtUlD
-----END CERTIFICATE-----
)OLFA_ROOTS";
#endif

constexpr int I2C_SDA = 32;
constexpr int I2C_SCL = 33;
constexpr uint8_t ADS_ADDRESS = 0x48;
constexpr uint8_t SGP_ADDRESS = 0x59;
constexpr uint32_t SGP_PERIOD_MS = 1000;
constexpr uint32_t RECORD_PERIOD_MS = 2000;
constexpr uint32_t BME_RETRY_MS = 15000;
constexpr uint32_t SGP_RETRY_MS = 15000;
constexpr uint16_t DEFAULT_RH_TICKS = 0x8000;  // 50% RH
constexpr uint16_t DEFAULT_TEMP_TICKS = 0x6666; // 25 C
constexpr time_t MIN_UNIX_TIME = 1577836800;    // 2020-01-01 UTC

Adafruit_ADS1115 ads;
Adafruit_BME680 bme(&Wire); // SAME initialization method as working original.
SensirionI2CSgp41 sgp41;
#if OLFA_HAS_GAS_INDEX
VOCGasIndexAlgorithm vocAlgorithm;
NOxGasIndexAlgorithm noxAlgorithm;
#endif

bool adsReady = false;
bool bmeReady = false;
bool sgpReady = false;
uint8_t bmeAddress = 0;
uint32_t bmeLastTry = 0;
uint32_t bmeLastRead = 0;
uint32_t sgpLastTry = 0;
uint32_t sgpLastRead = 0;
uint32_t sgpNextMs = 0;
uint32_t recordNextMs = 0;
uint32_t recordCount = 0;
uint8_t bmeFailures = 0;
uint8_t sgpFailures = 0;
char stage[12] = "baseline";

struct Measurements {
  float tempC = NAN, rh = NAN, pressureHpa = NAN, gasOhm = NAN;
  int16_t tgs2602Adc = -1, tgs2603Adc = -1;
  float tgs2602V = NAN, tgs2603V = NAN;
  float tgs2602Rs = NAN, tgs2603Rs = NAN;
  uint16_t vocRaw = 0, noxRaw = 0;
  uint16_t rhTicks = DEFAULT_RH_TICKS, tempTicks = DEFAULT_TEMP_TICKS;
  int vocIndex = -1, noxIndex = -1;
  int sgpError = -1;
  bool sgpCompensated = false;
  bool sgpValid = false;
} m;

struct Packet { char json[1800]; };
QueueHandle_t uploadQueue = nullptr;

bool i2cResponds(uint8_t addr) {
  Wire.beginTransmission(addr);
  return Wire.endTransmission() == 0;
}

void scanI2C() {
  Serial.println("[I2C] SDA=GPIO32, SCL=GPIO33");
  for (uint8_t addr = 0x08; addr < 0x78; ++addr) {
    if (i2cResponds(addr)) Serial.printf("  Found 0x%02X\n", addr);
  }
}

void initADS() {
  adsReady = i2cResponds(ADS_ADDRESS) && ads.begin(ADS_ADDRESS, &Wire);
  if (adsReady) ads.setGain(GAIN_TWOTHIRDS); // PGA range, NOT max safe voltage.
  Serial.printf("[ADS1115] %s\n", adsReady ? "OK" : "FAIL");
}

void initBME() {
  bmeLastTry = millis();
  bmeReady = false;
  const uint8_t addresses[] = {0x76, 0x77};
  for (uint8_t addr : addresses) {
    if (!i2cResponds(addr)) continue;
    Serial.printf("[BME688] ACK at 0x%02X\n", addr);
    if (!bme.begin(addr)) {
      Serial.printf("[BME688] begin failed at 0x%02X\n", addr);
      continue;
    }
    bme.setTemperatureOversampling(BME680_OS_8X);
    bme.setHumidityOversampling(BME680_OS_2X);
    bme.setPressureOversampling(BME680_OS_4X);
    bme.setIIRFilterSize(BME680_FILTER_SIZE_3);
    bme.setGasHeater(320, 150);
    bmeAddress = addr;
    bmeReady = true;
    bmeFailures = 0;
    Serial.printf("[BME688] Adafruit original driver OK at 0x%02X\n", addr);
    return;
  }
  Serial.println("[BME688] NOT FOUND; will retry every 15 seconds");
}

void initSGP() {
  sgpLastTry = millis();
  sgpReady = false;
  m.sgpValid = false;
  if (!i2cResponds(SGP_ADDRESS)) {
    Serial.println("[SGP41] 0x59 not found");
    return;
  }
  sgp41.begin(Wire);
  uint16_t sn[3] = {0, 0, 0};
  uint16_t err = sgp41.getSerialNumber(sn);
  if (err) {
    Serial.printf("[SGP41] serial read error=%u\n", err);
    return;
  }
  // Exact successful sequence from the original sensor-only sketch.
  for (int i = 0; i < 9; ++i) {
    uint32_t started = millis();
    uint16_t voc = 0;
    err = sgp41.executeConditioning(DEFAULT_RH_TICKS, DEFAULT_TEMP_TICKS, voc);
    if (err) {
      Serial.printf("[SGP41] conditioning error=%u\n", err);
      return;
    }
    uint32_t dt = millis() - started;
    if (dt < 1000) delay(1000 - dt);
  }
  sgpReady = true;
  sgpFailures = 0;
  sgpLastRead = 0;
  sgpNextMs = millis();
#if OLFA_HAS_GAS_INDEX
  vocAlgorithm.reset();
  noxAlgorithm.reset();
#endif
  Serial.println("[SGP41] OK; index algorithm needs startup stabilization");
}

void readBME() {
  if (!bmeReady) {
    if (millis() - bmeLastTry >= BME_RETRY_MS) initBME();
    return;
  }
  if (!bme.performReading()) {
    Serial.println("[BME688] performReading failed");
    if (++bmeFailures >= 5) {
      bmeReady = false;
      bmeLastTry = millis();
    }
    return;
  }
  bmeFailures = 0;
  m.tempC = bme.temperature;
  m.rh = bme.humidity;
  m.pressureHpa = bme.pressure / 100.0f;
  m.gasOhm = static_cast<float>(bme.gas_resistance);
  bmeLastRead = millis();
}

bool bmeFresh() {
  return bmeReady && bmeLastRead && millis() - bmeLastRead < 10000 &&
         isfinite(m.tempC) && isfinite(m.rh) && isfinite(m.gasOhm);
}

uint16_t rhToTicks(float rh) {
  return static_cast<uint16_t>(lroundf(constrain(rh, 0.0f, 100.0f) * 65535.0f / 100.0f));
}

uint16_t tempToTicks(float tc) {
  return static_cast<uint16_t>(lroundf((constrain(tc, -45.0f, 130.0f) + 45.0f) * 65535.0f / 175.0f));
}

void serviceSGP() {
  if (!sgpReady) {
    if (millis() - sgpLastTry >= SGP_RETRY_MS) initSGP();
    return;
  }
  if (static_cast<int32_t>(millis() - sgpNextMs) < 0) return;
  sgpNextMs = millis() + SGP_PERIOD_MS;
  m.sgpCompensated = bmeFresh();
  m.rhTicks = m.sgpCompensated ? rhToTicks(m.rh) : DEFAULT_RH_TICKS;
  m.tempTicks = m.sgpCompensated ? tempToTicks(m.tempC) : DEFAULT_TEMP_TICKS;
  uint16_t voc = 0, nox = 0;
  uint16_t err = sgp41.measureRawSignals(m.rhTicks, m.tempTicks, voc, nox);
  m.sgpError = err;
  if (err) {
    m.sgpValid = false;
    Serial.printf("[SGP41] raw read error=%u\n", err);
    if (++sgpFailures >= 5) {
      sgpReady = false;
      sgpLastTry = millis();
    }
    return;
  }
  sgpFailures = 0;
  m.vocRaw = voc;
  m.noxRaw = nox;
  m.sgpValid = true;
#if OLFA_HAS_GAS_INDEX
  if (sgpLastRead && millis() - sgpLastRead > 2500) {
    vocAlgorithm.reset();
    noxAlgorithm.reset();
    Serial.println("[GAS INDEX] sampling gap: algorithm reset");
  }
  m.vocIndex = vocAlgorithm.process(voc);
  m.noxIndex = noxAlgorithm.process(nox);
#endif
  sgpLastRead = millis();
}

float tgsResistance(float v) {
  if (!isfinite(v) || v <= 0.0f || v >= OLFA_TGS_SENSOR_VCC_V) return NAN;
  return OLFA_TGS_RL_KOHM * (OLFA_TGS_SENSOR_VCC_V / v - 1.0f);
}

void readADS() {
  if (!adsReady) {
    if (i2cResponds(ADS_ADDRESS)) initADS();
    if (!adsReady) return;
  }
  m.tgs2602Adc = ads.readADC_SingleEnded(0);
  m.tgs2603Adc = ads.readADC_SingleEnded(1);
  m.tgs2602V = m.tgs2602Adc >= 0 ? ads.computeVolts(m.tgs2602Adc) * OLFA_ADC_TO_VOUT_GAIN : NAN;
  m.tgs2603V = m.tgs2603Adc >= 0 ? ads.computeVolts(m.tgs2603Adc) * OLFA_ADC_TO_VOUT_GAIN : NAN;
  m.tgs2602Rs = tgsResistance(m.tgs2602V);
  m.tgs2603Rs = tgsResistance(m.tgs2603V);
}

// Construct valid JSON without ArduinoJson. Strings are properly escaped.
String jsonQuote(const char *s) {
  String out = "\"";
  for (; *s; ++s) {
    unsigned char c = static_cast<unsigned char>(*s);
    if (c == '\\' || c == '"') { out += '\\'; out += char(c); }
    else if (c < 0x20) { char esc[7]; snprintf(esc, sizeof(esc), "\\u%04x", c); out += esc; }
    else out += char(c);
  }
  out += '"';
  return out;
}

void field(String &out, const char *name, const String &value) {
  if (out.length() > 1) out += ',';
  out += '"'; out += name; out += "\":"; out += value;
}

String realValue(float x, unsigned digits = 3) {
  return isfinite(x) ? String(x, digits) : String("null");
}

String intValue(bool valid, int64_t x) {
  return valid ? String(static_cast<long>(x)) : String("null");
}

bool timeReady() { return time(nullptr) >= MIN_UNIX_TIME; }

uint64_t epochMs() {
  timeval tv;
  gettimeofday(&tv, nullptr);
  return static_cast<uint64_t>(tv.tv_sec) * 1000ULL + tv.tv_usec / 1000ULL;
}

String buildJson() {
  timeval tv;
  gettimeofday(&tv, nullptr);
  const uint64_t timestamp = static_cast<uint64_t>(tv.tv_sec) * 1000ULL + tv.tv_usec / 1000ULL;
  String out;
  out.reserve(1600);
  out = "{";
  const bool bmeOK = bmeFresh();
  const bool sgpOK = m.sgpValid && sgpLastRead && millis() - sgpLastRead < 2500;
  field(out, "device", jsonQuote(OLFA_DEVICE_ID));
  // Arduino String has no guaranteed uint64_t overload: write timestamp explicitly.
  char epochText[24];
  snprintf(epochText, sizeof(epochText), "%llu", static_cast<unsigned long long>(timestamp));
  field(out, "epoch_ms", String(epochText));
  field(out, "session", jsonQuote(OLFA_SESSION_ID));
  field(out, "millis", String(millis()));
  field(out, "led", jsonQuote(stage));
  field(out, "warmup_s", realValue(millis() / 1000.0f, 3));
  field(out, "bme_i2c_addr", intValue(bmeReady, bmeAddress));
  field(out, "bme_heater_profile", bmeReady ? jsonQuote("fixed") : "null");
  field(out, "bme_heater_c", bmeReady ? "320" : "null");
  field(out, "bme_heater_ms", bmeReady ? "150" : "null");
  field(out, "bme_age_ms", bmeOK ? String(millis() - bmeLastRead) : "null");
  field(out, "bme_gas_ohm", realValue(bmeOK ? m.gasOhm : NAN, 0));
  field(out, "bme_gas_kohm", realValue(bmeOK ? m.gasOhm / 1000.0f : NAN));
  field(out, "bme_t", realValue(bmeOK ? m.tempC : NAN));
  field(out, "bme_rh", realValue(bmeOK ? m.rh : NAN));
  field(out, "bme_p", realValue(bmeOK ? m.pressureHpa : NAN));
  // No BSEC2: bsec_iaq/eCO2/bVOC are intentionally not claimed.
  field(out, "tgs2602_adc", intValue(m.tgs2602Adc >= 0, m.tgs2602Adc));
  field(out, "tgs2603_adc", intValue(m.tgs2603Adc >= 0, m.tgs2603Adc));
  field(out, "tgs2602_vout", realValue(m.tgs2602V, 4));
  field(out, "tgs2603_vout", realValue(m.tgs2603V, 4));
  field(out, "tgs2602_rs_kohm", realValue(m.tgs2602Rs));
  field(out, "tgs2603_rs_kohm", realValue(m.tgs2603Rs));
  field(out, "sgp_voc", intValue(sgpOK, m.vocRaw));
  field(out, "sgp_nox", intValue(sgpOK, m.noxRaw));
  field(out, "sgp_rh_ticks", intValue(sgpOK, m.rhTicks));
  field(out, "sgp_temp_ticks", intValue(sgpOK, m.tempTicks));
  field(out, "sgp_compensated", intValue(sgpOK, m.sgpCompensated ? 1 : 0));
  field(out, "sgp_err", intValue(m.sgpError >= 0, m.sgpError));
#if OLFA_HAS_GAS_INDEX
  field(out, "sgp_voc_index", intValue(sgpOK && m.vocIndex >= 0, m.vocIndex));
  field(out, "sgp_nox_index", intValue(sgpOK && m.noxIndex >= 0, m.noxIndex));
  field(out, "sgp_voc_learning", intValue(sgpOK && m.vocIndex >= 0, m.vocIndex == 0 ? 1 : 0));
  field(out, "sgp_nox_learning", intValue(sgpOK && m.noxIndex >= 0, m.noxIndex == 0 ? 1 : 0));
#endif
  out += '}';
  return out;
}

bool settingsOK() {
  return strcmp(OLFA_WIFI_PASSWORD, "YOUR_WIFI_PASSWORD") != 0 &&
         strlen(OLFA_WIFI_PASSWORD) > 0 &&
         strcmp(OLFA_DEVICE_TOKEN, "YOUR_LONG_RANDOM_DEVICE_TOKEN") != 0 &&
         strlen(OLFA_DEVICE_TOKEN) >= 16;
}

#if OLFA_UPLOAD_MODE == OLFA_MODE_LOCAL
bool synchronizeFromPC() {
  WiFiClient client;
  HTTPClient http;
  String url = OLFA_LOCAL_BASE_URL;
  url += "/api/v1/device/time";
  http.setConnectTimeout(2500);
  http.setTimeout(3500);
  if (!http.begin(client, url)) return false;
  int code = http.GET();
  String body = code == 200 ? http.getString() : "";
  http.end();
  int pos = body.indexOf("\"epoch_ms\"");
  if (pos < 0) return false;
  pos = body.indexOf(':', pos);
  if (pos < 0) return false;
  const char *start = body.c_str() + pos + 1;
  while (*start == ' ' || *start == '\t') ++start;
  char *end = nullptr;
  unsigned long long ms = strtoull(start, &end, 10);
  if (end == start || ms < 1577836800000ULL || ms > 4102444800000ULL) return false;
  timeval clock;
  clock.tv_sec = static_cast<time_t>(ms / 1000ULL);
  clock.tv_usec = static_cast<suseconds_t>((ms % 1000ULL) * 1000ULL);
  bool ok = settimeofday(&clock, nullptr) == 0;
  if (ok) Serial.println("[TIME] PC clock synchronized");
  return ok;
}
#endif

void senderTask(void *) {
  Packet pending{};
  bool hasPending = false;
  uint32_t lastClockAttempt = 0, lastWiFiAttempt = 0;
#if OLFA_UPLOAD_MODE == OLFA_MODE_CLOUD
  WiFiClientSecure client;
  client.setCACert(OLFA_GTS_ROOT_CERTS);
#else
  WiFiClient client;
#endif
  while (true) {
    if (WiFi.status() != WL_CONNECTED) {
      if (lastWiFiAttempt == 0 || millis() - lastWiFiAttempt > 10000) {
        lastWiFiAttempt = millis();
        WiFi.reconnect();
        Serial.println("[WIFI] reconnecting...");
      }
      vTaskDelay(pdMS_TO_TICKS(500));
      continue;
    }
    if (!timeReady()) {
      if (lastClockAttempt == 0 || millis() - lastClockAttempt > 10000) {
        lastClockAttempt = millis();
#if OLFA_UPLOAD_MODE == OLFA_MODE_LOCAL
        if (!synchronizeFromPC()) Serial.println("[TIME] local PC unreachable; sampling continues");
#else
        configTime(0, 0, "time.google.com", "pool.ntp.org");
        Serial.println("[TIME] waiting for NTP");
#endif
      }
      vTaskDelay(pdMS_TO_TICKS(500));
      continue;
    }
    if (!hasPending) hasPending = xQueueReceive(uploadQueue, &pending, pdMS_TO_TICKS(1000)) == pdTRUE;
    if (!hasPending) continue;
    HTTPClient http;
    http.setConnectTimeout(3000);
    http.setTimeout(5000);
    http.setFollowRedirects(HTTPC_DISABLE_FOLLOW_REDIRECTS);
#if OLFA_UPLOAD_MODE == OLFA_MODE_LOCAL
    String url = OLFA_LOCAL_BASE_URL;
    url += "/api/v1/measurements";
#else
    String url = OLFA_CLOUD_BASE_URL;
    url += "/api/v1/measurements";
#endif
    int code = -1;
    if (http.begin(client, url)) {
      http.addHeader("Content-Type", "application/json");
      http.addHeader("X-Device-Token", OLFA_DEVICE_TOKEN);
      code = http.POST(reinterpret_cast<uint8_t *>(pending.json), strlen(pending.json));
      http.end();
    }
    if (code == 201) {
      Serial.println("[HTTP] 201 stored");
      hasPending = false;
    } else if (code >= 400 && code < 500) {
      Serial.printf("[HTTP] %d rejected; verify token/schema; record discarded\n", code);
      hasPending = false;
    } else {
      Serial.printf("[HTTP] %d, retrying (RAM queue is finite)\n", code);
      vTaskDelay(pdMS_TO_TICKS(2500));
    }
  }
}

void serialControls() {
  while (Serial.available()) {
    const char key = Serial.read();
    const char *next = nullptr;
    if (key == 'b') next = "baseline";
    if (key == 'e') next = "exposure";
    if (key == 'r') next = "refresh";
    if (key == 'i') next = "idle";
    if (next) {
      strncpy(stage, next, sizeof(stage) - 1);
      stage[sizeof(stage) - 1] = '\0';
      Serial.printf("[STAGE] %s\n", stage);
    }
  }
}

void printSample() {
  Serial.printf("\n[SAMPLE] #%lu stage=%s BME=%s ADS=%s SGP=%s\n",
                static_cast<unsigned long>(recordCount++), stage,
                bmeFresh() ? "OK" : "--", adsReady ? "OK" : "--",
                m.sgpValid ? "OK" : "--");
  Serial.printf("  TGS2602 A0: ADC=%d Vout=%.4fV Rs=%.3fkOhm\n", m.tgs2602Adc, m.tgs2602V, m.tgs2602Rs);
  Serial.printf("  TGS2603 A1: ADC=%d Vout=%.4fV Rs=%.3fkOhm\n", m.tgs2603Adc, m.tgs2603V, m.tgs2603Rs);
  if (bmeFresh()) {
    Serial.printf("  BME688: T=%.2fC RH=%.2f%% P=%.2fhPa Gas=%.2fkOhm (Adafruit)\n",
                  m.tempC, m.rh, m.pressureHpa, m.gasOhm / 1000.0f);
  } else Serial.println("  BME688: no valid sample yet");
  if (m.sgpValid) {
    Serial.printf("  SGP41: VOCraw=%u NOxraw=%u (RH compensation=%s)\n",
                  m.vocRaw, m.noxRaw, m.sgpCompensated ? "on" : "default");
#if OLFA_HAS_GAS_INDEX
    Serial.printf("  Sensirion Index: VOC=%d NOx=%d (0 during startup blackout)\n", m.vocIndex, m.noxIndex);
#endif
  } else Serial.println("  SGP41: conditioning or no valid sample");
  Serial.println("  BSEC IAQ: not computed in this Adafruit-only BME688 edition");
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n=== OlfaLink ESP32 v8 | ORIGINAL SENSOR LIBRARIES ===");
  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(100000);
  delay(300);
  scanI2C();
  initADS();
  initBME();
  initSGP();
  recordNextMs = millis() + RECORD_PERIOD_MS;
#if OLFA_HAS_GAS_INDEX
  Serial.println("[INDEX] official Sensirion Gas Index Algorithm enabled");
#else
  Serial.println("[INDEX] algorithm library absent or disabled: raw values still available");
#endif
  if (settingsOK()) {
    uploadQueue = xQueueCreate(12, sizeof(Packet));
    if (uploadQueue) {
      WiFi.mode(WIFI_STA);
      WiFi.setAutoReconnect(true);
      WiFi.begin(OLFA_WIFI_SSID, OLFA_WIFI_PASSWORD);
      if (xTaskCreate(senderTask, "OlfaSender", 12288, nullptr, 1, nullptr) != pdPASS) {
        vQueueDelete(uploadQueue);
        uploadQueue = nullptr;
        Serial.println("[WIFI] sender task creation failed");
      }
    }
  } else Serial.println("[WIFI] set password and device token to enable uploads");
  Serial.println("[SERIAL] b=baseline e=exposure r=refresh i=idle");
}

void loop() {
  serialControls();
  serviceSGP();
  if (static_cast<int32_t>(millis() - recordNextMs) >= 0) {
    if (millis() - recordNextMs > RECORD_PERIOD_MS)
      recordNextMs = millis() + RECORD_PERIOD_MS;
    else
      recordNextMs += RECORD_PERIOD_MS;
    readBME();
    readADS();
    printSample();
    if (uploadQueue && timeReady()) {
      String json = buildJson();
      if (json.length() < sizeof(Packet::json)) {
        Packet item{};
        json.toCharArray(item.json, sizeof(item.json));
        if (xQueueSend(uploadQueue, &item, 0) != pdTRUE)
          Serial.println("[QUEUE] FULL; sample lost (RAM only)");
      } else Serial.println("[JSON] too large; sample lost");
    } else if (uploadQueue && !timeReady()) {
      Serial.println("[TIME] waiting for PC time; no upload, measurement continues");
    }
  }
  delay(5);
}
