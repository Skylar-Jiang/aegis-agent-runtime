import { create } from 'zustand';
import type { AuditEventType } from '../types/contracts';

interface TaskEntry {
  taskId: string;
  objective: string;
  createdAt: string;
  status: string;
}

interface UIState {
  selectedTaskId: string | null;
  auditFilterType: AuditEventType | 'ALL';
  taskHistory: TaskEntry[];
  cancelledTaskIds: string[];
  setSelectedTask: (id: string | null) => void;
  setAuditFilterType: (t: AuditEventType | 'ALL') => void;
  addTask: (task: TaskEntry) => void;
  markCancelled: (taskId: string) => void;
}

export const useUIStore = create<UIState>((set) => ({
  selectedTaskId: null,
  auditFilterType: 'ALL',
  taskHistory: [],
  cancelledTaskIds: [],
  setSelectedTask: (id) => set({ selectedTaskId: id }),
  setAuditFilterType: (t) => set({ auditFilterType: t }),
  addTask: (task) =>
    set((state) => ({
      taskHistory: [task, ...state.taskHistory].slice(0, 50),
    })),
  markCancelled: (taskId) =>
    set((state) => ({
      cancelledTaskIds: state.cancelledTaskIds.includes(taskId)
        ? state.cancelledTaskIds
        : [...state.cancelledTaskIds, taskId],
    })),
}));
