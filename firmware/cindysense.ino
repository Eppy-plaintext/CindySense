
#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <math.h>

// =====================================
// 1. CONFIGURATION
// =====================================

// Enter your Wi-Fi credentials here
const char* WIFI_SSID = "eppy";
const char* WIFI_PASSWORD = "zafriakhtar2026";

// TinyIoT server
const char* TINYIOT_URL =
  "http://172.30.1.24:3000/TinyIoT/CindySense_AE/SensorData";

const char* ORIGINATOR = "CAdmin";

// MPU6050 I2C address
const uint8_t MPU_ADDR = 0x68;

// Sampling and batching
const int SAMPLE_INTERVAL_MS = 20;
const int BATCH_SIZE = 50;
const int BUFFER_SIZE = 600;

// HTTP retry configuration
const int HTTP_TIMEOUT_MS = 4000;
const int RETRY_DELAY_MS = 2000;

// =====================================
// 2. SENSOR STRUCTURE
// =====================================

struct SensorSample {
  uint32_t timestamp;
  float ax;
  float ay;
  float az;
  float gx;
  float gy;
  float gz;
};

// Shared ring buffer
SensorSample sampleBuffer[BUFFER_SIZE];
SensorSample uploadBatch[BATCH_SIZE];

int bufferHead = 0;
int bufferTail = 0;
int bufferCount = 0;

uint32_t droppedSamples = 0;
uint32_t sensorErrors = 0;

portMUX_TYPE bufferMutex =
  portMUX_INITIALIZER_UNLOCKED;

// Gyroscope calibration offsets
float gyroOffsetX = 0;
float gyroOffsetY = 0;
float gyroOffsetZ = 0;

// Network counters
uint32_t requestID = 0;
uint32_t successfulBatches = 0;
uint32_t failedAttempts = 0;

// =====================================
// 3. MPU6050 REGISTER FUNCTIONS
// =====================================

bool readRegisters(
  uint8_t reg,
  uint8_t* data,
  size_t length
) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);

  if (Wire.endTransmission(false) != 0) {
    return false;
  }

  if (Wire.requestFrom(
      MPU_ADDR,
      length,
      true
    ) != length) {
    return false;
  }

  for (size_t i = 0; i < length; i++) {
    data[i] = Wire.read();
  }

  return true;
}

bool writeRegister(
  uint8_t reg,
  uint8_t value
) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);
  Wire.write(value);

  return Wire.endTransmission() == 0;
}

// =====================================
// 4. INITIALIZE MPU6050
// =====================================

bool initializeMPU() {
  Serial.println("Checking MPU6050...");

  uint8_t id = 0;

  if (!readRegisters(0x75, &id, 1)) {
    Serial.println(
      "ERROR: WHO_AM_I read failed"
    );
    return false;
  }

  Serial.printf(
    "WHO_AM_I: 0x%02X\n",
    id
  );

  if (id != 0x68) {
    Serial.println(
      "ERROR: Unexpected sensor ID"
    );
    return false;
  }

  // Wake MPU6050
  if (!writeRegister(0x6B, 0x01)) {
    Serial.println("ERROR: Wake-up failed");
    return false;
  }

  // Enable accelerometer and gyroscope
  if (!writeRegister(0x6C, 0x00)) {
    Serial.println("ERROR: Sensor enable failed");
    return false;
  }

  // Digital low-pass filter
  if (!writeRegister(0x1A, 0x03)) {
    Serial.println("ERROR: Filter setup failed");
    return false;
  }

  // Accelerometer range: +/- 4g
  if (!writeRegister(0x1C, 0x08)) {
    Serial.println("ERROR: Accelerometer setup failed");
    return false;
  }

  // Gyroscope range: +/- 500 deg/s
  if (!writeRegister(0x1B, 0x08)) {
    Serial.println("ERROR: Gyroscope setup failed");
    return false;
  }

  delay(200);

  Serial.println(
    "MPU6050 initialization successful!"
  );

  return true;
}

// =====================================
// 5. GYROSCOPE CALIBRATION
// =====================================

bool calibrateGyro() {
  Serial.println();
  Serial.println(
    "Keep MPU6050 completely still!"
  );

  delay(2000);

  const int samples = 500;

  double sumX = 0;
  double sumY = 0;
  double sumZ = 0;

  for (int i = 0; i < samples; i++) {
    uint8_t data[6];

    if (!readRegisters(0x43, data, 6)) {
      Serial.println(
        "ERROR: Calibration read failed"
      );
      return false;
    }

    int16_t gx = (int16_t)(
      (data[0] << 8) | data[1]
    );

    int16_t gy = (int16_t)(
      (data[2] << 8) | data[3]
    );

    int16_t gz = (int16_t)(
      (data[4] << 8) | data[5]
    );

    sumX += gx;
    sumY += gy;
    sumZ += gz;

    delay(10);
  }

  gyroOffsetX = sumX / samples;
  gyroOffsetY = sumY / samples;
  gyroOffsetZ = sumZ / samples;

  Serial.println("Calibration complete!");

  Serial.printf(
    "Offsets: %.2f, %.2f, %.2f\n",
    gyroOffsetX,
    gyroOffsetY,
    gyroOffsetZ
  );

  return true;
}

// =====================================
// 6. READ SENSOR
// =====================================

bool readSensor(SensorSample &s, bool &invalidAcceleration) {
  invalidAcceleration = false;
  uint8_t data[14];

  if (!readRegisters(0x3B, data, 14)) {
    return false;
  }

  int16_t rawAX = (int16_t)(
    (data[0] << 8) | data[1]
  );

  int16_t rawAY = (int16_t)(
    (data[2] << 8) | data[3]
  );

  int16_t rawAZ = (int16_t)(
    (data[4] << 8) | data[5]
  );

  // A worn sensor cannot report zero on every acceleration axis repeatedly.
  // Reject this frame before it can enter the upload buffer.
  if (rawAX == 0 && rawAY == 0 && rawAZ == 0) {
    invalidAcceleration = true;
    return false;
  }

  int16_t rawGX = (int16_t)(
    (data[8] << 8) | data[9]
  );

  int16_t rawGY = (int16_t)(
    (data[10] << 8) | data[11]
  );

  int16_t rawGZ = (int16_t)(
    (data[12] << 8) | data[13]
  );

  s.timestamp = millis();

  // Convert raw acceleration to m/s^2
  // Accelerometer range: +/- 4g
  const float accScale =
    9.80665f / 8192.0f;

  s.ax = rawAX * accScale;
  s.ay = rawAY * accScale;
  s.az = rawAZ * accScale;

  // Convert gyro to deg/s, removing bias
  // Gyroscope range: +/- 500 deg/s
  s.gx = (rawGX - gyroOffsetX) / 65.5f;
  s.gy = (rawGY - gyroOffsetY) / 65.5f;
  s.gz = (rawGZ - gyroOffsetZ) / 65.5f;

  return true;
  }

bool recoverMPU() {
  Serial.println("Recovering MPU6050 after repeated zero-acceleration frames");
  if (!writeRegister(0x6B, 0x80)) {
    return false;
  }
  vTaskDelay(pdMS_TO_TICKS(100));
  return initializeMPU();
}

// =====================================
// 7. BUFFER OPERATIONS
// =====================================

// Add new sensor reading to ring buffer
void storeSample(const SensorSample &s) {
  portENTER_CRITICAL(&bufferMutex);

  if (bufferCount >= BUFFER_SIZE) {
    // Remove oldest sample on overflow
    bufferTail =
      (bufferTail + 1) % BUFFER_SIZE;

    bufferCount--;
    droppedSamples++;
  }

  sampleBuffer[bufferHead] = s;

  bufferHead =
    (bufferHead + 1) % BUFFER_SIZE;

  bufferCount++;

  portEXIT_CRITICAL(&bufferMutex);
}

// Copy readings into transmission buffer
int takeBatch() {
  portENTER_CRITICAL(&bufferMutex);

  int count = bufferCount;

  if (count > BATCH_SIZE) {
    count = BATCH_SIZE;
  }

  for (int i = 0; i < count; i++) {
    uploadBatch[i] =
      sampleBuffer[bufferTail];

    bufferTail =
      (bufferTail + 1) % BUFFER_SIZE;

    bufferCount--;
  }

  portEXIT_CRITICAL(&bufferMutex);

  return count;
}

int getBufferCount() {
  portENTER_CRITICAL(&bufferMutex);
  int count = bufferCount;
  portEXIT_CRITICAL(&bufferMutex);

  return count;
}

uint32_t getDroppedSamples() {
  portENTER_CRITICAL(&bufferMutex);
  uint32_t count = droppedSamples;
  portEXIT_CRITICAL(&bufferMutex);

  return count;
}

// =====================================
// 8. SENSOR SAMPLING TASK
// =====================================

void sensorTask(void* parameter) {
  TickType_t lastWake =
    xTaskGetTickCount();

  const TickType_t period =
    pdMS_TO_TICKS(SAMPLE_INTERVAL_MS);

  uint32_t sampleCounter = 0;
  uint32_t consecutiveInvalidAcceleration = 0;
  uint32_t lastRecovery = 0;

  while (true) {
    SensorSample s;
    bool invalidAcceleration = false;

    if (readSensor(s, invalidAcceleration)) {
      consecutiveInvalidAcceleration = 0;
      storeSample(s);

      sampleCounter++;

      // Print approximately once per second
      if (sampleCounter % 50 == 0) {
        float magnitude = sqrtf(
          s.ax * s.ax +
          s.ay * s.ay +
          s.az * s.az
        );

        Serial.printf(
          "AX: %.2f AY: %.2f AZ: %.2f "
          "| GX: %.2f GY: %.2f GZ: %.2f "
          "| MAG: %.2f\n",
          s.ax,
          s.ay,
          s.az,
          s.gx,
          s.gy,
          s.gz,
          magnitude
        );
      }
    } else {
      sensorErrors++;
      if (invalidAcceleration) {
        consecutiveInvalidAcceleration++;
        if (consecutiveInvalidAcceleration == 1 ||
            consecutiveInvalidAcceleration % 50 == 0) {
          Serial.println("ERROR: MPU6050 returned zero acceleration on all axes");
        }
        if (consecutiveInvalidAcceleration >= 5 &&
            (lastRecovery == 0 || millis() - lastRecovery >= 5000)) {
          lastRecovery = millis();
          if (!recoverMPU()) {
            Serial.println("ERROR: MPU6050 recovery failed");
          }
          consecutiveInvalidAcceleration = 0;
        }
      } else {
        consecutiveInvalidAcceleration = 0;
        Serial.println("ERROR: MPU6050 I2C read failed!");
      }
    }

    vTaskDelayUntil(&lastWake, period);
  }
}

// =====================================
// 9. BUILD ONEM2M JSON PAYLOAD
// =====================================

String buildPayload(int count) {
  String data;
  data.reserve(2000);

  data = "{";

  data +=
    "\"deviceId\":\"CindySense_ESP32\",";

  data += "\"sampleRateHz\":50,";
  data += "\"accUnit\":\"m/s2\",";
  data += "\"gyroUnit\":\"deg/s\",";

  data += "\"samples\":[";

  for (int i = 0; i < count; i++) {
    if (i > 0) {
      data += ",";
    }

    SensorSample &s = uploadBatch[i];

    data += "{\"t\":";
    data += String(s.timestamp);

    data += ",\"ax\":";
    data += String(s.ax, 3);

    data += ",\"ay\":";
    data += String(s.ay, 3);

    data += ",\"az\":";
    data += String(s.az, 3);

    data += ",\"gx\":";
    data += String(s.gx, 3);

    data += ",\"gy\":";
    data += String(s.gy, 3);

    data += ",\"gz\":";
    data += String(s.gz, 3);

    data += "}";
  }

  data += "]}";

  // Escape JSON for oneM2M con string
  data.replace("\\", "\\\\");
  data.replace("\"", "\\\"");

  String payload;
  payload.reserve(data.length() + 50);

  payload = "{\"m2m:cin\":{\"con\":\"";
  payload += data;
  payload += "\"}}";

  return payload;
}

// =====================================
// 10. SEND TO TINYIOT
// =====================================


int sendToTinyIoT(
  const String &payload,
  const String &ri
) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[HTTP] Wi-Fi not connected");
    return -101;
  }

  WiFiClient client;
  HTTPClient http;

  client.setTimeout(10);

  if (!http.begin(client, TINYIOT_URL)) {
    Serial.println("[HTTP] Setup failed");
    return -100;
  }

  http.setTimeout(HTTP_TIMEOUT_MS);
  http.setConnectTimeout(5000);
  http.setReuse(false);

  http.addHeader(
    "Content-Type",
    "application/json;ty=4"
  );

  http.addHeader(
    "Accept",
    "application/json"
  );

  http.addHeader(
    "X-M2M-Origin",
    ORIGINATOR
  );

  http.addHeader(
    "X-M2M-RVI",
    "4"
  );

  http.addHeader(
    "X-M2M-RI",
    ri
  );

  unsigned long startTime = millis();

  Serial.printf(
    "[HTTP] Sending %u bytes | RI: %s\n",
    (unsigned int)payload.length(),
    ri.c_str()
  );

  int code = http.POST(payload);

  unsigned long duration = millis() - startTime;

  Serial.printf(
    "[HTTP] Status: %d | Duration: %lu ms\n",
    code,
    duration
  );

  if (code > 0 && code != 201) {
    Serial.print("[HTTP] Response: ");
    Serial.println(http.getString());
  } else if (code <= 0) {
    Serial.print("[HTTP] Client error: ");
    Serial.println(http.errorToString(code));
  }

  http.end();

  return code;
}


// =====================================
// 11. NETWORK TASK
// =====================================

void networkTask(void* parameter) {
  unsigned long lastReconnect = 0;

  // Retain a failed batch for retry
  String pendingPayload = "";
  String pendingRI = "";

  int pendingCount = 0;
  bool hasPendingBatch = false;

  while (true) {

    // Check Wi-Fi
    if (WiFi.status() != WL_CONNECTED) {
      if (millis() - lastReconnect >= 10000) {
        lastReconnect = millis();

        Serial.println(
          "Reconnecting Wi-Fi..."
        );

        WiFi.reconnect();
      }

      vTaskDelay(pdMS_TO_TICKS(500));
      continue;
    }

    // Only take a new batch if none is pending
    if (!hasPendingBatch) {
      if (getBufferCount() < BATCH_SIZE) {
        vTaskDelay(pdMS_TO_TICKS(100));
        continue;
      }

      pendingCount = takeBatch();

      if (pendingCount == 0) {
        continue;
      }

      pendingPayload =
        buildPayload(pendingCount);

      // One request identifier per batch
      pendingRI =
        "sensor-" +
        String(millis()) +
        "-" +
        String(++requestID);

      hasPendingBatch = true;
    }

    // Send current batch
    int code = sendToTinyIoT(
      pendingPayload,
      pendingRI
    );

    Serial.printf(
      "TinyIoT HTTP: %d "
      "| Samples: %d "
      "| Payload: %u bytes "
      "| Buffered: %d "
      "| Dropped since boot: %lu "
      "| Invalid/read errors since boot: %lu\n",
      code,
      pendingCount,
      (unsigned int)pendingPayload.length(),
      getBufferCount(),
      (unsigned long)getDroppedSamples(),
      (unsigned long)sensorErrors
    );

    if (code == 201) {
      successfulBatches++;

      Serial.printf(
        "Successful batches: %lu\n",
        (unsigned long)successfulBatches
      );

      // Clear only after confirmed success
      pendingPayload = "";
      pendingRI = "";
      pendingCount = 0;
      hasPendingBatch = false;

      vTaskDelay(pdMS_TO_TICKS(1));

    } else {
      failedAttempts++;

      Serial.printf(
        "Upload failed. Retry attempt: %lu\n",
        (unsigned long)failedAttempts
      );

      // Keep the payload and retry
      vTaskDelay(
        pdMS_TO_TICKS(RETRY_DELAY_MS)
      );
    }
  }
}

// =====================================
// 12. SETUP
// =====================================

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println();
  Serial.println("========================");
  Serial.println("CindySense ESP32 Starting");
  Serial.println("========================");

  // Initialize I2C
  Wire.begin(21, 22);
  Wire.setClock(100000);

  // Initialize MPU6050
  if (!initializeMPU()) {
    Serial.println(
      "FATAL: MPU6050 initialization failed!"
    );

    while (true) {
      delay(1000);
    }
  }

  // Calibrate sensor
  if (!calibrateGyro()) {
    Serial.println(
      "FATAL: Gyroscope calibration failed!"
    );

    while (true) {
      delay(1000);
    }
  }

  // Connect Wi-Fi
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);

  WiFi.begin(
    WIFI_SSID,
    WIFI_PASSWORD
  );

  Serial.println("Connecting to Wi-Fi...");

  // Create sensor acquisition task
  BaseType_t sensorResult =
    xTaskCreatePinnedToCore(
      sensorTask,
      "SensorTask",
      4096,
      NULL,
      2,
      NULL,
      1
    );

  // Create network transmission task
  BaseType_t networkResult =
    xTaskCreatePinnedToCore(
      networkTask,
      "NetworkTask",
      8192,
      NULL,
      1,
      NULL,
      0
    );

  if (sensorResult != pdPASS ||
      networkResult != pdPASS) {
    Serial.println(
      "FATAL: Task creation failed!"
    );

    while (true) {
      delay(1000);
    }
  }

  Serial.println(
    "CindySense tasks started!"
  );
}

// =====================================
// 13. MAIN LOOP
// =====================================


void loop() {
  static unsigned long lastCheck = 0;

  if (millis() - lastCheck >= 5000) {
    lastCheck = millis();

    if (WiFi.status() == WL_CONNECTED) {
      Serial.print("[WiFi] Connected | IP: ");
      Serial.print(WiFi.localIP());

      Serial.print(" | RSSI: ");
      Serial.print(WiFi.RSSI());
      Serial.println(" dBm");
    } else {
      Serial.print("[WiFi] Disconnected | Status: ");
      Serial.println(WiFi.status());
    }
  }

  delay(100);
}

