export function parsePropertyValue(value: string, type: string) {
  if (type === 'integer') {
    if (!/^-?\d+$/.test(value) || !Number.isSafeInteger(Number(value))) throw new Error('Enter a whole number within the safe integer range.');
    return Number(value);
  }
  if (type === 'number') {
    if (!value.trim() || !Number.isFinite(Number(value))) throw new Error('Enter a finite number.');
    return Number(value);
  }
  if (type === 'boolean') {
    if (value !== 'true' && value !== 'false') throw new Error('Enter true or false.');
    return value === 'true';
  }
  if (type === 'json') { try { return JSON.parse(value); } catch { throw new Error('Enter valid JSON.'); } }
  return value;
}
