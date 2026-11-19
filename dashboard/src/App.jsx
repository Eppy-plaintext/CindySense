import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ConnectionStatus } from './components/ApiPanels.jsx'
import { CindyPtPage } from './components/CindyPtPage.jsx'
import { StoredHistoryPage } from './components/StoredHistoryPage.jsx'
import { usePolling } from './hooks/usePolling.js'
import { getBackendHealth, getWebcamStatus, getWebcamWorker, getWorkoutHistory, getWorkoutSnapshot, integrationEventUrl, startWebcamWorker, stopWebcamWorker, webcamPreviewUrl } from './services/api.js'
import { acceptSnapshot, liveDisplay, matchingWebcamSession } from './services/workoutDisplay.js'
import { applyLiveEvent, EMPTY_LIVE_WORKOUTS, mergeLiveEventsIntoSnapshot } from './services/liveWorkout.js'
import { canStartWebcamWorker, webcamPersistenceStatus, webcamWorkoutDisplay } from './services/webcamDisplay.js'
import { formatDashboardDate } from './services/dashboardText.js'
import { exercisePerformance, setBasedPace } from './services/workoutPace.js'
import { trackingStateForExercise } from './services/trackingQuality.js'
import { VoiceCoach, handleLocalVoiceEvent } from './services/voiceCoach.js'
import './App.css'

const WORKOUT_SECONDS = 20 * 60
const EXERCISES = [
  { key: 'pullUps', label: 'Pull-ups', target: 5, color: 'orange', icon: 'up' },
  { key: 'pushUps', label: 'Push-ups', target: 10, color: 'blue', icon: 'down' },
  { key: 'squats', label: 'Squats', target: 15, color: 'violet', icon: 'activity' },
]
const INITIAL_REPS = { pullUps: 0, pushUps: 0, squats: 0 }

function Icon({ name, size = 18 }) {
  const paths = {
    dashboard: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
    history: <><path d="M3 12a9 9 0 1 0 3-6.7" /><path d="M3 4v5h5" /><path d="M12 7v5l3 2" /></>,
    spark: <><path d="m12 3-1.8 5.2L5 10l5.2 1.8L12 17l1.8-5.2L19 10l-5.2-1.8L12 3Z" /><path d="m19 16-.7 2.3L16 19l2.3.7L19 22l.7-2.3L22 19l-2.3-.7L19 16Z" /></>,
    settings: <><path d="M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4Z" /><path d="m19.4 15 .1.1a1.7 1.7 0 0 1-2.4 2.4l-.1-.1a1.7 1.7 0 0 0-2.9 1.2v.2a1.7 1.7 0 0 1-3.4 0v-.2a1.7 1.7 0 0 0-2.9-1.2l-.1.1a1.7 1.7 0 0 1-2.4-2.4l.1-.1a1.7 1.7 0 0 0-1.2-2.9H4a1.7 1.7 0 0 1 0-3.4h.2a1.7 1.7 0 0 0 1.2-2.9l-.1-.1a1.7 1.7 0 0 1 2.4-2.4l.1.1a1.7 1.7 0 0 0 2.9-1.2V4a1.7 1.7 0 0 1 3.4 0v.2a1.7 1.7 0 0 0 2.9 1.2l.1-.1a1.7 1.7 0 0 1 2.4 2.4l-.1.1a1.7 1.7 0 0 0 1.2 2.9h.2a1.7 1.7 0 0 1 0 3.4h-.2a1.7 1.7 0 0 0-1.2.9Z" /></>,
    play: <path d="m8 5 11 7-11 7V5Z" fill="currentColor" stroke="none" />,
    pause: <><rect x="7" y="5" width="3" height="14" rx="1" fill="currentColor" stroke="none" /><rect x="14" y="5" width="3" height="14" rx="1" fill="currentColor" stroke="none" /></>,
    rotate: <><path d="M4 12a8 8 0 1 0 2.3-5.7" /><path d="M4 4v5h5" /></>,
    chevron: <path d="m9 18 6-6-6-6" />,
    check: <path d="m5 12 4 4L19 6" />,
    zap: <path d="m13 2-9 12h7l-1 8 9-12h-7l1-8Z" />,
    user: <><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></>,
    up: <path d="M12 19V5m-5 5 5-5 5 5" />,
    down: <path d="M12 5v14m-5-5 5 5 5-5" />,
    activity: <path d="M3 12h4l3-6 4 12 3-6h4" />,
  }
  return <svg className="icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>
}

function App() {
  const [activePage, setActivePage] = useState('dashboard')
  const [secondsLeft, setSecondsLeft] = useState(WORKOUT_SECONDS)
  const [workoutState, setWorkoutState] = useState('idle')
  const [reps, setReps] = useState(INITIAL_REPS)
  const [rounds, setRounds] = useState(0)
  const [activeExercise, setActiveExercise] = useState('pullUps')
  const [autoSimulate, setAutoSimulate] = useState(false)
  const [lastSnapshot, setLastSnapshot] = useState(null)
  const [liveWorkouts, setLiveWorkouts] = useState(EMPTY_LIVE_WORKOUTS)
  const [paceWarning, setPaceWarning] = useState(null)
  const [repetitionRevision, setRepetitionRevision] = useState(0)
  const [summaryRevision, setSummaryRevision] = useState(0)
  const [displayMode, setDisplayMode] = useState('webcam')
  const [workerRevision, setWorkerRevision] = useState(0)
  const [workerAction, setWorkerAction] = useState({ busy: false, error: '', stopRequestedAt: null })
  const workerActionLock = useRef(false)
  const [now, setNow] = useState(() => Date.now())
  const [voicePreferences, setVoicePreferences] = useState(() => {
    try { return { enabled: true, volume: 0.8, rate: 1, ...JSON.parse(localStorage.getItem('cindysense.voice') || '{}') } }
    catch { return { enabled: true, volume: 0.8, rate: 1 } }
  })
  const [voiceCoach] = useState(() => new VoiceCoach(globalThis.speechSynthesis, globalThis.SpeechSynthesisUtterance))
  useEffect(() => {
    voiceCoach.configure(voicePreferences)
    try { localStorage.setItem('cindysense.voice', JSON.stringify(voicePreferences)) } catch { /* optional storage */ }
  }, [voiceCoach, voicePreferences])
  useEffect(() => {
    if (displayMode !== 'webcam') { voiceCoach.reset(); return undefined }
    voiceCoach.setOutputEnabled(false)
    const stream = new EventSource('http://127.0.0.1:8765/events')
    stream.addEventListener('speaker', () => voiceCoach.setOutputEnabled(true))
    stream.onerror = () => voiceCoach.setOutputEnabled(false)
    stream.onmessage = ({ data }) => {
      const receivedAtEpochMs = Date.now()
      try { handleLocalVoiceEvent(voiceCoach, { ...JSON.parse(data), receivedAtEpochMs }) } catch { /* optional voice stream */ }
    }
    return () => { stream.close(); voiceCoach.setOutputEnabled(false); voiceCoach.reset() }
  }, [displayMode, voiceCoach])
  const currentExercise = useMemo(() => EXERCISES.find((exercise) => exercise.key === activeExercise), [activeExercise])
  const getLiveSnapshot = useCallback(async ({ signal }) => {
    const data = await getWorkoutSnapshot({ signal })
    if (!signal.aborted) setLastSnapshot((previous) => acceptSnapshot(previous, data) ? data : previous)
    return data
  }, [])
  const healthResult = usePolling(getBackendHealth)
  // The SSE stream is the normal update path. Keep a slower snapshot poll as
  // recovery for a missed/rejected callback.
  const snapshotResult = usePolling(getLiveSnapshot, { enabled: activePage === 'dashboard', intervalMs: 5000, refreshKey: repetitionRevision })
  const webcamStatus = usePolling(getWebcamStatus, { enabled: activePage === 'dashboard' && displayMode === 'webcam', intervalMs: 1500 })
  const webcamWorker = usePolling(getWebcamWorker, { enabled: activePage === 'dashboard' && displayMode === 'webcam', intervalMs: 500, refreshKey: workerRevision })
  const workerData = webcamWorker.status === 'success' ? webcamWorker.data : null
  async function controlWebcam(action) {
    if (workerActionLock.current) return
    workerActionLock.current = true
    setPaceWarning(null)
    setWorkerAction({ busy: true, error: '', stopRequestedAt: action === 'stop' ? Date.now() : null })
    try {
      if (action === 'start') voiceCoach.unlock()
      await (action === 'start' ? startWebcamWorker() : stopWebcamWorker())
      setWorkerRevision((value) => value + 1)
      setWorkerAction({ busy: false, error: '', stopRequestedAt: action === 'stop' ? Date.now() : null })
    } catch (error) {
      setWorkerAction({ busy: false, error: error.message, stopRequestedAt: null })
    } finally {
      workerActionLock.current = false
    }
  }
  const displayedSnapshot = useMemo(
    () => mergeLiveEventsIntoSnapshot(lastSnapshot, liveWorkouts),
    [lastSnapshot, liveWorkouts],
  )
  const visiblePaceWarning = paceWarning &&
    !['idle', 'stopped', 'completed', 'error'].includes(workerData?.state) &&
    (!workerData?.sessionId || workerData.sessionId === paceWarning.sessionId)
    ? paceWarning : null
  const historyResult = usePolling(getWorkoutHistory, { enabled: activePage === 'history', intervalMs: null })

  useEffect(() => {
    if (typeof EventSource === 'undefined') return undefined
    const stream = new EventSource(integrationEventUrl)
    stream.addEventListener('repetition', ({ data }) => {
      try {
        setLiveWorkouts((current) => applyLiveEvent(current, JSON.parse(data)))
      } catch { /* snapshot polling remains the recovery path */ }
      setRepetitionRevision((value) => value + 1)
    })
    stream.addEventListener('pace_warning', ({ data }) => {
      try {
        const warning = JSON.parse(data)
        setPaceWarning((previous) => previous?.warningId === warning.warningId ? previous : warning)
        voiceCoach.warning(warning)
      } catch { /* warning delivery is advisory; counts remain unaffected */ }
    })
    stream.addEventListener('summary', () => setSummaryRevision((value) => value + 1))
    stream.addEventListener('recommendation', () => setSummaryRevision((value) => value + 1))
    stream.addEventListener('reconcile', () => {
      setRepetitionRevision((value) => value + 1)
      setSummaryRevision((value) => value + 1)
    })
    return () => stream.close()
  }, [voiceCoach])

  useEffect(() => {
    const interval = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(interval)
  }, [])

  const incrementRep = useCallback((exerciseKey = activeExercise) => {
    setReps((currentReps) => {
      const exercise = EXERCISES.find((item) => item.key === exerciseKey)
      if (!exercise || currentReps[exerciseKey] >= exercise.target) return currentReps
      const nextReps = { ...currentReps, [exerciseKey]: currentReps[exerciseKey] + 1 }
      if (EXERCISES.every((item) => nextReps[item.key] >= item.target)) {
        setRounds((currentRounds) => currentRounds + 1)
        setActiveExercise('pullUps')
        return INITIAL_REPS
      }
      return nextReps
    })
  }, [activeExercise])

  useEffect(() => {
    if (workoutState !== 'running') return undefined
    const interval = window.setInterval(() => {
      setSecondsLeft((currentSeconds) => {
        if (currentSeconds <= 1) { setWorkoutState('complete'); return 0 }
        return currentSeconds - 1
      })
    }, 1000)
    return () => window.clearInterval(interval)
  }, [workoutState])

  useEffect(() => {
    if (!autoSimulate || workoutState !== 'running') return undefined
    const interval = window.setInterval(() => incrementRep(), 1800)
    return () => window.clearInterval(interval)
  }, [autoSimulate, incrementRep, workoutState])

  const resetWorkout = () => {
    setSecondsLeft(WORKOUT_SECONDS); setWorkoutState('idle'); setReps(INITIAL_REPS); setRounds(0); setActiveExercise('pullUps')
  }
  const formatTime = (seconds) => `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
  const timerProgress = ((WORKOUT_SECONDS - secondsLeft) / WORKOUT_SECONDS) * 100

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark"><span></span><span></span><span></span></div><div><strong>Cindy<span>Sense</span></strong><small>TRAIN SMARTER</small></div></div>
      <div className="demo-pill"><span className="pulse-dot"></span><span>{displayMode === 'demo' ? 'DEMO MODE' : 'WEBCAM LIVE'}</span><em>{displayMode === 'demo' ? 'SIMULATED' : 'TINYIOT'}</em></div>
      <nav className="nav-list" aria-label="Main navigation"><p className="nav-label">Workspace</p>
        {[['dashboard', 'Dashboard', 'dashboard'], ['history', 'Workout history', 'history'], ['pt', 'Cindy PT', 'spark']].map(([page, label, icon]) => <button key={page} className={`nav-item ${activePage === page ? 'active' : ''}`} onClick={() => setActivePage(page)}><Icon name={icon} /><span>{label}</span>{page === 'dashboard' && <i>{displayMode === 'demo' ? 'DEMO' : 'LIVE'}</i>}</button>)}
        <p className="nav-label nav-label-spaced">System</p><button className="nav-item"><Icon name="settings" /><span>Settings</span></button>
      </nav>
      <div className="hardware-card"><div className="hardware-top"><span className="hardware-icon"><Icon name="zap" size={20} /></span><span className="connected">{displayMode === 'demo' ? 'Simulated workout' : 'Live workout source'}</span></div><strong>Webcam pose detector</strong><span>{displayMode === 'demo' ? 'Demo counts only' : 'MediaPipe pose'}</span><div className="signal"><span></span><span></span><span></span><span></span><small>{displayMode === 'demo' ? 'Simulated signal' : 'TinyIoT events'}</small></div></div>
      <div className="user-card"><div className="avatar"><Icon name="user" size={17} /></div><div><strong>Guest User</strong><span>Guest Mode</span></div></div>
    </aside>
    <main className="main-content">
      <header className="topbar"><div className="mobile-brand"><div className="brand-mark"><span></span><span></span><span></span></div><strong>Cindy<span>Sense</span></strong></div><div className="breadcrumb"><span>Workspace</span><span>/</span><strong>{activePage === 'dashboard' ? 'Workout dashboard' : activePage === 'history' ? 'Workout history' : 'Cindy PT'}</strong></div><ConnectionStatus result={healthResult} /></header>
      <nav className="mobile-nav" aria-label="Mobile navigation">{[['dashboard', 'Dashboard'], ['history', 'History'], ['pt', 'Cindy PT']].map(([page, label]) => <button key={page} className={activePage === page ? 'active' : ''} onClick={() => setActivePage(page)}>{label}</button>)}</nav>
      {activePage === 'dashboard' && <Dashboard secondsLeft={secondsLeft} formatTime={formatTime} timerProgress={timerProgress} workoutState={workoutState} setWorkoutState={setWorkoutState} resetWorkout={resetWorkout} reps={reps} rounds={rounds} activeExercise={activeExercise} setActiveExercise={setActiveExercise} incrementRep={incrementRep} currentExercise={currentExercise} autoSimulate={autoSimulate} setAutoSimulate={setAutoSimulate} snapshotResult={snapshotResult} webcamStatus={webcamStatus} webcamWorker={webcamWorker} workerAction={workerAction} controlWebcam={controlWebcam} lastSnapshot={displayedSnapshot} displayMode={displayMode} setDisplayMode={setDisplayMode} now={now} voicePreferences={voicePreferences} setVoicePreferences={setVoicePreferences} voiceCoach={voiceCoach} paceWarning={visiblePaceWarning} />}
      {activePage === 'history' && <StoredHistoryPage result={historyResult} />}
      {activePage === 'pt' && <CindyPtPage summaryRevision={summaryRevision} />}
    </main>
  </div>
}

function WebcamPreview() {
  const [preview, setPreview] = useState('loading')
  useEffect(() => {
    const timeout = window.setTimeout(() => setPreview((current) => current === 'loading' ? 'timeout' : current), 10000)
    return () => window.clearTimeout(timeout)
  }, [])
  return <div className="webcam-preview-panel" aria-label="Live webcam preview">
    {preview !== 'ready' && <p className="webcam-preview-message" role={preview === 'loading' ? 'status' : 'alert'}>
      {preview === 'loading' ? 'Opening camera and waiting for the first frame...' :
        preview === 'timeout' ? 'No camera frames arrived. Check camera permission and worker status.' :
          'Camera preview disconnected. Check the FastAPI connection and worker status.'}
    </p>}
    <img className="webcam-preview" src={webcamPreviewUrl} alt="Live annotated CindySense webcam feed"
      onLoad={() => setPreview('ready')} onError={() => setPreview('error')} />
  </div>
}

function Dashboard({ secondsLeft, formatTime, timerProgress, workoutState, setWorkoutState, resetWorkout, reps, rounds, activeExercise, setActiveExercise, incrementRep, currentExercise, autoSimulate, setAutoSimulate, snapshotResult, webcamStatus, webcamWorker, workerAction, controlWebcam, lastSnapshot, displayMode, setDisplayMode, now, voicePreferences, setVoicePreferences, voiceCoach, paceWarning }) {
  const demo = displayMode === 'demo'
  const worker = webcamWorker.status === 'success' ? webcamWorker.data : null
  const persistenceStatus = displayMode === 'webcam' ? webcamPersistenceStatus(worker) : null
  const persistenceNotice = persistenceStatus?.startsWith('Workout stored:') ? null : persistenceStatus
  const workerLive = displayMode === 'webcam' ? webcamWorkoutDisplay(worker) : null
  const live = workerLive || (displayMode === 'webcam' && worker?.state && worker.state !== 'idle'
    ? null : liveDisplay(lastSnapshot, displayMode, now))
  const poseStatus = webcamStatus.data
  const poseAge = Date.parse(poseStatus?.detectedAt)
  const poseCurrent = displayMode === 'webcam' && webcamStatus.status === 'success' &&
    Number.isFinite(poseAge) && now - poseAge < 5000
  const sessionMismatch = poseCurrent && live && poseStatus.sessionId && poseStatus.sessionId !== live.sessionId
  const shownReps = demo ? reps : live?.reps || INITIAL_REPS
  const shownRounds = demo ? rounds : live?.rounds || 0
  const shownSeconds = demo ? secondsLeft : live?.secondsLeft ?? WORKOUT_SECONDS
  const shownProgress = demo ? timerProgress : ((WORKOUT_SECONDS - shownSeconds) / WORKOUT_SECONDS) * 100
  const statusLabel = demo
    ? workoutState === 'running' ? 'Demo in progress' : workoutState === 'paused' ? 'Demo paused' : workoutState === 'complete' ? 'Demo time complete' : 'Demo ready'
    : workerLive ? worker.state === 'stopped' ? 'Workout complete' : worker.state === 'stopping' ? 'Finishing workout' : 'Webcam live'
    : displayMode === 'webcam' && worker?.state === 'starting' ? 'Opening camera'
    : displayMode === 'webcam' && worker?.state === 'error' ? 'Camera error'
    : snapshotResult.status === 'error' ? 'Workout data unavailable'
      : !live ? 'Waiting for session' : live.complete ? 'Time complete'
        : live.stale ? 'No recent repetitions' : 'Live session'
  const totalReps = Object.values(shownReps).reduce((a, b) => a + b, 0)
  const matchedPaceSession = matchingWebcamSession(lastSnapshot, live?.sessionId)
  const paceSession = !demo && !sessionMismatch && worker?.state !== 'starting' &&
    (!worker?.sessionId || worker.sessionId === live?.sessionId) &&
    matchedPaceSession ? matchedPaceSession : null
  return <div className="page-wrap"><section className="page-heading"><div><p className="eyebrow">{formatDashboardDate(now)}</p><h1>Welcome to CindySense</h1><p className="subheading">Ready to build some momentum? Your next round starts here.</p></div>{demo && <button className="outline-btn" onClick={() => setWorkoutState('running')}><Icon name="play" size={15} /> Quick start</button>}</section>
    {displayMode === 'webcam' && (sessionMismatch || webcamStatus.status === 'error' ||
      (['running', 'stopping'].includes(worker?.state) && !poseCurrent)) &&
      <p className="workout-notice" role="alert">{sessionMismatch ? 'Webcam session changed; waiting for confirmed repetitions.' :
        'Webcam feed is disconnected or delayed; confirmed counts may lag.'}</p>}
    {!demo && (snapshotResult.status === 'error' || live?.stale) &&
      <p className="workout-notice" role="alert">{snapshotResult.status === 'error' ? 'Workout data is unavailable; showing the last verified counts.' :
        'No recent confirmed repetitions.'}</p>}
    {paceWarning && <p className="workout-notice" role="status">{paceWarning.message}</p>}
    <div className="source-picker" role="group" aria-label="Workout data source">
      {[['webcam', 'Webcam live'], ['demo', 'Demo Mode · simulated']].map(([mode, label]) => <button key={mode} className={displayMode === mode ? 'active' : ''} onClick={() => setDisplayMode(mode)} aria-pressed={displayMode === mode}>{label}</button>)}
      {!demo && <span>{snapshotResult.status === 'error' ? `Workout data request failed: ${snapshotResult.error}. Showing the last verified counts.` : live ? `${live.complete ? 'Workout complete' : 'Workout in progress'}${live.startInferred ? ' · timer estimated from first event' : ''}${!live.coverageComplete ? ' · some repetitions may be missing' : ''}` : 'Waiting for a stored workout'}</span>}
    </div>
    {displayMode === 'webcam' && <section className="webcam-panel panel" aria-label="Webcam workout controls">
      <div className="panel-header"><div><p className="eyebrow">LOCAL CAMERA</p><h2>Webcam workout</h2></div><span className="stored-tag">{worker?.state === 'idle' ? 'Webcam Idle' : worker?.state || (webcamWorker.status === 'error' ? 'unavailable' : 'checking')}</span></div>
      <div className="webcam-actions">
        <button className="control-btn primary" onClick={() => controlWebcam('start')} disabled={workerAction.busy || !canStartWebcamWorker(worker)}>Start Workout</button>
        <button className="control-btn secondary" onClick={() => controlWebcam('stop')} disabled={workerAction.busy || (worker && ['idle', 'stopped', 'completed'].includes(worker.state))}>Stop Workout</button>
        <span role="status">{workerAction.error || persistenceNotice ||
          (workerAction.stopRequestedAt && now - workerAction.stopRequestedAt > 15000 && worker?.state !== 'stopped' ? 'Workout is taking longer to stop. Retry Stop Workout if needed.' : null) ||
          (workerAction.stopRequestedAt ? 'Stopping webcam and saving the completed workout...' : null) ||
          (persistenceStatus?.startsWith('Workout stored:') ? 'Workout complete' : null) || worker?.message ||
          (webcamWorker.status === 'error' ? webcamWorker.error : `Camera ${worker?.state || 'checking'}`)}</span>
      </div>
      {['starting', 'running', 'stopping'].includes(worker?.state) && <WebcamPreview key={worker.sessionId || worker.pid || 'starting'} />}
      {worker?.state && ['starting', 'running', 'stopping'].includes(worker.state) && <p className="camera-guidance">Camera positioning: {worker.positioning || 'Checking required joints...'}</p>}
    </section>}
    <div className="hero-grid"><section className="timer-card panel"><div className="panel-header"><div><p className="eyebrow">CINDY AMRAP</p><h2>20-minute engine</h2></div><span className={`session-badge ${demo ? workoutState : live?.complete ? 'complete' : live ? 'running' : 'idle'}`}><span className="status-dot"></span>{statusLabel}</span></div><div className="timer-display"><div className="timer-ring" style={{ '--progress': `${shownProgress * 3.6}deg` }}><div><span>{formatTime(shownSeconds)}</span><small>{shownSeconds === 0 ? 'DONE' : 'REMAINING'}</small></div></div></div><div className="timer-controls">{demo ? <>{workoutState === 'running' ? <button className="control-btn primary" onClick={() => setWorkoutState('paused')}><Icon name="pause" size={16} /> Pause</button> : <button className="control-btn primary" onClick={() => setWorkoutState('running')}><Icon name="play" size={16} /> {workoutState === 'paused' ? 'Resume' : 'Start workout'}</button>}<button className="control-btn secondary" onClick={resetWorkout}><Icon name="rotate" size={16} /> Reset</button></> : <span className="live-timer-note">Timer follows the selected source session</span>}</div><div className="timer-meta"><span><b>20:00</b> target</span><span><b>{Math.round(shownProgress)}%</b> elapsed</span><span><b>AMRAP</b> format</span></div></section>
      <section className="round-card panel"><div className="panel-header"><div><p className="eyebrow">SESSION SCORE</p><h2>Round tracker</h2></div><div className="round-number"><span>ROUND</span><strong>{String(shownRounds + 1).padStart(2, '0')}</strong></div></div><div className="round-progress"><div className="round-progress-top"><span>Current round</span><strong>{Math.round((totalReps / 30) * 100)}%</strong></div><div className="progress-track"><span style={{ width: `${(totalReps / 30) * 100}%` }}></span></div></div><div className="exercise-list">{EXERCISES.map((exercise) => <button className={`exercise-row ${activeExercise === exercise.key ? 'selected' : ''}`} key={exercise.key} onClick={() => setActiveExercise(exercise.key)}><span className={`exercise-icon ${exercise.color}`}><Icon name={exercise.icon} size={15} /></span><span className="exercise-name"><strong>{exercise.label}</strong><small>{shownReps[exercise.key] >= exercise.target ? 'Complete' : `${exercise.target - shownReps[exercise.key]} reps to go`}</small></span><span className="exercise-count"><b>{String(shownReps[exercise.key]).padStart(2, '0')}</b><i>/{exercise.target}</i></span>{shownReps[exercise.key] >= exercise.target && <Icon name="check" size={16} />}</button>)}</div><div className="round-total"><span>Completed rounds</span><strong>{shownRounds}</strong></div></section></div>
    <section className="voice-controls panel" aria-label="Voice Coach controls"><div><p className="eyebrow">AUDIO FEEDBACK</p><h2>Voice Coach</h2><small>{displayMode === 'webcam' ? 'Local webcam events | open this dashboard on the webcam computer' : 'Voice feedback currently uses the local webcam event stream'}</small></div><label><input type="checkbox" checked={voicePreferences.enabled} onChange={(event) => { if (!event.target.checked) voiceCoach.clear(); setVoicePreferences((old) => ({ ...old, enabled: event.target.checked })) }} /> ON</label><label>Volume <input aria-label="Voice volume" type="range" min="0" max="1" step="0.1" value={voicePreferences.volume} onChange={(event) => setVoicePreferences((old) => ({ ...old, volume: Number(event.target.value) }))} /></label><label>Speed <input aria-label="Voice speed" type="range" min="0.7" max="1.3" step="0.1" value={voicePreferences.rate} onChange={(event) => setVoicePreferences((old) => ({ ...old, rate: Number(event.target.value) }))} /></label></section>
    {demo && <section className="rep-console panel"><div className="panel-header"><div><p className="eyebrow">DEMO CONTROLS</p><h2>Simulate movement</h2></div><label className="toggle-label"><span>Auto simulate</span><button className={`toggle ${autoSimulate ? 'on' : ''}`} onClick={() => setAutoSimulate(!autoSimulate)} aria-label="Toggle auto simulate"><span></span></button></label></div><div className="rep-console-body"><div className="focus-exercise"><span className={`exercise-icon ${currentExercise.color}`}><Icon name={currentExercise.icon} size={15} /></span><div><small>ACTIVE MOVEMENT</small><strong>{currentExercise.label}</strong></div></div><button className="rep-btn" onClick={() => incrementRep()} disabled={reps[currentExercise.key] >= currentExercise.target}><span>+1</span> Add {currentExercise.label} rep</button><div className="exercise-shortcuts">{EXERCISES.map((exercise) => <button key={exercise.key} className={activeExercise === exercise.key ? 'active' : ''} onClick={() => { setActiveExercise(exercise.key); incrementRep(exercise.key) }}><span className={`mini-dot ${exercise.color}`}></span>{exercise.label}</button>)}</div></div><p className="demo-note"><Icon name="zap" size={14} /> This is a simulation. These counts are not saved as a real workout.</p></section>}
    {!demo && <WorkoutPerformance session={paceSession} now={now} worker={worker} trackingInterrupted={Boolean(
      sessionMismatch || worker?.cameraInterrupted || worker?.state === 'error' ||
      snapshotResult.status === 'error' || webcamWorker.status === 'error',
    )} />}
  </div>
}

function WorkoutPerformance({ session, now, worker, trackingInterrupted }) {
  const names = { pullUps: 'pullup', pushUps: 'pushup', squats: 'squat' }
  const labels = { low: 'Low', moderate: 'Moderate', high: 'High', 'insufficient-data': 'Insufficient data', 'tracking-unstable': 'Tracking unstable' }
  return <section className="pace-panel panel" aria-label="Workout performance">
    <div className="panel-header"><div><p className="eyebrow">RECENT DETECTED REPS</p><h2>Workout Performance</h2></div><small>Active detected-rep pace, not workout throughput</small></div>
    <div className="pace-grid">{EXERCISES.map((exercise) => {
      const quality = trackingStateForExercise(worker, session?.sessionId, names[exercise.key], now)
      const { fatigue: paceFatigue } = exercisePerformance(session, names[exercise.key], now, undefined, trackingInterrupted)
      const setReading = setBasedPace(session, names[exercise.key], now, trackingInterrupted || quality === 'degraded')
      const pace = setReading.pace
      const fatigue = trackingInterrupted || quality === 'degraded' ? 'tracking-unstable'
        : quality === 'insufficient_evidence' ? 'insufficient-data' : paceFatigue
      return <div className="pace-indicator" key={exercise.key} aria-label={`${exercise.label} performance`}>
        <span>{exercise.label} · Set {setReading.round ?? '--'}</span><strong>{pace ? pace.secondsPerRep.toFixed(1) : '--'}<small> s/rep</small></strong>
        <small>{pace ? `${pace.repsPerMinute.toFixed(1)} detected reps/min` : 'No measured pace yet'}</small>
        <small>{({ 'waiting-for-repetitions': 'Waiting for repetitions', 'live-set-pace': 'Live set pace',
          'last-measured-set-pace': 'Last measured set pace', 'new-set-started': 'New set started',
          'tracking-interrupted': 'Tracking interrupted · last valid reading',
          'insufficient-data': 'Insufficient data' })[setReading.status]}</small>
        <small>Tracking: {quality === 'stable' ? 'continuous in observed frames' : quality === 'degraded' ? 'interrupted or incomplete' : 'insufficient evidence'}</small>
        <div className="performance-fatigue"><span>Estimated performance fatigue</span><b className={`performance-level ${fatigue}`}>{labels[fatigue]}</b></div>
      </div>
    })}</div>
    <p>Experimental pace-change heuristic, not physiological fatigue. Compares recent detected reps with each exercise’s early-session pace. Detector quality is unverified; missed reps can distort results.</p>
  </section>
}

export default App
