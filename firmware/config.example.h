#pragma once

// Copy to config.h in this folder and fill in local values. config.h is ignored by Git.
#define CINDYSENSE_WIFI_SSID "YOUR_WIFI_SSID"
#define CINDYSENSE_WIFI_PASSWORD "YOUR_WIFI_PASSWORD"
#define CINDYSENSE_TINYIOT_URL "http://YOUR_TINYIOT_HOST:3001/TinyIoT/CindySense_AE/SensorData"
#define CINDYSENSE_ORIGINATOR "YOUR_DEVICE_ORIGINATOR"

// Optional SensorData posting. Local detection and repetition event publishing
// work when this remains disabled.
#define CINDYSENSE_RAW_UPLOADS 0
