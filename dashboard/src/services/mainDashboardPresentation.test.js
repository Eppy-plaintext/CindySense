import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const app = readFileSync(new URL('../App.jsx', import.meta.url), 'utf8')
const history = readFileSync(new URL('../components/StoredHistoryPage.jsx', import.meta.url), 'utf8')
const coach = readFileSync(new URL('../components/CindyPtPage.jsx', import.meta.url), 'utf8')
const styles = readFileSync(new URL('../App.css', import.meta.url), 'utf8')

test('main dashboard shows webcam and clearly simulated demo without ESP32 UI', () => {
  assert.match(app, /Webcam live|Demo Mode · simulated|Start Workout|Stop Workout/)
  assert.doesNotMatch(app, /ESP32|MPU6050|StoredRepetitionPanel|SdtDevicePanel|FederatedLearningPanel/)
  assert.match(app, /getWorkoutSnapshot|mergeLiveEventsIntoSnapshot|voiceCoach\.clear/)
})

test('removed technical panels no longer initiate dedicated frontend polling', () => {
  assert.doesNotMatch(app, /getLatestRepetition|getSdtDevice|getFederatedLearningStatus|getFederatedLearningResults/)
  assert.match(app, /getBackendHealth|getWebcamStatus|getWebcamWorker/)
  assert.doesNotMatch(app, /getWorkoutFatigue/)
})

test('visible workout cards omit raw session IDs but retain internal correlation', () => {
  assert.doesNotMatch(app, /Session \{(?:worker|live)\.sessionId\}/)
  assert.doesNotMatch(history, /\{row\.sessionId\}<\/small>/)
  assert.doesNotMatch(coach, /\{(?:latest|selected)\.sessionId\}/)
  assert.match(app, /poseStatus\.sessionId !== live\.sessionId/)
  assert.match(history, /key=\{row\.sessionId\}/)
})

test('routine webcam text and observed pace diagnostics stay off the main view', () => {
  assert.doesNotMatch(app, /Waiting for webcam movement|Workout counts are up to date/)
  assert.doesNotMatch(app, /<PerformanceMonitor|Observed pace|OBSERVED PACE/)
  assert.match(app, /webcamStatus\.status === 'error'|snapshotResult\.status === 'error'/)
  assert.match(app, /persistenceNotice|<WorkoutPerformance|voiceCoach\.warning/)
})

test('live workout takes visual priority while voice and fatigue safeguards remain', () => {
  const dashboard = app.slice(app.indexOf('function Dashboard('))
  assert.ok(dashboard.indexOf('className="webcam-panel') < dashboard.indexOf('className="hero-grid'))
  assert.ok(dashboard.indexOf('className="hero-grid') < dashboard.indexOf('className="voice-controls'))
  assert.doesNotMatch(dashboard, /className="mini-stats|WORKOUT TIP/)
  assert.match(dashboard, /<WorkoutPerformance/)
  assert.match(dashboard, /not physiological fatigue/)
  assert.match(dashboard, /trackingInterrupted/)
  assert.match(styles, /:focus-visible/)
  assert.match(styles, /prefers-reduced-motion: reduce/)
})

test('retention capacity does not create a main-dashboard warning while history retains integrity labels', () => {
  assert.doesNotMatch(app, /TinyIoT history is full|lastSnapshot\?\.retentionAtCapacity/)
  assert.match(app, /snapshotResult\.status === 'error' \|\| live\?\.stale/)
  assert.match(history, /summary_without_retained_events|summary_event_mismatch|Missing event history may reflect TinyIoT retention/)
})

test('pace stays available while degraded or absent tracking suppresses fatigue labels', () => {
  assert.match(app, /trackingStateForExercise\(worker, session\?\.sessionId/)
  assert.match(app, /quality === 'degraded' \? 'tracking-unstable'/)
  assert.match(app, /quality === 'insufficient_evidence' \? 'insufficient-data'/)
  assert.match(app, /Detector quality is unverified/)
  assert.doesNotMatch(app, /snapshotResult\.status !== 'error' &&\s*!\[/)
  assert.match(app, /snapshotResult\.status === 'error' \|\| webcamWorker\.status === 'error'/)
  assert.doesNotMatch(app, /!poseCurrent\) &&\s*matchedPaceSession/)
})
