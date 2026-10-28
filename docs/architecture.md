# Architecture

```text
ESP32 + MPU6050 ──HTTP oneM2M content instance──> TinyIoT :3000
                                               ├──> FastAPI :8000 ──JSON──> React/Vite :5173
                                               └──> standalone Cindy PT script ──recommendation──> TinyIoT
```

TinyIoT runs as a separate WSL Ubuntu installation. FastAPI can run in WSL or Windows if its configured TinyIoT URL is reachable from that process. The dashboard calls only FastAPI. Browser code must never contain oneM2M originators or other private settings.

## Current data flow

1. The firmware samples acceleration and gyroscope readings and posts JSON to the configured TinyIoT sensor container every three seconds when WiFi and the sensor are available. Its URL and originator are supplied by ignored `firmware/config.h`.
2. FastAPI reads the latest content instances from the configured repetition and Cindy PT recommendation containers. It validates upstream responses and exposes JSON endpoints.
3. The dashboard polls FastAPI for health, latest repetition content, and recommendations while the relevant views are active. Its workout controls remain a local demo and do not convert the latest stored repetition value into a session score.
4. The independent Cindy PT script reads a workout summary and posts rule-based advice when deliberately run. It is not part of the FastAPI process.

## Boundaries and planned work

The current sketch sends raw sensor data; recognition and counting from MPU6050 readings still need implementation and validation. The latest oneM2M content instance is only a snapshot. A future session history needs a defined durable event source and deduplication rules. The development simulation write route exists in FastAPI but is disabled by default and is not a device ingestion route.

See [API](api.md) for implemented routes and [TinyIoT setup](tinyiot-setup.md) for resource paths and access precautions.
