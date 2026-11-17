export function nextLocalTimeForUtcClock(utc: string) {
  const next = new Date();
  const [hour, minute] = utc.split(':').map(Number);
  next.setUTCHours(hour, minute, 0, 0);
  if (next.getTime() < Date.now()) next.setUTCDate(next.getUTCDate() + 1);
  return new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', timeZoneName: 'short' }).format(next);
}
