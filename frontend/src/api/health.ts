export interface RuntimeHealth {
  status: string
  phase: string
  mode: string
}

export async function getRuntimeHealth(): Promise<RuntimeHealth> {
  const response = await fetch(apiUrl('/health'))
  if (!response.ok) throw new Error('Runtime health unavailable')
  const payload = (await response.json()) as { data?: RuntimeHealth }
  if (!payload.data) throw new Error('Runtime health unavailable')
  return payload.data
}
import { apiUrl } from './url'
