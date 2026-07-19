import { create } from 'zustand';
import type { AuditEventType } from '../types/contracts';

interface UIState {
  selectedTaskId: string | null;
  auditFilterType: AuditEventType | 'ALL';
  setSelectedTask: (id: string | null) => void;
  setAuditFilterType: (t: AuditEventType | 'ALL') => void;
}

export const useUIStore = create<UIState>((set) => ({
  selectedTaskId: null,
  auditFilterType: 'ALL',
  setSelectedTask: (id) => set({ selectedTaskId: id }),
  setAuditFilterType: (t) => set({ auditFilterType: t }),
}));
