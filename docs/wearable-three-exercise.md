# CindySense chest wearable quick test

The ESP32 counts locally at 50 Hz. Serial commands (115200 baud, newline):
`PULLUP`, `PUSHUP`, `SQUAT`, `STATUS`, `RECORD`, `STRESS`.
The default mode is `SQUAT`. After changing mode, hold the exercise's starting
posture still for at least four seconds (horizontal plank for `PUSHUP`, hanging
start for `PULLUP`, standing for `SQUAT`). The movement phase resets, but session
and per-exercise totals continue. The sensor must stay fixed on the upper chest.
Pull-up and push-up detection uses vertical dynamic acceleration projected onto
the measured gravity direction, leaky movement integration, return to the
starting position, duration, quiet return, and cooldown. A pull-up starts with
upward acceleration; a push-up starts with downward acceleration. Each mode
checks tilt against its calibrated starting posture, with a tighter push-up
limit. This does not independently prove that the posture was horizontal. The existing
squat detector uses torso tilt and directional gyro correlation. These modes
count movement patterns, not anatomical range of motion.

## Upload

1. Open `firmware/cindysense/cindysense.ino` in Arduino IDE.
2. Install/select **esp32 by Espressif Systems** (tested compile target 3.3.12).
3. Select **ESP32 Dev Module**, the attached COM port, and 115200 serial baud.
4. Keep local `config.h` with reachable Wi-Fi and TinyIoT settings. Set
   `CINDYSENSE_RAW_UPLOADS` to `0`.
5. Click **Verify**, then **Upload**. Open Serial Monitor at 115200, newline.
6. Send `STATUS`, select an exercise, hold still four seconds, then exercise.
   Use `STATUS` to inspect totals, queued/confirmed events, retries, and heap.

The queue has 1024 fixed slots (about 20 KB). Event sequence IDs increase across mode changes;
event payloads include session ID, exercise, exercise count, sequence, UTC
timestamp, and `mpu6050` source. The network task retries ambiguous results
with a named CIN readback. Overflow is printed as an error with the lost
sequence. **The queue is RAM-only: power loss or reboot loses undelivered
events.** Webcam events have their own `webcam` source and must be shown
separately from `mpu6050` events in comparisons.

## Record and tune

For each exercise, select its mode, then send `RECORD`. Save the 30-second
Serial Monitor output to a text file. Hold the starting posture still for the
first 4 seconds, then perform 5 controlled repetitions and finish at rest.
Keep all `[SENSOR]` and `[COUNT]` lines; the replay tool skips them. The
`t,ax,ay,az,gx,gy,gz` rows are in ms, m/s2, and degrees/s. The recording queue
holds 128 samples; check `record_drops` in the stress summary and repeat the
recording if samples were lost. No raw data is uploaded by this command.

Build the replay tool with any C++11 compiler. On Windows with Visual Studio:

```bat
cl /EHsc /std:c++14 firmware\cindysense\tests\replay_chest_csv.cpp /Fe:replay_chest_csv.exe
replay_chest_csv.exe pushup pushup-5.txt
```

Run similarly for `pullup` and `squat`. The tool reports detected count,
sampling gaps, quiet acceleration noise, movement peak, approximate movement
displacement, and a **provisional** trigger suggestion. Compare the five
individual cycles and resting/shaking recordings before changing the separate
parameter structs in `exercise_detector.h`. The current pull-up and push-up
values are starting hypotheses only: no chest recordings for these exercises
were present in the repository. Squat thresholds remain in `squat_detector.h`.

## Twenty-minute run

Send `STRESS` to pause event uploads and exercise locally for 20 minutes. The
sensor task and local counts continue. The final `[STRESS] COMPLETE` line
reports initial/minimum/final heap, I2C failures, missed sample slots, local
total, and queue overflows. Record the periodic `[SENSOR]` frequency and
`[COUNT]` totals too. The network task resumes and drains the queue afterward.
With 1024 slots, overflow remains possible if more than 1024 events occur while
uploads are paused or TinyIoT is unavailable; the error is explicit. Run a
second trial with Wi-Fi disconnected and restored during exercise to verify
readback and delivery. Physical tests should log actual, detected, missed,
and false repetitions per exercise; synthetic host tests do not establish
real-world accuracy.

For the physical acceptance run, record separate trials of five pull-ups,
five push-ups, five squats, one minute of standing/rest, shaking without a
complete movement, and one movement with a short pause. Note actual and
detected counts for each trial. During a repeat set, disconnect Wi-Fi and
restore it; compare queued and confirmed events and check the TinyIoT
`RepetitionCount` resource. Log `[SENSOR]` frequency, I2C failures, missed
slots, and free heap before and after the 20-minute run. A single chest IMU
cannot verify elbow extension, squat depth, or chin height; visual judging or
the separate webcam is required for form assessment.
