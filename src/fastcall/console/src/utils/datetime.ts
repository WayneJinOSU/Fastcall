export function formatDateTime(value: string | null | undefined, includeSeconds = false) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    ...(includeSeconds ? { second: '2-digit' } : {}), hour12: false,
  }).format(date);
}

export function formatTimelineTime(value: string | null | undefined) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit', minute: '2-digit', second: '2-digit', fractionalSecondDigits: 3, hour12: false,
  }).format(date);
}

export function shortId(value: string | null | undefined, length = 12) {
  if (!value) return '—';
  return value.length > length ? `${value.slice(0, length)}…` : value;
}
