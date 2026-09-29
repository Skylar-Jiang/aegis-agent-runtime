import { get } from './client'
import type { ExperimentCase, ExperimentResult } from '../types/experiments'

export function listResults(): Promise<
  { name: string; size: number; modified: number }[]
> {
  return get('/experiments/results')
}

export function getResult(
  filename: string,
): Promise<{ filename: string; results: ExperimentResult[] }> {
  return get(`/experiments/results/${encodeURIComponent(filename)}`)
}

export interface FinalEvidenceSummary {
  runtime_boundary: {
    contained_proposals: number
    proposals: number
    unsafe_runs: number
    unsafe_effects: number
  }
  scheduling: {
    approval_only: number
    adaptive_runtime: number
    dependency_conflict_violations: number
  }
  recovery: {
    approval_only_scope: number
    aegis_scope: number
    preservation_rate: number
    preservation_precision: number
  }
}

export function getFinalEvidence(): Promise<FinalEvidenceSummary> {
  return get('/experiments/final-evidence')
}

export interface CoreExperimentRun {
  id: string
  title: string
  source: string
  recorded_at: string
  sha256: string
  data: Record<string, unknown>
}

export function getCoreExperiments(): Promise<{
  recorded: true
  runs: CoreExperimentRun[]
  unavailable?: Array<{ id: string; error: string }>
}> {
  return get('/v1/experiments/core')
}

export function listCases(): Promise<ExperimentCase[]> {
  return get('/experiments/cases')
}
