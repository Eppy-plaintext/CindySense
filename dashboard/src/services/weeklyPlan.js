export async function savePlanAfterConfirmation(preview, preferences, confirm, save) {
  if (!preview?.planId || !confirm(`Save this rule-based plan for the week of ${preview.weekStart} to TinyIoT?`)) return false
  return save({ preferences, planId: preview.planId })
}
