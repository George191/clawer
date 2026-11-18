export function nextLocalTimeForUtcClock(utc: string) {
  const next = new Date();
  const [hour, minute] = utc.split(':').map(Number);
  next.setUTCHours(hour, minute, 0, 0);
  if (next.getTime() < Date.now()) next.setUTCDate(next.getUTCDate() + 1);
  return new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', timeZoneName: 'short' }).format(next);
}

export const isoWeekdaysUtc = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'] as const;

export function isoWeekdayName(value?: number | null) {
  return typeof value === 'number' && Number.isInteger(value) && value >= 1 && value <= 7
    ? isoWeekdaysUtc[value - 1] : undefined;
}

export function isUtcMonthDay(value?: number | null): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 1 && value <= 28;
}
