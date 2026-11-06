
#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <esp_system.h>
#include <ctype.h>
#include <math.h>
#include "config.h"
#include "batch_verification.h"
#include "squat_detector.h"
#include "exercise_detector.h"
#include <time.h>

#ifndef CINDYSENSE_RAW_UPLOADS
#define CINDYSENSE_RAW_UPLOADS 0
#endif
#ifndef CINDYSENSE_SENSOR_VERBOSE
#define CINDYSENSE_SENSOR_VERBOSE 0
#endif
#define CINDYSENSE_FIRMWARE_VERSION "0.3.0-three-exercises"

// Arduino generates function prototypes before the type definitions below.
struct BufferStats;

// =====================================
// 1. CONFIGURATION
// =====================================

// Keep Wi-Fi credentials in the ignored local config.h.
// TinyIoT URL and originator are configured below.
const char* WIFI_SSID = CINDYSENSE_WIFI_SSID;
const char* WIFI_PASSWORD = CINDYSENSE_WIFI_PASSWORD;
const char* TINYIOT_URL = CINDYSENSE_TINYIOT_URL;
const char* ORIGINATOR = CINDYSENSE_ORIGINATOR;

// MPU6050 I2C address
const uint8_t MPU_ADDR = 0x68;

const int SAMPLE_INTERVAL_MS = 20;
const int BATCH_SIZE = 100;
const int BUFFER_SIZE = 1500;

// HTTP retry configuration
const int HTTP_TIMEOUT_MS = 4000;
const int RETRY_DELAY_MS = 2000;
const int MAX_RETRY_DELAY_MS = 8000;

// =====================================
// 2. SENSOR STRUCTURE
// =====================================

// Raw acquisition storage exists only in builds that explicitly enable uploads.
#if CINDYSENSE_RAW_UPLOADS
SensorSample sampleBuffer[BUFFER_SIZE];
SensorSample uploadBatch[BATCH_SIZE];

int bufferHead = 0;
int bufferTail = 0;
int bufferCount = 0;

uint32_t droppedSamples = 0;
uint32_t firstDroppedTimestamp = 0;
uint32_t lastDroppedTimestamp = 0;
int peakBufferCount = 0;
#endif
uint32_t i2cReadFailures = 0;
uint32_t invalidAccelerationFrames = 0;
uint32_t missedSampleSlots = 0;

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

static SquatDetector squatDetector;
static VerticalMotionDetector motionDetector;
static volatile ExerciseMode requestedMode=EX_SQUAT;
static volatile bool localStressTest=false;
static ExerciseMode activeMode=EX_SQUAT;
static uint32_t totalRepetitions=0, eventSequence=0, modeRepetitions[3]={0,0,0};
static uint32_t stressStarted=0, stressHeapStart=0, stressMinHeap=0;
static volatile uint32_t recordUntil=0;
static const int RECORD_CAPACITY=128;
static SensorSample recordQueue[RECORD_CAPACITY];
static int recordHead=0,recordTail=0,recordCount=0;
static uint32_t recordDrops=0;
static portMUX_TYPE recordMutex=portMUX_INITIALIZER_UNLOCKED;
static const int EVENT_QUEUE_CAPACITY = 1024;
struct QueuedRepetition { uint32_t sequence; uint32_t repetitions; uint32_t timestampMs; uint32_t epochSeconds; ExerciseMode exercise; };
static QueuedRepetition eventQueue[EVENT_QUEUE_CAPACITY];
static int eventHead=0,eventTail=0,eventCount=0;
static uint32_t eventQueueOverflows=0,confirmedEvents=0,eventRetries=0;
static char eventReadback[2049];
static portMUX_TYPE eventMutex=portMUX_INITIALIZER_UNLOCKED;
static char eventSessionId[32];

static bool enqueueEvent(uint32_t sequence,uint32_t repetition,uint32_t timestampMs,ExerciseMode exercise) {
  bool ok=false;
  time_t wall=time(nullptr);
  portENTER_CRITICAL(&eventMutex);
  if(eventCount<EVENT_QUEUE_CAPACITY){
    QueuedRepetition &slot=eventQueue[eventHead];
    slot.sequence=sequence;slot.repetitions=repetition;slot.timestampMs=timestampMs;slot.exercise=exercise;
    slot.epochSeconds=wall>1700000000?(uint32_t)wall:0;
    eventHead=(eventHead+1)%EVENT_QUEUE_CAPACITY;eventCount++;ok=true;
  } else eventQueueOverflows++;
  portEXIT_CRITICAL(&eventMutex);
  return ok;
}
static int queuedEventCount(){portENTER_CRITICAL(&eventMutex);int n=eventCount;portEXIT_CRITICAL(&eventMutex);return n;}
static bool peekEvent(QueuedRepetition &e){portENTER_CRITICAL(&eventMutex);bool ok=eventCount>0;if(ok)e=eventQueue[eventTail];portEXIT_CRITICAL(&eventMutex);return ok;}
static void confirmEvent(uint32_t sequence){portENTER_CRITICAL(&eventMutex);if(eventCount&&eventQueue[eventTail].sequence==sequence){eventTail=(eventTail+1)%EVENT_QUEUE_CAPACITY;eventCount--;confirmedEvents++;}portEXIT_CRITICAL(&eventMutex);}
static void setEventEpoch(uint32_t sequence,uint32_t epoch){portENTER_CRITICAL(&eventMutex);if(eventCount&&eventQueue[eventTail].sequence==sequence&&!eventQueue[eventTail].epochSeconds)eventQueue[eventTail].epochSeconds=epoch;portEXIT_CRITICAL(&eventMutex);}

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

bool initializeMPU(bool waitForSettle = true, bool printStatus = true) {
  if (printStatus) Serial.println("Checking MPU6050...");

  uint8_t id = 0;

  if (!readRegisters(0x75, &id, 1)) {
    if (printStatus) Serial.println(
      "ERROR: WHO_AM_I read failed"
    );
    return false;
  }

  if (printStatus) Serial.printf(
    "WHO_AM_I: 0x%02X\n",
    id
  );

  if (id != 0x68) {
    if (printStatus) Serial.println(
      "ERROR: Unexpected sensor ID"
    );
    return false;
  }

  // Wake MPU6050
  if (!writeRegister(0x6B, 0x01)) {
    if (printStatus) Serial.println("ERROR: Wake-up failed");
    return false;
  }

  // Enable accelerometer and gyroscope
  if (!writeRegister(0x6C, 0x00)) {
    if (printStatus) Serial.println("ERROR: Sensor enable failed");
    return false;
  }

  // Digital low-pass filter
  if (!writeRegister(0x1A, 0x03)) {
    if (printStatus) Serial.println("ERROR: Filter setup failed");
    return false;
  }

  // Accelerometer range: +/- 4g
  if (!writeRegister(0x1C, 0x08)) {
    if (printStatus) Serial.println("ERROR: Accelerometer setup failed");
    return false;
  }

  // Gyroscope range: +/- 500 deg/s
  if (!writeRegister(0x1B, 0x08)) {
    if (printStatus) Serial.println("ERROR: Gyroscope setup failed");
    return false;
  }

  if (waitForSettle) delay(200);

  if (printStatus) Serial.println(
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

// =====================================
// 7. BUFFER OPERATIONS
// =====================================

#if CINDYSENSE_RAW_UPLOADS
// Add new sensor reading to ring buffer
bool storeSample(const SensorSample &s) {
  portENTER_CRITICAL(&bufferMutex);

  bool overflow = bufferCount >= BUFFER_SIZE;
  if (overflow) {
    // Remove oldest sample on overflow
    uint32_t evictedTimestamp = sampleBuffer[bufferTail].timestamp;
    if (droppedSamples == 0) firstDroppedTimestamp = evictedTimestamp;
    lastDroppedTimestamp = evictedTimestamp;
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
#endif

struct BufferStats {
  int buffered;
  int peak;
  uint32_t dropped;
  uint32_t firstDroppedAt;
  uint32_t lastDroppedAt;
  uint32_t readFailures;
  uint32_t invalidFrames;
  uint32_t missedSlots;
};

BufferStats getBufferStats() {
  BufferStats stats;
  portENTER_CRITICAL(&bufferMutex);
#if CINDYSENSE_RAW_UPLOADS
  stats.buffered = bufferCount;
  stats.peak = peakBufferCount;
  stats.dropped = droppedSamples;
  stats.firstDroppedAt = firstDroppedTimestamp;
  stats.lastDroppedAt = lastDroppedTimestamp;
#else
  stats.buffered = stats.peak = 0;
  stats.dropped = stats.firstDroppedAt = stats.lastDroppedAt = 0;
#endif
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
  bool recoveryWaiting = false;
  uint32_t recoveryStarted = 0;
  uint32_t lastAttempt = 0;
  uint32_t lastDiagnostics = millis();
  uint32_t lastFrequencyReport=millis();
  uint32_t frequencySamples=0;
  uint32_t reportedEventOverflows=0;
  uint32_t reportedTotal=0;

  while (true) {
    if (activeMode != requestedMode) {
      activeMode=requestedMode;
      squatDetector.recalibrate(); motionDetector.reset(activeMode);
      Serial.printf("[MODE] %s; hold start posture still for 4 seconds\n",exerciseName(activeMode));
    }
    uint32_t now = millis();
    if (lastAttempt && now - lastAttempt >= 2*SAMPLE_INTERVAL_MS) {
      uint32_t missed = (now - lastAttempt) / SAMPLE_INTERVAL_MS - 1;
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
      // Detection is synchronous, fixed-state work on core 1; it never uses Wi-Fi/HTTP.
      bool detected=false;
      if (activeMode==EX_SQUAT) { SquatEvent event; detected=squatDetector.process(s,event); }
      else detected=motionDetector.process(s);
      if (detected) {
        uint32_t exerciseCount=++modeRepetitions[activeMode];
        ++totalRepetitions;
        if (!enqueueEvent(++eventSequence,exerciseCount,s.timestamp,activeMode))
          Serial.printf("[EVENT] ERROR queue full; lost sequence=%lu %s rep=%lu\n",
                        (unsigned long)eventSequence,exerciseName(activeMode),(unsigned long)exerciseCount);
      }
      if (recordUntil && (int32_t)(recordUntil-s.timestamp)>0) {
        portENTER_CRITICAL(&recordMutex);
        if(recordCount<RECORD_CAPACITY){recordQueue[recordHead]=s;recordHead=(recordHead+1)%RECORD_CAPACITY;recordCount++;}
        else recordDrops++;
        portEXIT_CRITICAL(&recordMutex);
      }
      frequencySamples++;
#if CINDYSENSE_RAW_UPLOADS
      if (storeSample(s)) {
        BufferStats stats = getBufferStats();
        if (stats.dropped == 1 || stats.dropped % BATCH_SIZE == 0) {
          Serial.printf("WARNING: sensor buffer overflow; dropped %lu samples, buffered %d/%d\n",
                        (unsigned long)stats.dropped, stats.buffered, BUFFER_SIZE);
        }
      }
#endif

      sampleCounter++;

#if CINDYSENSE_SENSOR_VERBOSE
      // Optional detailed diagnostics; disabled by default to protect the 20 ms loop.
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
#endif
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
        if (consecutiveInvalidAcceleration == 1) {
          Serial.println("ERROR: MPU6050 returned zero acceleration on all axes");
        }
        if (!recoveryWaiting && consecutiveInvalidAcceleration >= 5 &&
            (lastRecovery == 0 || millis() - lastRecovery >= 5000)) {
          lastRecovery = millis();
          Serial.println("[MPU] recovery reset requested (nonblocking)");
          recoveryWaiting = writeRegister(0x6B, 0x80);
          recoveryStarted = millis();
          if (!recoveryWaiting) Serial.println("[MPU] recovery reset write failed");
          consecutiveInvalidAcceleration = 0;
        }
      } else {
        consecutiveInvalidAcceleration = 0;
        consecutiveI2cFailures++;
        if (consecutiveI2cFailures == 1) {
          Serial.printf("WARNING: MPU6050 I2C read failed (%lu consecutive)\n",
                        (unsigned long)consecutiveI2cFailures);
        }
        if (!recoveryWaiting && consecutiveI2cFailures >= 5 &&
            (lastRecovery == 0 || millis()-lastRecovery >= 5000)) {
          lastRecovery=millis();
          recoveryWaiting=writeRegister(0x6B,0x80);
          recoveryStarted=millis();
          Serial.printf("[MPU] I2C recovery reset %s\n",recoveryWaiting?"requested":"failed");
          consecutiveI2cFailures=0;
        }
      }
    }

    // Let the sensor reset while this task continues its regular 20 ms reads.
    // Register setup skips the old 200 ms settle delay when performed here.
    if (recoveryWaiting && millis() - recoveryStarted >= 100) {
      recoveryWaiting = false;
      if (initializeMPU(false, false)) {
        Serial.println("[MPU] recovery configuration complete");
      } else {
        Serial.println("[MPU] recovery configuration failed");
      }
    }

    if (millis() - lastDiagnostics >= 5000) {
      lastDiagnostics = millis();
      BufferStats stats = getBufferStats();
      uint32_t elapsed=millis()-lastFrequencyReport;
#if CINDYSENSE_RAW_UPLOADS
      Serial.printf("[SENSOR] hz=%lu buffered=%d/%d dropped=%lu i2c=%lu invalid=%lu missed=%lu\n",
                    elapsed?(unsigned long)(frequencySamples*1000UL/elapsed):0,
                    stats.buffered,BUFFER_SIZE,(unsigned long)stats.dropped,
                    (unsigned long)stats.readFailures,(unsigned long)stats.invalidFrames,
                    (unsigned long)stats.missedSlots);
#else
      Serial.printf("[SENSOR] hz=%lu i2c=%lu inv=%lu miss=%lu\n",
                    elapsed?(unsigned long)(frequencySamples*1000UL/elapsed):0,
                    (unsigned long)stats.readFailures,(unsigned long)stats.invalidFrames,
                    (unsigned long)stats.missedSlots);
#endif
      Serial.printf("[COUNT] mode=%s pullup=%lu pushup=%lu squat=%lu total=%lu q=%d confirmed=%lu retries=%lu overflow=%lu cal=%d heap=%u\n",
                    exerciseName(activeMode),(unsigned long)modeRepetitions[EX_PULLUP],
                    (unsigned long)modeRepetitions[EX_PUSHUP],(unsigned long)modeRepetitions[EX_SQUAT],
                    (unsigned long)totalRepetitions,queuedEventCount(),(unsigned long)confirmedEvents,
                    (unsigned long)eventRetries,(unsigned long)eventQueueOverflows,
                    activeMode==EX_SQUAT?squatDetector.calibrated():motionDetector.calibrated(),
                    (unsigned)ESP.getFreeHeap());
      if(totalRepetitions>reportedTotal){Serial.printf("[REP] %s total=%lu\n",exerciseName(activeMode),(unsigned long)totalRepetitions);reportedTotal=totalRepetitions;}
      if(eventQueueOverflows!=reportedEventOverflows){Serial.printf("[EVENT] ERROR event queue overflow=%lu\n",(unsigned long)eventQueueOverflows);reportedEventOverflows=eventQueueOverflows;}
      if(localStressTest){
        uint32_t heap=ESP.getFreeHeap();if(heap<stressMinHeap)stressMinHeap=heap;
        if(millis()-stressStarted>=1200000UL){
          localStressTest=false;
          Serial.printf("[STRESS] COMPLETE 20min heap_start=%lu heap_min=%lu heap_end=%lu i2c=%lu missed=%lu total=%lu queue_overflow=%lu record_drops=%lu\n",
            (unsigned long)stressHeapStart,(unsigned long)stressMinHeap,(unsigned long)heap,
            (unsigned long)stats.readFailures,(unsigned long)stats.missedSlots,(unsigned long)totalRepetitions,
            (unsigned long)eventQueueOverflows,(unsigned long)recordDrops);
        }
      }
      frequencySamples=0;lastFrequencyReport=millis();
    }

    vTaskDelayUntil(&lastWake, period);
  }
}

// =====================================
// 9. BUILD ONEM2M JSON PAYLOAD
// =====================================

#if CINDYSENSE_RAW_UPLOADS
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
    SensorSample &s = uploadBatch[i];
    char sampleJson[160];
    int written = snprintf(sampleJson, sizeof(sampleJson),
                           "%s{\"t\":%lu,\"ax\":%.3f,\"ay\":%.3f,\"az\":%.3f,\"gx\":%.3f,\"gy\":%.3f,\"gz\":%.3f}",
                           i ? "," : "", (unsigned long)s.timestamp,
                           s.ax, s.ay, s.az, s.gx, s.gy, s.gz);
    if (written < 0 || written >= (int)sizeof(sampleJson)) return "";
    data += sampleJson;
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
#endif

// =====================================
// 10. SEND TO TINYIOT
// =====================================

String nextRequestId() {
  return "sensor-" + String(bootNonce, HEX) + "-" + String(++requestID);
}

#if CINDYSENSE_RAW_UPLOADS
String makeBatchName(uint32_t sequence) {
  char name[48];
  snprintf(name, sizeof(name), "cs-%012llx-%08lx-%08lx",
           (unsigned long long)(ESP.getEfuseMac() & 0xFFFFFFFFFFFFULL),
           (unsigned long)bootNonce, (unsigned long)sequence);
  return String(name);
}

static char decodedVerificationContent[24000];

VerificationResult verifyStoredBatch(const String &batchName, int sampleCount) {
  if (WiFi.status() != WL_CONNECTED) return VERIFY_UNKNOWN;
  WiFiClient client;
  HTTPClient http;
  if (!http.begin(client, String(TINYIOT_URL) + "/" + batchName)) {
    Serial.println("WARNING: CIN readback setup failed");
    return VERIFY_UNKNOWN;
  }
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.setConnectTimeout(1500);
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
    result = verifyBatchHttpResponse(
        code, stored.c_str(), stored.length(), batchName.c_str(),
        uploadBatch, (size_t)sampleCount, decodedVerificationContent,
        sizeof(decodedVerificationContent));
    if (result == VERIFY_MISMATCH) {
      Serial.printf("ERROR: named CIN %s exists with different or unreadable content; upload paused\n",
                    batchName.c_str());
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
  http.setConnectTimeout(1500);
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

  // Diagnostics: response duration, queued samples, and free ESP32 heap.
  Serial.printf(
    "[PERF] status=%d duration=%lu ms buffer=%d/%d heap=%u\n",
    code,
    duration,
    getBufferCount(),
    BUFFER_SIZE,
    (unsigned int)ESP.getFreeHeap()
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
#endif


// =====================================
// 11. NETWORK TASK
// =====================================

static String repetitionContainerUrl(){
  String url(TINYIOT_URL);int slash=url.lastIndexOf('/');
  if(slash>=0)url=url.substring(0,slash+1)+"RepetitionCount";
  return url;
}

static bool deliverRepetitionEvent(const QueuedRepetition &event,bool &needsVerify,int &retryMs){
  if(WiFi.status()!=WL_CONNECTED)return false;
  time_t now=time(nullptr);
  if(now<1700000000){
    static uint32_t lastClockWarning=0;
    if(!lastClockWarning || millis()-lastClockWarning>30000){
      Serial.println("[EVENT] waiting for NTP UTC time; events remain queued");lastClockWarning=millis();
    }
    return false;
  }
  time_t eventTime=event.epochSeconds?event.epochSeconds:now-((millis()-event.timestampMs)/1000);
  setEventEpoch(event.sequence,(uint32_t)eventTime);
  struct tm utc;gmtime_r(&eventTime,&utc);
  char timestamp[32],eventId[40],resource[48],payload[560];
  strftime(timestamp,sizeof(timestamp),"%Y-%m-%dT%H:%M:%SZ",&utc);
  uint64_t mac=ESP.getEfuseMac()&0xFFFFFFFFFFFFULL;
  snprintf(eventId,sizeof(eventId),"%08lx-%04lx-4%03lx-8%03lx-%012llx",
           (unsigned long)bootNonce,(unsigned long)((event.sequence>>12)&0xffff),
           (unsigned long)(event.sequence&0xfff),(unsigned long)(mac&0xfff),
           (unsigned long long)(mac&0xffffffffffffULL));
  snprintf(resource,sizeof(resource),"esp-%08lx-%08lx",(unsigned long)bootNonce,(unsigned long)event.sequence);
  unsigned quota=event.exercise==EX_PULLUP?5:event.exercise==EX_PUSHUP?10:15;
  int payloadLength=snprintf(payload,sizeof(payload),"{\"m2m:cin\":{\"rn\":\"%s\",\"con\":\"{\\\"eventId\\\":\\\"%s\\\",\\\"eventSequence\\\":%lu,\\\"sessionId\\\":\\\"%s\\\",\\\"exercise\\\":\\\"%s\\\",\\\"repetitions\\\":%lu,\\\"round\\\":%lu,\\\"timestamp\\\":\\\"%s\\\",\\\"source\\\":\\\"mpu6050\\\"}\"}}",
           resource,eventId,(unsigned long)event.sequence,eventSessionId,exerciseName(event.exercise),
           (unsigned long)event.repetitions,(unsigned long)((event.repetitions-1)/quota+1),timestamp);
  if(payloadLength<0 || payloadLength>=(int)sizeof(payload)){Serial.println("[EVENT] payload too large; queued for retry");return false;}
  String url=repetitionContainerUrl()+"/"+resource;
  WiFiClient client;HTTPClient http;http.setTimeout(HTTP_TIMEOUT_MS);http.setConnectTimeout(1500);
  if(needsVerify){
    if(!http.begin(client,url))return false;
    http.addHeader("X-M2M-Origin",ORIGINATOR);http.addHeader("X-M2M-RVI","4");http.addHeader("X-M2M-RI",nextRequestId());
    int code=http.GET();int length=http.getSize();int read=0;
    if(code==200 && length>0 && length<(int)sizeof(eventReadback)){
      read=http.getStreamPtr()->readBytes(eventReadback,length);
      eventReadback[read]=0;
    }
    http.end();
    Serial.printf("[EVENT] verify seq=%lu HTTP=%d bytes=%d\n",(unsigned long)event.sequence,code,read);
    if(code==200 && read==length && strstr(eventReadback,eventId) &&
       strstr(eventReadback,eventSessionId) && strstr(eventReadback,exerciseName(event.exercise)) &&
       strstr(eventReadback,"mpu6050")){needsVerify=false;confirmEvent(event.sequence);return true;}
    if(code!=404){eventRetries++;retryMs=min(retryMs*2,MAX_RETRY_DELAY_MS);return false;}
    needsVerify=false;
  }
  if(!http.begin(client,repetitionContainerUrl()))return false;
  http.addHeader("Content-Type","application/json;ty=4");http.addHeader("Accept","application/json");
  http.addHeader("X-M2M-Origin",ORIGINATOR);http.addHeader("X-M2M-RVI","4");http.addHeader("X-M2M-RI",nextRequestId());
  int code=http.POST((uint8_t*)payload,strlen(payload));http.end();
  Serial.printf("[EVENT] POST seq=%lu %s HTTP=%d\n",(unsigned long)event.sequence,exerciseName(event.exercise),code);
  if(code==201){confirmEvent(event.sequence);retryMs=RETRY_DELAY_MS;return true;}
  needsVerify=true;eventRetries++;retryMs=min(retryMs*2,MAX_RETRY_DELAY_MS);return false;
}

void networkTask(void* parameter) {
  unsigned long lastReconnect = 0;

#if CINDYSENSE_RAW_UPLOADS
  // Keep one batch outside the ring until its storage outcome is known.
  String pendingPayload = "";
  String pendingName = "";
  int pendingCount = 0;
  bool hasPendingBatch = false;
  bool mustVerify = false;
  uint32_t batchSequence = 0;
  uint32_t batchAttempts = 0;
  int retryDelay = RETRY_DELAY_MS;
#endif
  bool eventNeedsVerify=false;int eventRetryDelay=RETRY_DELAY_MS;uint32_t eventRetryAt=0;

  while (true) {
    if(localStressTest){vTaskDelay(pdMS_TO_TICKS(500));continue;}

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

    QueuedRepetition queuedEvent;
    if(peekEvent(queuedEvent)){
      if(!eventRetryAt||millis()>=eventRetryAt){
        bool delivered=deliverRepetitionEvent(queuedEvent,eventNeedsVerify,eventRetryDelay);
        if(!delivered)eventRetryAt=millis()+eventRetryDelay;
        else eventRetryAt=0;
      }
      // Raw uploads remain available by opt-in; event queue gets first service.
    }
#if CINDYSENSE_RAW_UPLOADS
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
      VerificationResult result = verifyStoredBatch(pendingName, pendingCount);
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
                  "peak=%d dropped=%lu dropped_ts=%lu..%lu i2c_failures=%lu invalid_frames=%lu "
                  "missed_slots=%lu free_heap=%u\n",
                  pendingName.c_str(), code, (unsigned long)batchAttempts,
                  pendingCount, stats.buffered, BUFFER_SIZE, stats.peak,
                  (unsigned long)stats.dropped,
                  (unsigned long)stats.firstDroppedAt,
                  (unsigned long)stats.lastDroppedAt,
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
#else
    vTaskDelay(pdMS_TO_TICKS(100));
#endif
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
  Serial.printf("[FW] version=%s RAW_UPLOADS=%d LOCAL_DETECTOR=1\n",
                CINDYSENSE_FIRMWARE_VERSION,CINDYSENSE_RAW_UPLOADS);

  // Initialize I2C
  Wire.begin(21, 22);
  Wire.setClock(100000);
  Wire.setTimeOut(10);

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
  snprintf(eventSessionId,sizeof(eventSessionId),"esp32-%08lx",(unsigned long)bootNonce);
  configTime(0,0,"pool.ntp.org","time.nist.gov");
#if CINDYSENSE_RAW_UPLOADS
  Serial.printf("[UPLOAD] Boot nonce: %08lx, buffer capacity: %d samples (%d seconds); batch=%d\n",
                (unsigned long)bootNonce, BUFFER_SIZE,
                BUFFER_SIZE / (1000 / SAMPLE_INTERVAL_MS), BATCH_SIZE);
#else
  Serial.printf("[EVENT] Session %s; raw sensor buffering and uploads disabled\n",eventSessionId);
#endif

  Serial.println("Connecting to Wi-Fi...");
  Serial.println("Commands: PULLUP, PUSHUP, SQUAT, RECORD, STRESS, STATUS");
  Serial.println("[EVENT] RAM-only queue is lost on reboot; no persistent event storage");

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
  static char command[24];static uint8_t commandLength=0;
  static bool recordFinished=true;
  while(Serial.available()){
    char ch=(char)Serial.read();
    if(ch=='\r'||ch=='\n'){
      command[commandLength]=0;
      if(!strcmp(command,"PULLUP"))requestedMode=EX_PULLUP;
      else if(!strcmp(command,"PUSHUP"))requestedMode=EX_PUSHUP;
      else if(!strcmp(command,"SQUAT"))requestedMode=EX_SQUAT;
      else if(!strcmp(command,"RECORD")){
        portENTER_CRITICAL(&recordMutex);
        recordHead=recordTail=recordCount=0;recordDrops=0;
        portEXIT_CRITICAL(&recordMutex);
        recordUntil=millis()+30000;
        recordFinished=false;
        Serial.println("[RECORD] 30 seconds CSV: hold starting posture 4 seconds, then perform 5 controlled reps");
        Serial.println("t,ax,ay,az,gx,gy,gz");
      } else if(!strcmp(command,"STRESS")){
        stressStarted=millis();stressHeapStart=stressMinHeap=ESP.getFreeHeap();localStressTest=true;
        Serial.println("[STRESS] 20-minute local sampling/detection run started; network paused");
      } else if(!strcmp(command,"STATUS")){
        Serial.printf("[STATUS] mode=%s total=%lu queued=%d confirmed=%lu retries=%lu overflow=%lu heap=%u\n",
          exerciseName(activeMode),(unsigned long)totalRepetitions,queuedEventCount(),
          (unsigned long)confirmedEvents,(unsigned long)eventRetries,
          (unsigned long)eventQueueOverflows,(unsigned)ESP.getFreeHeap());
      } else if(commandLength) Serial.println("[CMD] Unknown command");
      commandLength=0;
    } else if(commandLength<sizeof(command)-1) command[commandLength++]=(char)toupper((unsigned char)ch);
  }
  SensorSample row;bool haveRow=false;
  portENTER_CRITICAL(&recordMutex);
  if(recordCount){row=recordQueue[recordTail];recordTail=(recordTail+1)%RECORD_CAPACITY;recordCount--;haveRow=true;}
  portEXIT_CRITICAL(&recordMutex);
  if(haveRow)Serial.printf("%lu,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f\n",
    (unsigned long)row.timestamp,row.ax,row.ay,row.az,row.gx,row.gy,row.gz);
  if(!recordFinished && (int32_t)(millis()-recordUntil)>=0 && !recordCount){
    recordFinished=true;recordUntil=0;
    Serial.printf("[RECORD] complete dropped=%lu (repeat if nonzero)\n",(unsigned long)recordDrops);
  }

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

  delay(recordCount?1:20);
}

