import { useCallback, useEffect, useMemo, useState } from 'react'
import { ConnectionStatus, HistoryAvailability, StoredRepetitionPanel } from './components/ApiPanels.jsx'
import { CindyPtPage } from './components/CindyPtPage.jsx'
import { FatiguePanel } from './components/FatiguePanel.jsx'
import { usePolling } from './hooks/usePolling.js'
import { getBackendHealth, getLatestRepetition, getWebcamStatus, getWorkoutFatigue, getWorkoutHistory, getWorkoutSnapshot, integrationEventUrl } from './services/api.js'
import { acceptSnapshot, liveDisplay } from './services/workoutDisplay.js'
import { VoiceCoach, handleLocalVoiceEvent } from './services/voiceCoach.js'
import './App.css'

const WORKOUT_SECONDS = 20 * 60
const EXERCISES = [
  { key: 'pullUps', label: 'Pull-ups', target: 5, color: 'orange', icon: '↑' },
  { key: 'pushUps', label: 'Push-ups', target: 10, color: 'blue', icon: '↓' },
  { key: 'squats', label: 'Squats', target: 15, color: 'violet', icon: '◒' },
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
  const [repetitionRevision, setRepetitionRevision] = useState(0)
  const [summaryRevision, setSummaryRevision] = useState(0)
  const [displayMode, setDisplayMode] = useState('webcam')
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
    const stream = new EventSource('http://127.0.0.1:8765/events')
    stream.onmessage = ({ data }) => {
      const receivedAtEpochMs = Date.now()
      try { handleLocalVoiceEvent(voiceCoach, { ...JSON.parse(data), receivedAtEpochMs }) } catch { /* optional voice stream */ }
    }
    return () => { stream.close(); voiceCoach.reset() }
  }, [displayMode, voiceCoach])
  const currentExercise = useMemo(() => EXERCISES.find((exercise) => exercise.key === activeExercise), [activeExercise])
  const getLiveSnapshot = useCallback(async ({ signal }) => {
    const data = await getWorkoutSnapshot({ signal })
    if (!signal.aborted) setLastSnapshot((previous) => acceptSnapshot(previous, data) ? data : previous)
    return data
  }, [])
  const healthResult = usePolling(getBackendHealth)
  const liveResult = usePolling(getLatestRepetition, { enabled: activePage === 'dashboard' })
  const snapshotResult = usePolling(getLiveSnapshot, { enabled: activePage === 'dashboard', intervalMs: 2000, refreshKey: repetitionRevision })
  const webcamStatus = usePolling(getWebcamStatus, { enabled: activePage === 'dashboard' && displayMode === 'webcam', intervalMs: 1500 })
  const fatigueSessionId = displayMode === 'webcam' ? liveDisplay(lastSnapshot, displayMode, now)?.sessionId : null
  const getFatigue = useCallback(({ signal }) => getWorkoutFatigue(fatigueSessionId, { signal }), [fatigueSessionId])
  const fatigueResult = usePolling(getFatigue, { enabled: activePage === 'dashboard' && Boolean(fatigueSessionId), intervalMs: 5000, refreshKey: repetitionRevision })
  const historyResult = usePolling(getWorkoutHistory, { enabled: activePage === 'history', intervalMs: null })

  useEffect(() => {
    if (typeof EventSource === 'undefined') return undefined
    const stream = new EventSource(integrationEventUrl)
    stream.addEventListener('repetition', () => setRepetitionRevision((value) => value + 1))
    stream.addEventListener('summary', () => setSummaryRevision((value) => value + 1))
    stream.addEventListener('recommendation', () => setSummaryRevision((value) => value + 1))
    stream.addEventListener('reconcile', () => {
      setRepetitionRevision((value) => value + 1)
      setSummaryRevision((value) => value + 1)
    })
    return () => stream.close()
  }, [])

  useEffect(() => {
    const interval = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(interval)
  }, [])

  useEffect(() => {
    if (!lastSnapshot) return
    const selected = lastSnapshot.sources?.[displayMode]
    console.debug('[CindySense] dashboard rendered', {
      renderedAt: new Date().toISOString(), backendRetrievedAt: lastSnapshot.retrievedAt,
      backendRetrievalMs: lastSnapshot.retrievalMs,
      lastEventAt: selected?.lastEventAt, sessionId: selected?.sessionId,
      counts: selected?.counts, revision: lastSnapshot.snapshotRevision,
    })
  }, [lastSnapshot, displayMode])

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
      <div className="demo-pill"><span className="pulse-dot"></span><span>{displayMode === 'demo' ? 'DEMO MODE' : displayMode === 'webcam' ? 'WEBCAM LIVE' : 'ESP32 LIVE'}</span><em>{displayMode === 'demo' ? 'SIMULATED' : 'TINYIOT'}</em></div>
      <nav className="nav-list" aria-label="Main navigation"><p className="nav-label">Workspace</p>
        {[['dashboard', 'Dashboard', 'dashboard'], ['history', 'Workout history', 'history'], ['pt', 'Cindy PT', 'spark']].map(([page, label, icon]) => <button key={page} className={`nav-item ${activePage === page ? 'active' : ''}`} onClick={() => setActivePage(page)}><Icon name={icon} /><span>{label}</span>{page === 'dashboard' && <i>{displayMode === 'demo' ? 'DEMO' : 'LIVE'}</i>}</button>)}
        <p className="nav-label nav-label-spaced">System</p><button className="nav-item"><Icon name="settings" /><span>Settings</span></button>
      </nav>
      <div className="hardware-card"><div className="hardware-top"><span className="hardware-icon">⌁</span><span className="connected">{displayMode === 'demo' ? 'Demo hardware' : 'Live workout source'}</span></div><strong>{displayMode === 'webcam' ? 'Webcam pose detector' : 'ESP32 sensor kit'}</strong><span>{displayMode === 'webcam' ? 'MediaPipe pose' : 'MPU6050 motion sensor'}</span><div className="signal"><span></span><span></span><span></span><span></span><small>{displayMode === 'demo' ? 'Simulated signal' : 'TinyIoT events'}</small></div></div>
      <div className="user-card"><div className="avatar">JD</div><div><strong>Jordan Davis</strong><span>CrossFit athlete</span></div><button aria-label="Open profile">•••</button></div>
    </aside>
    <main className="main-content">
      <header className="topbar"><div className="mobile-brand"><div className="brand-mark"><span></span><span></span><span></span></div><strong>Cindy<span>Sense</span></strong></div><div className="breadcrumb"><span>Workspace</span><span>/</span><strong>{activePage === 'dashboard' ? 'Workout dashboard' : activePage === 'history' ? 'Workout history' : 'Cindy PT'}</strong></div><ConnectionStatus result={healthResult} /></header>
      <nav className="mobile-nav" aria-label="Mobile navigation">{[['dashboard', 'Dashboard'], ['history', 'History'], ['pt', 'Cindy PT']].map(([page, label]) => <button key={page} className={activePage === page ? 'active' : ''} onClick={() => setActivePage(page)}>{label}</button>)}</nav>
      {activePage === 'dashboard' && <Dashboard secondsLeft={secondsLeft} formatTime={formatTime} timerProgress={timerProgress} workoutState={workoutState} setWorkoutState={setWorkoutState} resetWorkout={resetWorkout} reps={reps} rounds={rounds} activeExercise={activeExercise} setActiveExercise={setActiveExercise} incrementRep={incrementRep} currentExercise={currentExercise} autoSimulate={autoSimulate} setAutoSimulate={setAutoSimulate} liveResult={liveResult} snapshotResult={snapshotResult} webcamStatus={webcamStatus} lastSnapshot={lastSnapshot} displayMode={displayMode} setDisplayMode={setDisplayMode} now={now} voicePreferences={voicePreferences} setVoicePreferences={setVoicePreferences} voiceCoach={voiceCoach} fatigueResult={fatigueResult} fatigueSessionId={fatigueSessionId} />}
      {activePage === 'history' && <><div className="view-data-slot"><HistoryAvailability result={historyResult} /><p className="demo-section-label">SIMULATED HISTORY EXAMPLES BELOW</p></div><HistoryPage /></>}
      {activePage === 'pt' && <CindyPtPage summaryRevision={summaryRevision} />}
    </main>
  </div>
}

function Dashboard({ secondsLeft, formatTime, timerProgress, workoutState, setWorkoutState, resetWorkout, reps, rounds, activeExercise, setActiveExercise, incrementRep, currentExercise, autoSimulate, setAutoSimulate, liveResult, snapshotResult, webcamStatus, lastSnapshot, displayMode, setDisplayMode, now, voicePreferences, setVoicePreferences, voiceCoach, fatigueResult, fatigueSessionId }) {
  const demo = displayMode === 'demo'
  const live = liveDisplay(lastSnapshot, displayMode, now)
  const poseStatus = webcamStatus.data
  const poseAge = Date.parse(poseStatus?.detectedAt)
  const poseCurrent = displayMode === 'webcam' && webcamStatus.status === 'success' &&
    Number.isFinite(poseAge) && now - poseAge < 5000
  const sessionMismatch = poseCurrent && live && poseStatus.sessionId && poseStatus.sessionId !== live.sessionId
  const confirmedForMode = lastSnapshot?.sources?.webcam?.counts?.[poseStatus?.exercise] ?? 0
  const pendingRepetitions = poseCurrent && !sessionMismatch && Number.isSafeInteger(poseStatus.detectedRepetitions)
    ? Math.max(0, poseStatus.detectedRepetitions - confirmedForMode) : null
  const shownReps = demo ? reps : live?.reps || INITIAL_REPS
  const shownRounds = demo ? rounds : live?.rounds || 0
  const shownSeconds = demo ? secondsLeft : live?.secondsLeft ?? WORKOUT_SECONDS
  const shownProgress = demo ? timerProgress : ((WORKOUT_SECONDS - shownSeconds) / WORKOUT_SECONDS) * 100
  const statusLabel = demo
    ? workoutState === 'running' ? 'Demo in progress' : workoutState === 'paused' ? 'Demo paused' : workoutState === 'complete' ? 'Demo time complete' : 'Demo ready'
    : snapshotResult.status === 'error' ? 'Source disconnected'
      : !live ? 'Waiting for session' : live.complete ? 'Time complete'
        : live.stale ? 'No recent repetitions' : 'Live session'
  const totalReps = Object.values(shownReps).reduce((a, b) => a + b, 0)
  return <div className="page-wrap"><section className="page-heading"><div><p className="eyebrow">TUESDAY · OCTOBER 27, 2026</p><h1>Good morning, Jordan <span>✦</span></h1><p className="subheading">Ready to build some momentum? Your next round starts here.</p></div>{demo && <button className="outline-btn" onClick={() => setWorkoutState('running')}><Icon name="play" size={15} /> Quick start</button>}</section>
    {displayMode === 'webcam' && <div className="source-picker"><span>
      {sessionMismatch ? 'Webcam session changed; waiting for its confirmed events.' :
        poseCurrent ? `Detected ${poseStatus.exercise} ${poseStatus.detectedRepetitions ?? '?'} | pending confirmation ${pendingRepetitions ?? '?'} | publisher queue ${poseStatus.eventQueueDepth ?? '?'} | retries ${poseStatus.eventRetries ?? '?'} | overflow ${poseStatus.eventOverflow ?? '?'}` :
          'Webcam pose status stale or disconnected; pending event count unavailable.'}
    </span></div>}
    {!demo && <div className="source-picker"><span>
      {snapshotResult.status === 'error' ? 'Connection lost; showing the last verified snapshot.' :
        live ? `Last confirmed event ${new Date(lastSnapshot.sources[displayMode].lastEventAt).toLocaleTimeString()} | backend ${lastSnapshot.retrievalMs ?? '?'} ms${live.stale ? ' | source stale' : ''}${lastSnapshot.retentionAtCapacity ? ' | TinyIoT retention full; older events may be unavailable' : ''}` :
          'Waiting for a confirmed repetition event.'}
    </span></div>}
    <div className="source-picker" role="group" aria-label="Workout data source">
      {[['webcam', 'Webcam live'], ['mpu6050', 'ESP32 live'], ['demo', 'Demo Mode']].map(([mode, label]) => <button key={mode} className={displayMode === mode ? 'active' : ''} onClick={() => setDisplayMode(mode)} aria-pressed={displayMode === mode}>{label}</button>)}
      {!demo && <span>{snapshotResult.status === 'error' ? `Connection lost: ${snapshotResult.error}. Showing last verified session.` : live ? `Session ${live.sessionId}${live.startInferred ? ' · timer starts at first stored event' : ''}${!live.coverageComplete ? ' · partial exercise coverage' : ''}` : 'Waiting for a stored session'}</span>}
    </div>
    <section className="voice-controls panel" aria-label="Voice Coach controls"><div><p className="eyebrow">AUDIO FEEDBACK</p><h2>Voice Coach</h2><small>{displayMode === 'webcam' ? 'Local webcam events · open this dashboard on the webcam computer' : 'Voice feedback currently uses the local webcam event stream'}</small></div><label><input type="checkbox" checked={voicePreferences.enabled} onChange={(event) => { if (!event.target.checked) voiceCoach.clear(); setVoicePreferences((old) => ({ ...old, enabled: event.target.checked })) }} /> ON</label><label>Volume <input aria-label="Voice volume" type="range" min="0" max="1" step="0.1" value={voicePreferences.volume} onChange={(event) => setVoicePreferences((old) => ({ ...old, volume: Number(event.target.value) }))} /></label><label>Speed <input aria-label="Voice speed" type="range" min="0.7" max="1.3" step="0.1" value={voicePreferences.rate} onChange={(event) => setVoicePreferences((old) => ({ ...old, rate: Number(event.target.value) }))} /></label></section>
    <div className="hero-grid"><section className="timer-card panel"><div className="panel-header"><div><p className="eyebrow">CINDY AMRAP</p><h2>20-minute engine</h2></div><span className={`session-badge ${demo ? workoutState : live?.complete ? 'complete' : live ? 'running' : 'idle'}`}><span className="status-dot"></span>{statusLabel}</span></div><div className="timer-display"><div className="timer-ring" style={{ '--progress': `${shownProgress * 3.6}deg` }}><div><span>{formatTime(shownSeconds)}</span><small>{shownSeconds === 0 ? 'DONE' : 'REMAINING'}</small></div></div></div><div className="timer-controls">{demo ? <>{workoutState === 'running' ? <button className="control-btn primary" onClick={() => setWorkoutState('paused')}><Icon name="pause" size={16} /> Pause</button> : <button className="control-btn primary" onClick={() => setWorkoutState('running')}><Icon name="play" size={16} /> {workoutState === 'paused' ? 'Resume' : 'Start workout'}</button>}<button className="control-btn secondary" onClick={resetWorkout}><Icon name="rotate" size={16} /> Reset</button></> : <span className="live-timer-note">Timer follows the selected source session</span>}</div><div className="timer-meta"><span><b>20:00</b> target</span><span><b>{Math.round(shownProgress)}%</b> elapsed</span><span><b>AMRAP</b> format</span></div></section>
      <section className="round-card panel"><div className="panel-header"><div><p className="eyebrow">SESSION SCORE</p><h2>Round tracker</h2></div><div className="round-number"><span>ROUND</span><strong>{String(shownRounds + 1).padStart(2, '0')}</strong></div></div><div className="round-progress"><div className="round-progress-top"><span>Current round</span><strong>{Math.round((totalReps / 30) * 100)}%</strong></div><div className="progress-track"><span style={{ width: `${(totalReps / 30) * 100}%` }}></span></div></div><div className="exercise-list">{EXERCISES.map((exercise) => <button className={`exercise-row ${activeExercise === exercise.key ? 'selected' : ''}`} key={exercise.key} onClick={() => setActiveExercise(exercise.key)}><span className={`exercise-icon ${exercise.color}`}>{exercise.icon}</span><span className="exercise-name"><strong>{exercise.label}</strong><small>{shownReps[exercise.key] >= exercise.target ? 'Complete' : `${exercise.target - shownReps[exercise.key]} reps to go`}</small></span><span className="exercise-count"><b>{String(shownReps[exercise.key]).padStart(2, '0')}</b><i>/{exercise.target}</i></span>{shownReps[exercise.key] >= exercise.target && <Icon name="check" size={16} />}</button>)}</div><div className="round-total"><span>Completed rounds</span><strong>{shownRounds}</strong></div></section></div>
    {demo && <section className="rep-console panel"><div className="panel-header"><div><p className="eyebrow">DEMO CONTROLS</p><h2>Simulate movement</h2></div><label className="toggle-label"><span>Auto simulate</span><button className={`toggle ${autoSimulate ? 'on' : ''}`} onClick={() => setAutoSimulate(!autoSimulate)} aria-label="Toggle auto simulate"><span></span></button></label></div><div className="rep-console-body"><div className="focus-exercise"><span className={`exercise-icon ${currentExercise.color}`}>{currentExercise.icon}</span><div><small>ACTIVE MOVEMENT</small><strong>{currentExercise.label}</strong></div></div><button className="rep-btn" onClick={() => incrementRep()} disabled={reps[currentExercise.key] >= currentExercise.target}><span>+1</span> Add {currentExercise.label} rep</button><div className="exercise-shortcuts">{EXERCISES.map((exercise) => <button key={exercise.key} className={activeExercise === exercise.key ? 'active' : ''} onClick={() => { setActiveExercise(exercise.key); incrementRep(exercise.key) }}><span className={`mini-dot ${exercise.color}`}></span>{exercise.label}</button>)}</div></div><p className="demo-note"><Icon name="zap" size={14} /> Timer, exercise counters, and round score are simulated. Stored TinyIoT content below is read-only and does not update this session.</p></section>}
    {!demo && <FatiguePanel result={fatigueResult} sessionId={fatigueSessionId} live={Boolean(live && !live.stale)} />}
    <StoredRepetitionPanel result={liveResult} snapshot={lastSnapshot} />
    <section className="bottom-grid"><div className="insight-card panel"><div className="insight-icon">↗</div><div><p className="eyebrow">TODAY'S INSIGHT</p><h3>Consistency beats intensity.</h3><p>Keep your transitions smooth and save your sprint for the final 3 minutes.</p></div><span className="insight-tag">SIMULATED</span></div><div className="mini-stats panel"><div><span>Last workout</span><strong>4 rounds</strong><small>+1 from average</small></div><div><span>Weekly volume</span><strong>12.4k</strong><small>reps logged</small></div><div><span>Streak</span><strong>06 days</strong><small>Personal best</small></div></div></section>
  </div>
}

function HistoryPage() {
  const sessions = [['Oct 25, 2026', 'Sunday', '5 rounds + 12 reps', '19:42', 'Completed'], ['Oct 23, 2026', 'Friday', '4 rounds + 21 reps', '20:00', 'Completed'], ['Oct 21, 2026', 'Wednesday', '3 rounds + 28 reps', '20:00', 'Completed'], ['Oct 19, 2026', 'Monday', '4 rounds + 5 reps', '17:36', 'Stopped early']]
  return <div className="page-wrap"><section className="page-heading"><div><p className="eyebrow">PERFORMANCE LOG</p><h1>Workout history</h1><p className="subheading">A clear view of your work, one session at a time.</p></div><button className="outline-btn"><span>↗</span> Export data</button></section><section className="history-summary">{[['orange', '◒', 'SESSIONS THIS MONTH', '14', '+3 vs. last month'], ['blue', '↗', 'AVERAGE ROUNDS', '4.2', '+0.6 round improvement'], ['violet', '✦', 'BEST SESSION', '5.4', 'Oct 25, 2026']].map((item) => <div className="summary-card panel" key={item[2]}><span className={`summary-symbol ${item[0]}`}>{item[1]}</span><div><small>{item[2]}</small><strong>{item[3]}</strong><span>{item[4]}</span></div></div>)}</section><section className="panel history-panel"><div className="panel-header"><div><p className="eyebrow">RECENT SESSIONS</p><h2>Your training rhythm</h2></div><span className="history-filter">Last 30 days <Icon name="chevron" size={14} /></span></div><div className="history-table"><div className="history-row history-head"><span>DATE</span><span>RESULT</span><span>DURATION</span><span>STATUS</span><span></span></div>{sessions.map((session) => <div className="history-row" key={session[0]}><span><strong>{session[0]}</strong><small>{session[1]}</small></span><span className="result">{session[2]}</span><span className="duration">{session[3]}</span><span><em className={`status-chip ${session[4] === 'Completed' ? 'complete' : 'stopped'}`}>{session[4]}</em></span><button aria-label={`View ${session[0]} workout details`}><Icon name="chevron" size={16} /></button></div>)}</div></section></div>
}

export default App
