import { get } from './client';
import type { ExperimentCase, ExperimentResult } from '../types/experiments';

export function listResults(): Promise<{ name: string; size: number; modified: number }[]> {
  return get('/experiments/results');
}

export function getResult(filename: string): Promise<{ filename: string; results: ExperimentResult[] }> {
  return get(`/experiments/results/${encodeURIComponent(filename)}`);
}

export function listCases(): Promise<ExperimentCase[]> {
  return get('/experiments/cases');
}
