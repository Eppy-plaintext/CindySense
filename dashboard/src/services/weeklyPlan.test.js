import assert from 'node:assert/strict'
import test from 'node:test'
import { savePlanAfterConfirmation } from './weeklyPlan.js'

test('weekly plan save requires explicit confirmation', async () => {
  const plan = { planId: 'a'.repeat(64), weekStart: '2026-11-09' }
  const preferences = { timezone: 'Asia/Seoul', trainingDays: [0, 2, 4], experienceLevel: 'beginner' }
  let writes = 0
  const save = async (body) => { writes += 1; return body }
  assert.equal(await savePlanAfterConfirmation(plan, preferences, () => false, save), false)
  assert.equal(writes, 0)
  const result = await savePlanAfterConfirmation(plan, preferences, () => true, save)
  assert.equal(writes, 1)
  assert.deepEqual(result, { preferences, planId: plan.planId })
})
