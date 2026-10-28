
#include <Wire.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include "config.h"

Adafruit_MPU6050 mpu;

// Wi-Fi configuration
const char* ssid = CINDYSENSE_WIFI_SSID;
const char* password = CINDYSENSE_WIFI_PASSWORD;

// TinyIoT server on Windows laptop
const char* tinyIoTURL = CINDYSENSE_TINYIOT_URL;

// Temporary prototype originator
const char* originator = CINDYSENSE_ORIGINATOR;

unsigned long lastSend = 0;
unsigned long lastReconnect = 0;
unsigned long requestID = 0;

const unsigned long sendInterval = 3000;

bool sensorReady = false;

void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);

  Serial.print("Connecting to WiFi");

  unsigned long start = millis();

  while (WiFi.status() != WL_CONNECTED &&
         millis() - start < 15000) {
    delay(500);
    Serial.print(".");
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nWiFi Connected!");
    Serial.print("ESP32 IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("\nWiFi connection failed.");
  }
}

void sendSensorData() {
  if (WiFi.status() != WL_CONNECTED || !sensorReady) {
    return;
  }

  sensors_event_t a, g, temp;

  if (!mpu.getEvent(&a, &g, &temp)) {
    Serial.println("Sensor reading failed!");
    return;
  }

  // Build sensor JSON
  String sensorJSON = "{";
  sensorJSON += "\"accX\":" + String(a.acceleration.x, 3) + ",";
  sensorJSON += "\"accY\":" + String(a.acceleration.y, 3) + ",";
  sensorJSON += "\"accZ\":" + String(a.acceleration.z, 3) + ",";
  sensorJSON += "\"gyroX\":" + String(g.gyro.x, 3) + ",";
  sensorJSON += "\"gyroY\":" + String(g.gyro.y, 3) + ",";
  sensorJSON += "\"gyroZ\":" + String(g.gyro.z, 3);
  sensorJSON += "}";

  // Escape JSON for oneM2M con string
  String escaped = sensorJSON;
  escaped.replace("\\", "\\\\");
  escaped.replace("\"", "\\\"");

  String payload = "{\"m2m:cin\":{\"con\":\"";
  payload += escaped;
  payload += "\"}}";

  WiFiClient client;
  HTTPClient http;

  http.setTimeout(5000);

  if (!http.begin(client, tinyIoTURL)) {
    Serial.println("HTTP connection setup failed");
    return;
  }

  http.addHeader("Content-Type", "application/json;ty=4");
  http.addHeader("Accept", "application/json");
  http.addHeader("X-M2M-Origin", originator);
  http.addHeader("X-M2M-RVI", "4");
  http.addHeader("X-M2M-RI", "esp32-" + String(++requestID));

  int httpCode = http.POST(payload);

  Serial.print("HTTP status: ");
  Serial.println(httpCode);

  if (httpCode > 0) {
    String response = http.getString();
    Serial.println(response);
  } else {
    Serial.print("HTTP error: ");
    Serial.println(http.errorToString(httpCode));
  }

  http.end();
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  Wire.begin(21, 22);

  sensorReady = mpu.begin();

  if (sensorReady) {
    Serial.println("MPU6050 Connected!");
  } else {
    Serial.println("MPU6050 not detected!");
  }

  connectWiFi();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    if (millis() - lastReconnect >= 5000) {
      lastReconnect = millis();
      WiFi.reconnect();
    }
  }

  if (sensorReady && millis() - lastSend >= sendInterval) {
    lastSend = millis();
    sendSensorData();
  }

  delay(20);
}
