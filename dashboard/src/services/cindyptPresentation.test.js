import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const component = (name) => readFileSync(new URL(`../components/${name}.jsx`, import.meta.url), 'utf8')

test('CindyPT presentation omits resource diagnostics and dedicated polling', () => {
  const source = component('CindyPtPage')
  assert.doesNotMatch(source, /Workout resources|ONEM2M MONITORING|groupResult|discoveryResult/)
  assert.doesNotMatch(source, /getWorkoutMonitoringGroup|discoverWorkoutResources/)
  assert.match(source, /Generate Recommendation/)
  assert.match(source, /<WeeklyPlanPanel/)
})

test('coach and saved recommendation keep actionable guidance without raw metadata', () => {
  const page = component('CindyPtPage')
  const saved = component('ApiPanels').split('export function StoredRecommendationPanel')[1].split('export function HistoryAvailability')[0]
  assert.doesNotMatch(page, /recommendations\.explanation|No target change is supported/)
  assert.match(page, /recoveryRecommendation/)
  assert.doesNotMatch(saved, /details\.explanation|recorded_at|stored-metadata|Retrieved from/)
  assert.match(saved, /Test recommendation|recoveryRecommendation|savedForCurrentGoal/)
})

test('weekly plan hides raw test goal names and keeps preview, save, and trust notices', () => {
  const source = component('WeeklyPlanPanel')
  assert.match(source, /Your Weekly Cindy Plan|Test plan|Limited workout history/)
  assert.doesNotMatch(source, /plan\.goal\.label|SAVED IN TINYIOT|TEST DATA/)
  assert.match(source, /validateWeeklyPlan|validateSavedRecovery|savePlanAfterConfirmation/)
  assert.match(source, /Preview Plan|Save Plan|Show Saved Plan/)
})

test('latest workout uses four selected-session statistics', () => {
  const source = component('CindyPtPage')
  assert.match(source, /Total Pull-ups|Total Push-ups|Total Squats|Fatigue Alerts/)
  assert.match(source, /selectedWorkoutStats|workoutStatDisplay|pt-workout-stats/)
  assert.match(source, /display\.numeric \? 'is-number' : 'is-status'/)
  assert.match(source, /fatigueAlertValue/)
  assert.doesNotMatch(source, /Detected-rep performance|pt-performance-trends|Movement quality is unverified|alertPresentation\.explanation/)
  assert.doesNotMatch(source, /Completion reason:|Observed reps:|WORKOUT SELECTED FOR COACHING|Summary \{latest\.summaryVerified/)
})
