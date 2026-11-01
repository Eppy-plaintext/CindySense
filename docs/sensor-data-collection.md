# Recording labelled MPU6050 data

The recorder in [collect_sensor_data.py](../tools/collect_sensor_data.py) reads TinyIoT only. It uses `GET` requests with `X-M2M-Origin`, `X-M2M-RI`, `X-M2M-RVI: 4`, and `Accept: application/json`. It never creates or changes a oneM2M resource.

## Configure and probe

Copy [collector.example.json](../tools/collector.example.json) to `tools/collector.local.json`. Set `origin` to an originator authorized to retrieve the SensorData container and its CINs. Use a least-privilege read identity. Set `base_url` to the address reachable from the machine running Python. From Windows, `http://localhost:3000/TinyIoT` works only when WSL forwards that port; otherwise use the reachable WSL address. Set `device_id` if other devices share the container. Both `tools/collector.local.json` and `tools/collector.json`, plus `data/recordings/`, are Git ignored.

The script uses only the Python standard library. From the repository root:

```powershell
Copy-Item tools/collector.example.json tools/collector.local.json
# Edit tools/collector.local.json: set origin and confirm the URL and device ID.
python tools/collect_sensor_data.py --config tools/collector.local.json probe
```

Run `probe` before recording. It verifies the container listing and attempts to parse its newest CIN. A 403 means the origin lacks read privilege. If the listing succeeds but parsing fails, inspect the actual CIN format before recording.

## Consistent recording protocol

Keep the sensor in the same chest position and orientation for every session.
Start collection before moving. For each squat trial, stand still for five
seconds, perform exactly five slow squats (about two seconds down, one second
paused at the bottom, and two seconds up), then stand still for five seconds.
This is a 35-second recording. Do not press Ctrl+C early; the duration includes
the full post-exercise rest. The collector adds its configured final upload
settle time before saving the CSV.

```powershell
python tools/collect_sensor_data.py --config tools/collector.local.json record --label squat --session-id squat-slow-01 --duration 35
python tools/replay_squat_stream.py data/recordings/squat-slow-01.csv --calibration-mode initial_standing --standing-calibration-seconds 5
```

Record a separate rest-only validation trial with the sensor in the same
position. Remain standing and still for the full 35 seconds; do not perform
squats. Use a distinct session ID:

```powershell
python tools/collect_sensor_data.py --config tools/collector.local.json record --label rest --session-id rest-validation-01 --duration 35
python tools/replay_squat_stream.py data/recordings/rest-validation-01.csv --calibration-mode initial_standing --standing-calibration-seconds 5
```

Each command creates a new CSV and refuses to overwrite an existing session ID.
Keep the final standing rest through the collector's upload settle wait.

The CSV contains `session_id`, `label`, `device_id`, raw `sample_t`, normalized `sample_time_seconds`, `cin_created_at`, `cin_ri`, and `ax`, `ay`, `az`, `gx`, `gy`, `gz`. Set `timestamp_unit` to `ms` or `s` to match numeric `t`. Numeric timestamps are not assumed to be UTC; they may be milliseconds since ESP32 boot. Timezone-aware ISO 8601 `t` values are also accepted. `cin_created_at` is the server's creation timestamp and may lag the sample time.

The recorder checks for batches other than 50 samples, unexpected sample rate, missing fields, malformed `con` JSON, repeated timestamps, and gaps greater than 1.5 times the expected 20 ms interval. It skips malformed CINs and prints warnings. It checks collected samples in original acquisition order before sorting. If a device timestamp moves backwards or the firmware boot nonce changes, it reports the adjacent samples, saves the untouched acquisition order as `<session-id>.invalid.csv` for diagnosis, and refuses to write the normal replay CSV. Do not replay the invalid file as a valid session. Start a new session after a reboot; do not combine samples from different boots.

## Listing method and limits

This TinyIoT checkout's retrieval implementation supports `rcn=6` on the container and returns `m2m:rrl.rrf` child references. The recorder requests `ty=4` (CIN), `lvl=1`, `lim`, and `ofst`, then retrieves each new CIN by its own URI. Returned references can include the `TinyIoT` CSEBase prefix, omit it, or be an absolute URL on the configured server. The reader normalizes these to one path beneath the configured container and rejects references to another host or container. It reads the full retained listing at startup and on each poll, then retrieves only references not seen at startup or already processed. Failed retrievals stay pending while their CIN remains listed. Each HTTP request retries transient 404, 408, 429, and 5xx failures up to three attempts. The recorder uses sample timestamps to order rows, remove duplicate device/timestamp pairs, and report gaps. It does not use `/la`, which exposes only one CIN. The final poll runs even when `settle_seconds` is zero.

The checked-in [cindysense.ino](../firmware/cindysense.ino) sends 50-sample batches with `deviceId`, `sampleRateHz`, and `samples`, which the collector accepts. It also includes a `batchId`; the collector ignores that extra field. The sketch retains a failed batch and uses a stable CIN resource name while verifying uncertain POST outcomes by readback. Watch its Serial Monitor counters for dropped samples, I2C failures, missed sample slots, buffer occupancy, and retries. Its 1,500-sample ring holds about 30 seconds at 50 Hz; longer outages can still cause loss. A newly flashed sketch cannot restore samples that were lost before flashing.

Listing is not a server-side snapshot. If CINs expire or are deleted while pages are being read, offsets can shift and a CIN may be missed. The collector warns when consecutive discovery windows do not overlap or a pending CIN disappears. Poll every second and review warnings and row counts. Data no longer retrievable from TinyIoT cannot be recovered by this tool. On 2026-11-01, a live read-only check showed 15 retained CINs, `mni=100`, and `cbs` near 63 KB. A CIN retrieved successfully returned HTTP 404 five seconds later while `cni` remained 15. This indicates an effective byte-size retention limit despite `mni=100`; the container did not expose an explicit `mbs`. Both a literal comma and `%2C` retrieved the same current CIN successfully, so an encoded comma alone did not explain the 404s. The live listing was newest first and honored offsets; the recorder deduplicates overlapping pages. A 45-second recording needs 45 consecutive uploads of 50 samples, and the retention window leaves only about 15 seconds to catch up after a polling outage. A separate read-only audit of `squat_slow_001.csv` found five consecutive CIN resource IDs, each with 50 valid samples and no duplicate timestamps. The two gaps are between CINs, not inside them. Those five CINs now return HTTP 404, so the original stored payloads cannot currently be used to reconstruct the gaps. Their consecutive IDs and the CSV's complete batches indicate that the missing sample timestamps were absent before collection; the exact sensor-side cause cannot be established from the retained data.
