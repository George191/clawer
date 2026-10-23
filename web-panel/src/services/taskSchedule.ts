const intervalUnits = new Set(['minute', 'hour', 'day', 'week', 'month']);

export const formatWorkspaceScheduleLabel = (
  schedule: Record<string, unknown> | undefined,
  fallback = 'Waiting',
): string => {
  const mode = schedule?.mode === 'recurring' ? schedule.recurring_mode : schedule?.mode;
  if (mode === 'once') return 'One-time task';
  if (mode === 'daily') {
    const time = (String(schedule?.daily_time ?? '') || '09:00').replace(
      /^(\d{1,2}):(\d{1,2})$/,
      (_, hour: string, minute: string) => `${hour.padStart(2, '0')}:${minute.padStart(2, '0')}`,
    );
    return `Daily at ${time} (Asia/Shanghai)`;
  }
  if (mode === 'interval') {
    const value = Number(schedule?.interval_value === '' ? 30 : schedule?.interval_value ?? 30);
    const requestedUnit = String(schedule?.interval_unit ?? 'minute');
    const unit = intervalUnits.has(requestedUnit) ? requestedUnit : 'minute';
    return `Every ${value} ${unit}${value === 1 ? '' : 's'}`;
  }
  return String(schedule?.label ?? fallback);
};
