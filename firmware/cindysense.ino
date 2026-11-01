
#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <esp_system.h>
#include <ctype.h>
#include <math.h>

// Arduino generates function prototypes before the type definitions below.
struct BufferStats;
enum VerificationResult : int;

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
// 30 seconds of samples. A pending 50-sample batch is held separately.
const int BUFFER_SIZE = 1500;

// HTTP retry configuration
const int HTTP_TIMEOUT_MS = 4000;
const int RETRY_DELAY_MS = 2000;
const int MAX_RETRY_DELAY_MS = 8000;

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
uint32_t i2cReadFailures = 0;
uint32_t invalidAccelerationFrames = 0;
uint32_t missedSampleSlots = 0;
int peakBufferCount = 0;

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
uint32_t bootNonce = 0;

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
bool storeSample(const SensorSample &s) {
  portENTER_CRITICAL(&bufferMutex);

  bool overflow = bufferCount >= BUFFER_SIZE;
  if (overflow) {
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
  if (bufferCount > peakBufferCount) {
    peakBufferCount = bufferCount;
  }

  portEXIT_CRITICAL(&bufferMutex);
  return overflow;
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

struct BufferStats {
  int buffered;
  int peak;
  uint32_t dropped;
  uint32_t readFailures;
  uint32_t invalidFrames;
  uint32_t missedSlots;
};

BufferStats getBufferStats() {
  BufferStats stats;
  portENTER_CRITICAL(&bufferMutex);
  stats.buffered = bufferCount;
  stats.peak = peakBufferCount;
  stats.dropped = droppedSamples;
  stats.readFailures = i2cReadFailures;
  stats.invalidFrames = invalidAccelerationFrames;
  stats.missedSlots = missedSampleSlots;
  portEXIT_CRITICAL(&bufferMutex);

  return stats;
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
  uint32_t consecutiveI2cFailures = 0;
  uint32_t lastRecovery = 0;
  uint32_t lastAttempt = 0;
  uint32_t lastDiagnostics = millis();
  uint32_t lastReportedMissedSlots = 0;

  while (true) {
    uint32_t now = millis();
    if (lastAttempt && now - lastAttempt > SAMPLE_INTERVAL_MS + 5) {
      uint32_t missed = (now - lastAttempt - 1) / SAMPLE_INTERVAL_MS;
      portENTER_CRITICAL(&bufferMutex);
      missedSampleSlots += missed;
      portEXIT_CRITICAL(&bufferMutex);
    }
    lastAttempt = now;
    SensorSample s;
    bool invalidAcceleration = false;

    if (readSensor(s, invalidAcceleration)) {
      consecutiveInvalidAcceleration = 0;
      consecutiveI2cFailures = 0;
      if (storeSample(s)) {
        BufferStats stats = getBufferStats();
        if (stats.dropped == 1 || stats.dropped % BATCH_SIZE == 0) {
          Serial.printf("WARNING: sensor buffer overflow; dropped %lu samples, buffered %d/%d\n",
                        (unsigned long)stats.dropped, stats.buffered, BUFFER_SIZE);
        }
      }

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
      portENTER_CRITICAL(&bufferMutex);
      if (invalidAcceleration) {
        invalidAccelerationFrames++;
      } else {
        i2cReadFailures++;
      }
      portEXIT_CRITICAL(&bufferMutex);
      if (invalidAcceleration) {
        consecutiveI2cFailures = 0;
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
        consecutiveI2cFailures++;
        if (consecutiveI2cFailures == 1 || consecutiveI2cFailures % 50 == 0) {
          Serial.printf("WARNING: MPU6050 I2C read failed (%lu consecutive)\n",
                        (unsigned long)consecutiveI2cFailures);
        }
      }
    }

    if (millis() - lastDiagnostics >= 1000) {
      lastDiagnostics = millis();
      BufferStats stats = getBufferStats();
      if (stats.missedSlots != lastReportedMissedSlots) {
        Serial.printf("WARNING: missed %lu additional 20 ms sample slots\n",
                      (unsigned long)(stats.missedSlots - lastReportedMissedSlots));
        lastReportedMissedSlots = stats.missedSlots;
      }
      Serial.printf("[SENSOR] buffered=%d/%d peak=%d dropped=%lu i2c_failures=%lu "
                    "invalid_frames=%lu missed_slots=%lu\n",
                    stats.buffered, BUFFER_SIZE, stats.peak,
                    (unsigned long)stats.dropped,
                    (unsigned long)stats.readFailures,
                    (unsigned long)stats.invalidFrames,
                    (unsigned long)stats.missedSlots);
    }

    vTaskDelayUntil(&lastWake, period);
  }
}

// =====================================
// 9. BUILD ONEM2M JSON PAYLOAD
// =====================================

String buildPayload(int count, const String &batchName) {
  String data;
  if (!data.reserve(128 + count * 120)) {
    return "";
  }

  data = "{";

  data +=
    "\"deviceId\":\"CindySense_ESP32\",";
  data += "\"batchId\":\"";
  data += batchName;
  data += "\",";

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
  if (!payload.reserve(data.length() + batchName.length() + 80)) {
    return "";
  }

  payload = "{\"m2m:cin\":{\"rn\":\"";
  payload += batchName;
  payload += "\",\"con\":\"";
  payload += data;
  payload += "\"}}";

  return payload;
}

// =====================================
// 10. SEND TO TINYIOT
// =====================================

String nextRequestId() {
  return "sensor-" + String(bootNonce, HEX) + "-" + String(++requestID);
}

String makeBatchName(uint32_t sequence) {
  char name[48];
  snprintf(name, sizeof(name), "cs-%012llx-%08lx-%08lx",
           (unsigned long long)(ESP.getEfuseMac() & 0xFFFFFFFFFFFFULL),
           (unsigned long)bootNonce, (unsigned long)sequence);
  return String(name);
}

// Extract a JSON string literal without changing its escapes. TinyIoT stores
// our con as a string; an exact comparison fails closed if readback differs.
bool conLiteral(const String &json, String &literal) {
  int key = json.indexOf("\"con\"");
  if (key < 0) return false;
  int colon = json.indexOf(':', key + 5);
  if (colon < 0) return false;
  int start = colon + 1;
  while (start < json.length() && isspace((unsigned char)json[start])) start++;
  if (start >= json.length() || json[start] != '"') return false;
  bool escaped = false;
  for (int i = start + 1; i < json.length(); i++) {
    char ch = json[i];
    if (escaped) {
      escaped = false;
    } else if (ch == '\\') {
      escaped = true;
    } else if (ch == '"') {
      literal = json.substring(start, i + 1);
      return true;
    }
  }
  return false;
}

enum VerificationResult : int {
  VERIFY_MATCH,
  VERIFY_NOT_FOUND,
  VERIFY_UNKNOWN,
  VERIFY_MISMATCH
};

VerificationResult verifyStoredBatch(const String &batchName,
                                     const String &payload) {
  if (WiFi.status() != WL_CONNECTED) return VERIFY_UNKNOWN;
  WiFiClient client;
  HTTPClient http;
  if (!http.begin(client, String(TINYIOT_URL) + "/" + batchName)) {
    Serial.println("WARNING: CIN readback setup failed");
    return VERIFY_UNKNOWN;
  }
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.setConnectTimeout(5000);
  http.addHeader("X-M2M-Origin", ORIGINATOR);
  http.addHeader("X-M2M-RVI", "4");
  http.addHeader("X-M2M-RI", nextRequestId());
  http.addHeader("Accept", "application/json");
  uint32_t started = millis();
  int code = http.GET();
  Serial.printf("[HTTP] Verify %s: %d in %lu ms\n", batchName.c_str(),
                code, (unsigned long)(millis() - started));
  VerificationResult result = VERIFY_UNKNOWN;
  if (code == 404) {
    result = VERIFY_NOT_FOUND;
  } else if (code == 200) {
    String stored = http.getString();
    String expectedCon, storedCon;
    if (stored.indexOf("\"m2m:cin\"") >= 0 &&
        conLiteral(payload, expectedCon) && conLiteral(stored, storedCon) &&
        expectedCon == storedCon) {
      result = VERIFY_MATCH;
    } else {
      Serial.printf("ERROR: named CIN %s exists with different or unreadable content; upload paused\n",
                    batchName.c_str());
      result = VERIFY_MISMATCH;
    }
  }
  http.end();
  return result;
}

int sendToTinyIoT(
  const String &payload,
  const String &batchName
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
    nextRequestId()
  );

  unsigned long startTime = millis();

  Serial.printf(
    "[HTTP] Sending %u bytes | RI: %s\n",
    (unsigned int)payload.length(),
    batchName.c_str()
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

  // Keep one batch outside the ring until its storage outcome is known.
  String pendingPayload = "";
  String pendingName = "";
  int pendingCount = 0;
  bool hasPendingBatch = false;
  bool mustVerify = false;
  uint32_t batchSequence = 0;
  uint32_t batchAttempts = 0;
  int retryDelay = RETRY_DELAY_MS;

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
      pendingName = makeBatchName(++batchSequence);
      hasPendingBatch = true;
    }

    if (pendingName.length() == 0) {
      pendingName = makeBatchName(batchSequence);
    }
    if (pendingPayload.length() == 0 && pendingName.length() != 0) {
      pendingPayload = buildPayload(pendingCount, pendingName);
    }
    if (pendingPayload.length() == 0) {
      Serial.println("WARNING: could not allocate batch payload; batch retained for retry");
      vTaskDelay(pdMS_TO_TICKS(RETRY_DELAY_MS));
      continue;
    }

    bool confirmed = false;
    if (mustVerify) {
      VerificationResult result = verifyStoredBatch(pendingName, pendingPayload);
      if (result == VERIFY_MATCH) {
        confirmed = true;
      } else if (result == VERIFY_NOT_FOUND) {
        mustVerify = false;
      } else {
        Serial.printf("WARNING: batch %s outcome unresolved; no new POST until readback succeeds\n",
                      pendingName.c_str());
        vTaskDelay(pdMS_TO_TICKS(retryDelay));
        continue;
      }
    }

    int code = 201;
    if (!confirmed) {
      batchAttempts++;
      Serial.printf("[HTTP] Batch %s attempt %lu, t=%lu..%lu\n",
                    pendingName.c_str(), (unsigned long)batchAttempts,
                    (unsigned long)uploadBatch[0].timestamp,
                    (unsigned long)uploadBatch[pendingCount - 1].timestamp);
      code = sendToTinyIoT(pendingPayload, pendingName);
      confirmed = code == 201;
    }

    BufferStats stats = getBufferStats();
    Serial.printf("[UPLOAD] batch=%s code=%d attempts=%lu samples=%d buffered=%d/%d "
                  "peak=%d dropped=%lu i2c_failures=%lu invalid_frames=%lu "
                  "missed_slots=%lu free_heap=%u\n",
                  pendingName.c_str(), code, (unsigned long)batchAttempts,
                  pendingCount, stats.buffered, BUFFER_SIZE, stats.peak,
                  (unsigned long)stats.dropped,
                  (unsigned long)stats.readFailures,
                  (unsigned long)stats.invalidFrames,
                  (unsigned long)stats.missedSlots, (unsigned int)ESP.getFreeHeap());

    if (confirmed) {
      successfulBatches++;
      Serial.printf("[UPLOAD] Confirmed batches: %lu\n",
                    (unsigned long)successfulBatches);
      pendingPayload = "";
      pendingName = "";
      pendingCount = 0;
      hasPendingBatch = false;
      mustVerify = false;
      batchAttempts = 0;
      retryDelay = RETRY_DELAY_MS;
      vTaskDelay(pdMS_TO_TICKS(1));
    } else {
      failedAttempts++;
      mustVerify = true;
      Serial.printf("WARNING: batch %s unconfirmed (HTTP %d); failed attempts=%lu, "
                    "verify before retry in %d ms\n",
                    pendingName.c_str(), code, (unsigned long)failedAttempts,
                    retryDelay);
      vTaskDelay(pdMS_TO_TICKS(retryDelay));
      retryDelay = min(retryDelay * 2, MAX_RETRY_DELAY_MS);
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
  bootNonce = esp_random();
  Serial.printf("[UPLOAD] Boot nonce: %08lx, buffer capacity: %d samples (%d seconds)\n",
                (unsigned long)bootNonce, BUFFER_SIZE,
                BUFFER_SIZE / (1000 / SAMPLE_INTERVAL_MS));

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

