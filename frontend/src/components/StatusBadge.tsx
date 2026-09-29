import { localizeStatus, useI18n } from '../i18n'

interface Props {
  status: string
}

const colors: Record<string, string> = {
  CREATED: 'border-sky-400/30 bg-sky-400/10 text-indigo-600',
  PLANNED: 'border-slate-500/30 bg-slate-500/10 text-slate-600',
  ACTIVE: 'border-sky-400/30 bg-sky-400/10 text-indigo-600',
  RUNNING: 'border-sky-400/30 bg-sky-400/10 text-indigo-600',
  PENDING: 'border-amber-400/30 bg-amber-400/10 text-amber-700',
  WAITING_APPROVAL: 'border-amber-400/30 bg-amber-400/10 text-amber-700',
  GRANTED: 'border-emerald-400/30 bg-emerald-400/10 text-emerald-700',
  DENIED: 'border-rose-400/30 bg-rose-400/10 text-rose-700',
  EXPIRED: 'border-orange-400/30 bg-orange-400/10 text-amber-700',
  CANCELLED: 'border-slate-500/30 bg-slate-500/10 text-slate-500',
  INTERRUPTED: 'border-orange-400/30 bg-orange-400/10 text-amber-700',
  COMPLETED: 'border-emerald-400/30 bg-emerald-400/10 text-emerald-700',
  COMMITTED: 'border-emerald-400/30 bg-emerald-400/10 text-emerald-700',
  BLOCKED: 'border-rose-400/30 bg-rose-400/10 text-rose-700',
  ROLLED_BACK: 'border-violet-400/30 bg-violet-400/10 text-violet-700',
  PRESERVED: 'border-sky-400/30 bg-sky-400/10 text-indigo-600',
  CONFLICT: 'border-orange-400/30 bg-orange-400/10 text-amber-700',
  FAILED: 'border-rose-400/30 bg-rose-400/10 text-rose-700',
}

export function StatusBadge({ status }: Props) {
  const { language } = useI18n()
  const color =
    colors[status] ?? 'border-slate-500/30 bg-slate-500/10 text-slate-600'
  return (
    <span
      className={`inline-block rounded-full border px-2 py-0.5 text-[10px] font-medium tracking-wide ${color}`}
    >
      {localizeStatus(status, language)}
    </span>
  )
}
