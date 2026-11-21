/** Keep references selected on other pages when the current page changes. */
export function selectedPageRows<T extends { key: string }>(previous: T[], pageKeys: Set<string>, selected: T[], preserveOtherPages: boolean, limit: number): T[] {
  const next = preserveOtherPages ? [...previous.filter(row => !pageKeys.has(row.key)), ...selected] : selected;
  if (next.length > limit) throw new Error(`Select up to ${limit} objects.`);
  return next;
}
