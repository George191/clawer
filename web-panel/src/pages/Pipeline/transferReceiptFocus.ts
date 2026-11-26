export const canonicalTransferReceipt = (value: string) => /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value);
export function exactTransferReceiptFocus(params: URLSearchParams, schema: string, table: string): string | undefined {
  const exact = (key: string, expected: string) => {
    const occurrences = params.getAll(key);
    return occurrences.length === 1 && occurrences[0] === expected;
  };
  const receipts = params.getAll('transferReceipt');
  return exact('schema', schema) && exact('table', table) && exact('view', 'evidence') &&
    receipts.length === 1 && canonicalTransferReceipt(receipts[0]) ? receipts[0] : undefined;
}
