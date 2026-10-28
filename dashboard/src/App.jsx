import { useCallback, useEffect, useMemo, useState } from 'react'
import { ConnectionStatus, HistoryAvailability, StoredRecommendationPanel, StoredRepetitionPanel } from './components/ApiPanels.jsx'
import { usePolling } from './hooks/usePolling.js'
import { getBackendHealth, getCindyPtRecommendations, getLatestRepetition, getWorkoutHistory } from './services/api.js'
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
  const currentExercise = useMemo(() => EXERCISES.find((exercise) => exercise.key === activeExercise), [activeExercise])
  const healthResult = usePolling(getBackendHealth)
  const liveResult = usePolling(getLatestRepetition, { enabled: activePage === 'dashboard' })
  const recommendationResult = usePolling(getCindyPtRecommendations, { enabled: activePage === 'pt' })
  const historyResult = usePolling(getWorkoutHistory, { enabled: activePage === 'history', intervalMs: null })

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
      <div className="demo-pill"><span className="pulse-dot"></span><span>DEMO MODE</span><em>SIMULATED</em></div>
      <nav className="nav-list" aria-label="Main navigation"><p className="nav-label">Workspace</p>
        {[['dashboard', 'Dashboard', 'dashboard'], ['history', 'Workout history', 'history'], ['pt', 'Cindy PT', 'spark']].map(([page, label, icon]) => <button key={page} className={`nav-item ${activePage === page ? 'active' : ''}`} onClick={() => setActivePage(page)}><Icon name={icon} /><span>{label}</span>{page === 'dashboard' && <i>DEMO</i>}</button>)}
        <p className="nav-label nav-label-spaced">System</p><button className="nav-item"><Icon name="settings" /><span>Settings</span></button>
      </nav>
      <div className="hardware-card"><div className="hardware-top"><span className="hardware-icon">⌁</span><span className="connected">Demo hardware</span></div><strong>ESP32 sensor kit</strong><span>MPU6050 motion sensor</span><div className="signal"><span></span><span></span><span></span><span></span><small>Simulated signal</small></div></div>
      <div className="user-card"><div className="avatar">JD</div><div><strong>Jordan Davis</strong><span>CrossFit athlete</span></div><button aria-label="Open profile">•••</button></div>
    </aside>
    <main className="main-content">
      <header className="topbar"><div className="mobile-brand"><div className="brand-mark"><span></span><span></span><span></span></div><strong>Cindy<span>Sense</span></strong></div><div className="breadcrumb"><span>Workspace</span><span>/</span><strong>{activePage === 'dashboard' ? 'Workout dashboard' : activePage === 'history' ? 'Workout history' : 'Cindy PT'}</strong></div><ConnectionStatus result={healthResult} /></header>
      <nav className="mobile-nav" aria-label="Mobile navigation">{[['dashboard', 'Dashboard'], ['history', 'History'], ['pt', 'Cindy PT']].map(([page, label]) => <button key={page} className={activePage === page ? 'active' : ''} onClick={() => setActivePage(page)}>{label}</button>)}</nav>
      {activePage === 'dashboard' && <Dashboard secondsLeft={secondsLeft} formatTime={formatTime} timerProgress={timerProgress} workoutState={workoutState} setWorkoutState={setWorkoutState} resetWorkout={resetWorkout} reps={reps} rounds={rounds} activeExercise={activeExercise} setActiveExercise={setActiveExercise} incrementRep={incrementRep} currentExercise={currentExercise} autoSimulate={autoSimulate} setAutoSimulate={setAutoSimulate} liveResult={liveResult} />}
      {activePage === 'history' && <><div className="view-data-slot"><HistoryAvailability result={historyResult} /><p className="demo-section-label">SIMULATED HISTORY EXAMPLES BELOW</p></div><HistoryPage /></>}
      {activePage === 'pt' && <><div className="view-data-slot"><StoredRecommendationPanel result={recommendationResult} /></div><CindyPtPage /></>}
    </main>
  </div>
}

function Dashboard({ secondsLeft, formatTime, timerProgress, workoutState, setWorkoutState, resetWorkout, reps, rounds, activeExercise, setActiveExercise, incrementRep, currentExercise, autoSimulate, setAutoSimulate, liveResult }) {
  const statusLabel = workoutState === 'running' ? 'Session in progress' : workoutState === 'paused' ? 'Session paused' : workoutState === 'complete' ? 'Time complete' : 'Ready when you are'
  const totalReps = Object.values(reps).reduce((a, b) => a + b, 0)
  return <div className="page-wrap"><section className="page-heading"><div><p className="eyebrow">TUESDAY · OCTOBER 27, 2026</p><h1>Good morning, Jordan <span>✦</span></h1><p className="subheading">Ready to build some momentum? Your next round starts here.</p></div><button className="outline-btn" onClick={() => setWorkoutState('running')}><Icon name="play" size={15} /> Quick start</button></section>
    <div className="hero-grid"><section className="timer-card panel"><div className="panel-header"><div><p className="eyebrow">CINDY AMRAP</p><h2>20-minute engine</h2></div><span className={`session-badge ${workoutState}`}><span className="status-dot"></span>{statusLabel}</span></div><div className="timer-display"><div className="timer-ring" style={{ '--progress': `${timerProgress * 3.6}deg` }}><div><span>{formatTime(secondsLeft)}</span><small>{secondsLeft === 0 ? 'DONE' : 'REMAINING'}</small></div></div></div><div className="timer-controls">{workoutState === 'running' ? <button className="control-btn primary" onClick={() => setWorkoutState('paused')}><Icon name="pause" size={16} /> Pause</button> : <button className="control-btn primary" onClick={() => setWorkoutState('running')}><Icon name="play" size={16} /> {workoutState === 'paused' ? 'Resume' : 'Start workout'}</button>}<button className="control-btn secondary" onClick={resetWorkout}><Icon name="rotate" size={16} /> Reset</button></div><div className="timer-meta"><span><b>20:00</b> target</span><span><b>{Math.round(timerProgress)}%</b> elapsed</span><span><b>AMRAP</b> format</span></div></section>
      <section className="round-card panel"><div className="panel-header"><div><p className="eyebrow">SESSION SCORE</p><h2>Round tracker</h2></div><div className="round-number"><span>ROUND</span><strong>{String(rounds + 1).padStart(2, '0')}</strong></div></div><div className="round-progress"><div className="round-progress-top"><span>Current round</span><strong>{Math.round((totalReps / 30) * 100)}%</strong></div><div className="progress-track"><span style={{ width: `${(totalReps / 30) * 100}%` }}></span></div></div><div className="exercise-list">{EXERCISES.map((exercise) => <button className={`exercise-row ${activeExercise === exercise.key ? 'selected' : ''}`} key={exercise.key} onClick={() => setActiveExercise(exercise.key)}><span className={`exercise-icon ${exercise.color}`}>{exercise.icon}</span><span className="exercise-name"><strong>{exercise.label}</strong><small>{reps[exercise.key] >= exercise.target ? 'Complete' : `${exercise.target - reps[exercise.key]} reps to go`}</small></span><span className="exercise-count"><b>{String(reps[exercise.key]).padStart(2, '0')}</b><i>/{exercise.target}</i></span>{reps[exercise.key] >= exercise.target && <Icon name="check" size={16} />}</button>)}</div><div className="round-total"><span>Completed rounds</span><strong>{rounds}</strong></div></section></div>
    <section className="rep-console panel"><div className="panel-header"><div><p className="eyebrow">DEMO CONTROLS</p><h2>Simulate movement</h2></div><label className="toggle-label"><span>Auto simulate</span><button className={`toggle ${autoSimulate ? 'on' : ''}`} onClick={() => setAutoSimulate(!autoSimulate)} aria-label="Toggle auto simulate"><span></span></button></label></div><div className="rep-console-body"><div className="focus-exercise"><span className={`exercise-icon ${currentExercise.color}`}>{currentExercise.icon}</span><div><small>ACTIVE MOVEMENT</small><strong>{currentExercise.label}</strong></div></div><button className="rep-btn" onClick={() => incrementRep()} disabled={reps[currentExercise.key] >= currentExercise.target}><span>+1</span> Add {currentExercise.label} rep</button><div className="exercise-shortcuts">{EXERCISES.map((exercise) => <button key={exercise.key} className={activeExercise === exercise.key ? 'active' : ''} onClick={() => { setActiveExercise(exercise.key); incrementRep(exercise.key) }}><span className={`mini-dot ${exercise.color}`}></span>{exercise.label}</button>)}</div></div><p className="demo-note"><Icon name="zap" size={14} /> Timer, exercise counters, and round score are simulated. Stored TinyIoT content below is read-only and does not update this session.</p></section>
    <StoredRepetitionPanel result={liveResult} />
    <section className="bottom-grid"><div className="insight-card panel"><div className="insight-icon">↗</div><div><p className="eyebrow">TODAY'S INSIGHT</p><h3>Consistency beats intensity.</h3><p>Keep your transitions smooth and save your sprint for the final 3 minutes.</p></div><span className="insight-tag">SIMULATED</span></div><div className="mini-stats panel"><div><span>Last workout</span><strong>4 rounds</strong><small>+1 from average</small></div><div><span>Weekly volume</span><strong>12.4k</strong><small>reps logged</small></div><div><span>Streak</span><strong>06 days</strong><small>Personal best</small></div></div></section>
  </div>
}

function HistoryPage() {
  const sessions = [['Oct 25, 2026', 'Sunday', '5 rounds + 12 reps', '19:42', 'Completed'], ['Oct 23, 2026', 'Friday', '4 rounds + 21 reps', '20:00', 'Completed'], ['Oct 21, 2026', 'Wednesday', '3 rounds + 28 reps', '20:00', 'Completed'], ['Oct 19, 2026', 'Monday', '4 rounds + 5 reps', '17:36', 'Stopped early']]
  return <div className="page-wrap"><section className="page-heading"><div><p className="eyebrow">PERFORMANCE LOG</p><h1>Workout history</h1><p className="subheading">A clear view of your work, one session at a time.</p></div><button className="outline-btn"><span>↗</span> Export data</button></section><section className="history-summary">{[['orange', '◒', 'SESSIONS THIS MONTH', '14', '+3 vs. last month'], ['blue', '↗', 'AVERAGE ROUNDS', '4.2', '+0.6 round improvement'], ['violet', '✦', 'BEST SESSION', '5.4', 'Oct 25, 2026']].map((item) => <div className="summary-card panel" key={item[2]}><span className={`summary-symbol ${item[0]}`}>{item[1]}</span><div><small>{item[2]}</small><strong>{item[3]}</strong><span>{item[4]}</span></div></div>)}</section><section className="panel history-panel"><div className="panel-header"><div><p className="eyebrow">RECENT SESSIONS</p><h2>Your training rhythm</h2></div><span className="history-filter">Last 30 days <Icon name="chevron" size={14} /></span></div><div className="history-table"><div className="history-row history-head"><span>DATE</span><span>RESULT</span><span>DURATION</span><span>STATUS</span><span></span></div>{sessions.map((session) => <div className="history-row" key={session[0]}><span><strong>{session[0]}</strong><small>{session[1]}</small></span><span className="result">{session[2]}</span><span className="duration">{session[3]}</span><span><em className={`status-chip ${session[4] === 'Completed' ? 'complete' : 'stopped'}`}>{session[4]}</em></span><button aria-label={`View ${session[0]} workout details`}><Icon name="chevron" size={16} /></button></div>)}</div></section></div>
}

function CindyPtPage() {
  const schedule = [['MON', 'Technique + mobility', 'Done', 'done'], ['TUE', 'Cindy AMRAP', 'Today', 'today'], ['WED', 'Active recovery', 'Next', 'next'], ['THU', 'Strength base', 'Upcoming', 'next']]
  return <div className="page-wrap"><section className="page-heading"><div><p className="eyebrow">YOUR DIGITAL COACH</p><h1>Cindy PT <span>✦</span></h1><p className="subheading">Small, smart adjustments that keep your training moving forward.</p></div><span className="coach-badge"><span className="pulse-dot"></span> SIMULATED COACH</span></section><div className="pt-grid"><section className="goal-card panel"><div className="goal-orb">↗</div><p className="eyebrow">CURRENT FITNESS GOAL</p><h2>Build your engine</h2><p>Complete 5 rounds of Cindy unbroken by the end of November.</p><div className="goal-progress"><div><span>Progress</span><strong>68%</strong></div><div className="progress-track"><span style={{ width: '68%' }}></span></div></div><span className="simulated-label">SIMULATED RECOMMENDATION</span></section><section className="panel schedule-card"><div className="panel-header"><div><p className="eyebrow">THIS WEEK</p><h2>Training schedule</h2></div><span className="week-label">Week 44</span></div><div className="schedule-list">{schedule.map((item) => <div className="schedule-item" key={item[0]}><strong>{item[0]}</strong><span>{item[1]}</span><em className={item[3]}>{item[2]}</em></div>)}</div></section><section className="panel rest-card"><div className="rest-icon">☼</div><div><p className="eyebrow">NEXT REST DAY</p><h2>Wednesday, October 28</h2><p>20 min walk · 10 min shoulder mobility · hydrate well</p></div></section><section className="panel recommendations"><div className="panel-header"><div><p className="eyebrow">COACH'S NOTES</p><h2>Recommendations for you</h2></div><span className="simulated-label">ALL SIMULATED</span></div><div className="recommendation-list"><div><span className="recommendation-number">01</span><p><strong>Break your push-ups into 5 + 5.</strong><br />You’ll preserve your shoulder rhythm for later rounds.</p><Icon name="chevron" size={16} /></div><div><span className="recommendation-number">02</span><p><strong>Try a 90-second warm-up.</strong><br />Prime your hips and wrists before the clock starts.</p><Icon name="chevron" size={16} /></div></div></section></div></div>
}

export default App
