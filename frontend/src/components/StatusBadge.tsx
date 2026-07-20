interface Props {
  status: string;
}

const colors: Record<string, string> = {
  CREATED: 'bg-blue-100 text-blue-800',
  PLANNED: 'bg-gray-100 text-gray-800',
  ACTIVE: 'bg-green-100 text-green-800',
  PENDING: 'bg-yellow-100 text-yellow-800',
  GRANTED: 'bg-green-100 text-green-800',
  DENIED: 'bg-red-100 text-red-800',
  EXPIRED: 'bg-orange-100 text-orange-800',
  CANCELLED: 'bg-gray-100 text-gray-500',
  COMMITTED: 'bg-green-100 text-green-800',
  BLOCKED: 'bg-red-100 text-red-800',
};

export function StatusBadge({ status }: Props) {
  const color = colors[status] ?? 'bg-gray-100 text-gray-600';
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${color}`}>
      {status}
    </span>
  );
}
