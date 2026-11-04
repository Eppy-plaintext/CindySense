# Architecture

```text
ESP32 + MPU6050 ──HTTP oneM2M content instance──> TinyIoT (configured URL)
                                               ├──> FastAPI :8000 ──JSON──> React/Vite :5173
                                               └──> standalone Cindy PT script ──recommendation──> TinyIoT
```

TinyIoT runs as a separate WSL Ubuntu installation. FastAPI can run in WSL or Windows if its configured TinyIoT URL is reachable from that process. The dashboard calls only FastAPI. Browser code must never contain oneM2M originators or other private settings.

## Sensor upload reliability and 20-minute sessions

The firmware samples every 20 ms (50 Hz), timestamps each sample at acquisition with the ESP32 millisecond clock, and converts acceleration to m/s² and angular velocity to deg/s before enqueueing it. Sensor acquisition runs on core 1 at higher task priority than networking on core 0. A critical section protects ring indices and counters; taking a batch copies at most `BATCH_SIZE` samples while holding that lock.

The ring stores 1,500 samples, or 30 seconds at 50 Hz, in addition to one pending network batch. Uploads are sequential. Healthy operation must average at least 50 samples per second: a 50-sample batch must complete and be confirmed about once per second (a 100-sample batch about once per two seconds). A POST timeout followed by readback can take several seconds, so repeated timeouts can exceed this rate and fill the ring. The finite ring then evicts its oldest sample to preserve acquisition; firmware reports the cumulative drop count and first/last evicted sample timestamps. Those bounds describe the extent of loss, while exact individual gaps would require retaining/exporting a gap event list.

Batch names are deterministic within a boot and include the ESP32 MAC, boot nonce, and sequence. An ambiguous POST is read back by name and its stored `con` string is compared exactly before the firmware accepts it or retries the same name. A 200 exact match confirms storage; 404 permits retry; timeouts and other responses remain unknown and block later POSTs. This prevents blind retries from creating duplicate named instances, but an unresolved network/server outage can keep one batch pending indefinitely. Later samples continue filling the ring during that wait.

The sketch endpoint and Wi-Fi credentials belong in ignored `firmware/config.h`; copy `firmware/config.example.h` and set the current Windows-to-WSL forwarded TinyIoT URL. The checked-in example is only a placeholder. The WSL address and forwarding rule can change, so verify the current Windows listener/port forwarding and test a GET and POST from Windows before attributing connection-refused failures to ESP32 firmware. A successful Windows request establishes that route at that moment, not ESP32-to-server reliability under sustained traffic.

A 20-minute session produces 60,000 samples. The current RAM ring cannot preserve a 20-minute network outage, and the current code has no persistent spool. Validate sustained TinyIoT ingest above 50 samples/second with margin, payload limits, and storage growth before using this for a full session. Consider flash/SD-backed append-only spooling with acknowledged deletion, session sequence numbers and explicit gap records, and a resumable uploader. Keep repetition detection on a separate local 50 Hz path so rep counts continue when uploads stall; publish derived repetition events independently and reconcile them with raw-sample session IDs later. These are future recommendations; this change does not replace a repetition algorithm.

### Real-device 20-minute test

1. Copy `firmware/config.example.h` to ignored `firmware/config.h`, enter current Wi-Fi settings, forwarded URL, and a least-privileged device originator, then compile and flash the ESP32.
2. Confirm TinyIoT responds on the configured Windows forwarded address with the existing `SensorData` container and originator. Do not change the resource tree or access policy.
3. Capture serial output for 20 minutes. Confirm 60,000 scheduled samples and 1,200 50-sample batches (or 600 batches if deliberately configured for 100), unique `rn` names, increasing batch sequences, and matching readback for ambiguous POSTs.
4. Record HTTP response code/duration, readback result, retry/backoff, RSSI, free heap, ring occupancy/peak, dropped count/timestamp bounds, I²C errors, invalid frames, and missed slots. A lossless run has no buffer drops and no unresolved batch at completion.
5. Repeat with the forwarding listener stopped briefly, then restored. Verify ambiguous outcomes do not cause blind duplicate POSTs and that backlog drains after restoration. Outages beyond the 30-second ring capacity can cause evictions and must be reported as loss.

Use `Test-NetConnection <current-forwarded-host> -Port <current-forwarded-port>` in PowerShell to validate reachability. Use local config values for authenticated oneM2M GET/POST checks; never paste credentials into shared logs.

## Current data flow

1. The firmware samples acceleration and gyroscope readings and uploads batches to the configured TinyIoT sensor container at a 50 Hz sample rate when WiFi and the sensor are available. Its URL and originator are supplied by ignored `firmware/config.h`.
2. FastAPI reads the latest content instances from the configured repetition and Cindy PT recommendation containers. It validates upstream responses and exposes JSON endpoints.
3. The dashboard polls FastAPI for health, latest repetition content, and recommendations while the relevant views are active. Its workout controls remain a local demo and do not convert the latest stored repetition value into a session score.
4. The independent Cindy PT script reads a workout summary and posts rule-based advice when deliberately run. It is not part of the FastAPI process.

## Boundaries and planned work

The current sketch sends raw sensor data; recognition and counting from MPU6050 readings still need implementation and validation. The latest oneM2M content instance is only a snapshot. A future session history needs a defined durable event source and deduplication rules. The development simulation write route exists in FastAPI but is disabled by default and is not a device ingestion route.

See [API](api.md) for implemented routes and [TinyIoT setup](tinyiot-setup.md) for resource paths and access precautions.
