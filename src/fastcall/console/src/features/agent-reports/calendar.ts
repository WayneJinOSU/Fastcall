export const DAY_MS = 24 * 60 * 60 * 1000;
export const SHANGHAI_OFFSET = '+08:00';

export function dateKey(value: Date) {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
}

export function shiftDateKey(value: string, days: number) {
  const shifted = new Date(toUtc(value, '00:00').getTime() + days * DAY_MS + 8 * 60 * 60 * 1000);
  return shifted.toISOString().slice(0, 10);
}

export function toLocalDateTime(value: string | undefined, fallback: Date) {
  const date = value ? new Date(value) : fallback;
  const local = new Date(date.getTime() + 8 * 60 * 60 * 1000);
  return {
    date: local.toISOString().slice(0, 10),
    time: local.toISOString().slice(11, 16),
  };
}

export function toUtc(date: string, time: string) {
  return new Date(`${date}T${time}:00${SHANGHAI_OFFSET}`);
}

export function naturalDayCount(startDate: string, endDate: string, startTime: string, endTime: string) {
  const exclusiveEnd = toUtc(endDate, endTime).getTime();
  const lastLocalDate = new Date(exclusiveEnd - 1 + 8 * 60 * 60 * 1000)
    .toISOString().slice(0, 10);
  const start = toUtc(startDate, startTime).getTime();
  const last = toUtc(lastLocalDate, '00:00').getTime();
  return Math.floor((last - start) / DAY_MS) + 1;
}

export function calendarDates(month: Date) {
  const year = month.getFullYear();
  const monthIndex = month.getMonth();
  const offset = (new Date(year, monthIndex, 1).getDay() + 6) % 7;
  const count = new Date(year, monthIndex + 1, 0).getDate();
  return [
    ...Array.from({ length: offset }, () => ''),
    ...Array.from({ length: count }, (_, index) => dateKey(new Date(year, monthIndex, index + 1))),
  ];
}
