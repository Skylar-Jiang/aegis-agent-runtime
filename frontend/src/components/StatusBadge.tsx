interface Props {
  status: string;
}

const colors: Record<string, string> = {
  CREATED: 'border-sky-400/30 bg-sky-400/10 text-sky-200',
  PLANNED: 'border-slate-500/30 bg-slate-500/10 text-slate-300',
  ACTIVE: 'border-sky-400/30 bg-sky-400/10 text-sky-200',
  PENDING: 'border-amber-400/30 bg-amber-400/10 text-amber-200',
  WAITING_APPROVAL: 'border-amber-400/30 bg-amber-400/10 text-amber-200',
  GRANTED: 'border-emerald-400/30 bg-emerald-400/10 text-emerald-200',
  DENIED: 'border-rose-400/30 bg-rose-400/10 text-rose-200',
  EXPIRED: 'border-orange-400/30 bg-orange-400/10 text-orange-200',
  CANCELLED: 'border-slate-500/30 bg-slate-500/10 text-slate-400',
  COMMITTED: 'border-emerald-400/30 bg-emerald-400/10 text-emerald-200',
  BLOCKED: 'border-rose-400/30 bg-rose-400/10 text-rose-200',
  ROLLED_BACK: 'border-violet-400/30 bg-violet-400/10 text-violet-200',
  FAILED: 'border-rose-400/30 bg-rose-400/10 text-rose-200',
};

export function StatusBadge({ status }: Props) {
  const color = colors[status] ?? 'border-slate-500/30 bg-slate-500/10 text-slate-300';
  return (
    <span className={`inline-block rounded-full border px-2 py-0.5 text-[10px] font-medium tracking-wide ${color}`}>
      {status}
    </span>
  );
}
