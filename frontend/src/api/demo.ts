import { get, post } from './client'

export type DemoScenario = 'boundary' | 'scheduling' | 'recovery' | 'conflict'

export function resetDemoWorkspace(): Promise<{ workspace: string }> {
  return post('/demo/reset')
}
export function startDemoScenario(
  scenario: DemoScenario,
): Promise<{ task_id: string; graph_id: string; scenario: string }> {
  return post(`/demo/scenarios/${scenario}`)
}
export function previewDemoRecovery(
  taskId: string,
  selectedEffectIds: string[],
): Promise<RecoveryView> {
  return post(
    `/demo/scenarios/${encodeURIComponent(taskId)}/recovery-preview`,
    { selected_effect_ids: selectedEffectIds },
  )
}
export function recoverDemoScenario(
  taskId: string,
  selectedEffectIds: string[],
): Promise<RecoveryView> {
  return post(`/demo/scenarios/${encodeURIComponent(taskId)}/recover`, {
    selected_effect_ids: selectedEffectIds,
  })
}
export function applyDemoUserModification(
  taskId: string,
): Promise<{ status: string }> {
  return post(`/demo/scenarios/${encodeURIComponent(taskId)}/user-modification`)
}
export interface RecoveryView {
  trigger: string
  selected_effect_ids: string[]
  affected_effect_ids: string[]
  rollback_effect_ids: string[]
  preserve_effect_ids: string[]
  conflict_effect_ids: string[]
}
export function getDemoRecovery(taskId: string): Promise<RecoveryView | null> {
  return get(`/demo/scenarios/${encodeURIComponent(taskId)}/recovery`)
}
