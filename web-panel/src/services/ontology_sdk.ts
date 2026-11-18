import type { OntologyRecordPage, PublishedOntologyType, SemanticProperty } from './ontology';

type Actions = NonNullable<OntologyRecordPage['actions']>;
export type OntologySdkFunction = { key: string; name: string; parameters: Array<{ key: string; type: string; nullable: boolean }> };
const wireTypes: Record<string, string> = {
  string: 'string', date: 'string', datetime: 'string',
  integer: 'number', number: 'number', boolean: 'boolean', json: 'JsonValue',
};

function members(properties: Pick<SemanticProperty, 'id' | 'type' | 'required'>[], kind: 'read' | 'create' | 'modify' | 'function') {
  if (!properties.length) return 'Record<string, never>';
  return '{\n' + properties.map(property => {
    const type = property.type === 'json' && property.required ? 'NonNullable<JsonValue>' : wireTypes[property.type] ?? 'unknown';
    const optional = kind === 'read' || kind === 'modify' || (kind === 'create' && !property.required);
    return '  ' + (kind === 'read' ? 'readonly ' : '') + JSON.stringify(property.id) +
      (optional ? '?' : '') + ': ' + type + (property.required || property.type === 'json' ? '' : ' | null') + ';';
  }).join('\n') + '\n}';
}

function actionFields(properties: SemanticProperty[], actions: Actions, kind: 'create' | 'modify') {
  const fields = new Set(actions.find(action => action.kind === kind)?.fields);
  return properties.filter(property => fields.has(property.id));
}

export function generateOntologySdk(object: PublishedOntologyType, available: Actions, functions: OntologySdkFunction[] = []) {
  const { properties, source, titleKey } = object.definition;
  const editable = source.mode === 'none';
  const visible = new Set(properties.map(property => property.id));
  const actions = editable
    ? available.filter(action => action.fields.every(field => visible.has(field)))
    : [];
  const title = properties.find(property => property.id === titleKey);
  const titleType = title ? (wireTypes[title.type] ?? 'unknown') + ' | null' : 'null';
  const integerFields = properties.filter(property => property.type === 'integer').map(property => property.id);
  const create = actions.some(action => action.kind === 'create');
  const modify = actions.some(action => action.kind === 'modify');
  const remove = actions.some(action => action.kind === 'delete');
  const write = create || modify || remove;
  const callable = editable ? functions : [];
  const functionTypes = callable.map(operation => '  ' + JSON.stringify(operation.key) + ': ' + members(
    operation.parameters.map(parameter => ({ id: parameter.key, type: parameter.type, required: !parameter.nullable })), 'function',
  ) + ';');
  const sdkImports = [
    '// Generated TypeScript client. Use the access token of the caller, never a shared credential.',
    '// Field visibility and actions are a snapshot of the exporting user’s access. The server authorizes every request.',
    '// The JSON API represents integers as JavaScript numbers. Unsafe integer values are rejected.',
    'export const objectTypeId = ' + JSON.stringify(object.id) + ';',
    'export const publishedRevision = ' + object.revision + ';',
    'export type JsonValue = null | string | number | boolean | JsonValue[] | { [key: string]: JsonValue };',
    'export type ObjectProperties = ' + members(properties, 'read') + ';',
    'export type ObjectRecord = { id: string | null; revision: number | null; title: ' + titleType + '; properties: ObjectProperties };',
    'export type ObjectRecordsPage = { items: ObjectRecord[]; hasMore: boolean };',
    ...(create ? ['export type CreateObjectInput = ' + members(actionFields(properties, actions, 'create'), 'create') + ';'] : []),
    ...(modify ? ['export type ModifyObjectInput = ' + members(actionFields(properties, actions, 'modify'), 'modify') + ';'] : []),
    ...(write ? ['export type ActionReceipt = { id: string; revision: number; deleted: boolean };'] : []),
    ...(callable.length ? [
      'export type TransactionReceipt = { id: string; name: string; stepCount: number; createdAt: string; undoneAt: string | null };',
      'export type FunctionArguments = {', ...functionTypes, '};',
    ] : []),
  ];
  const methods = [
    '    async listRecords(options: { limit?: number; offset?: number' + (editable ? '; deleted?: boolean' : '') + '; signal?: AbortSignal } = {}) {',
    '      const query = new URLSearchParams({',
    '        limit: String(options.limit ?? 25), offset: String(options.offset ?? 0),',
    ...(editable ? ['        ...(options.deleted ? { deleted: "true" } : {}),'] : []),
    '      });',
    '      const page = await request<ObjectRecordsPage>(objectPath + "/records?" + query, { signal: options.signal });',
    '      page.items.forEach(checkRecord);',
    '      return page;',
    '    },',
    ...(editable ? [
      '    async getRecord(id: string, signal?: AbortSignal) {',
      '      const record = await request<ObjectRecord>(objectPath + "/records/" + encodeURIComponent(id), { signal });',
      '      checkRecord(record);',
      '      return record;',
      '    },',
    ] : []),
    ...(create ? [
      '    createRecord(values: CreateObjectInput, signal?: AbortSignal) {',
      '      checkIntegers(values);',
      '      return request<ActionReceipt>(objectPath + "/actions/create", { method: "POST", body: JSON.stringify({ values }), signal });',
      '    },',
    ] : []),
    ...(modify ? [
      '    modifyRecord(recordId: string, expectedRevision: number, values: ModifyObjectInput, signal?: AbortSignal) {',
      '      checkIntegers(values);',
      '      return request<ActionReceipt>(objectPath + "/actions/modify", { method: "POST", body: JSON.stringify({ recordId, expectedRevision, values }), signal });',
      '    },',
    ] : []),
    ...(remove ? [
      '    deleteRecord(recordId: string, expectedRevision: number, signal?: AbortSignal) {',
      '      return request<ActionReceipt>(objectPath + "/actions/delete", { method: "POST", body: JSON.stringify({ recordId, expectedRevision }), signal });',
      '    },',
    ] : []),
    ...(callable.length ? [
      '    functions: {',
      ...callable.flatMap(operation => {
        const key = JSON.stringify(operation.key);
        const parameters = operation.parameters.filter(parameter => parameter.type === 'integer').map(parameter => parameter.key);
        return [
          '      [' + key + '](input: { requestId: string; recordId: string; expectedRevision: number; arguments: FunctionArguments[' + key + '] }, signal?: AbortSignal) {',
          '        checkIntegers(input.arguments, ' + JSON.stringify(parameters) + ');',
          '        return request<TransactionReceipt>(objectPath + "/functions/" + encodeURIComponent(' + key + ') + "/invoke",',
          '          { method: "POST", body: JSON.stringify({ ...input, modelRevision: publishedRevision }), signal });',
          '      },',
        ];
      }),
      '    },',
    ] : []),
  ];
  return sdkImports.join('\n') + '\n\n' + (
`export type OntologyClientOptions = {
  baseUrl: string | URL;
  getAccessToken: () => string | Promise<string>;
  fetch?: typeof fetch;
};

export class OntologySdkError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
    this.name = "OntologySdkError";
  }
}

const objectPath = "/api/ontology/v1/object-types/" + encodeURIComponent(objectTypeId);
const integerFields: readonly string[] = ${JSON.stringify(integerFields)};

function checkIntegers(values: Record<string, unknown>, fields: readonly string[] = integerFields) {
  for (const field of fields) {
    const value = values[field];
    if (value != null && !Number.isSafeInteger(value)) {
      throw new RangeError("The value of " + field + " is outside the safe JavaScript integer range.");
    }
  }
}

function checkRecord(record: ObjectRecord) {
  checkIntegers(record.properties);
  ${title?.type === 'integer' ? 'checkIntegers({ [' + JSON.stringify(title.id) + ']: record.title });' : ''}
}

export function createOntologyClient(options: OntologyClientOptions) {
  const base = new URL(options.baseUrl);
  if (!["http:", "https:"].includes(base.protocol) || base.username || base.password) {
    throw new TypeError("Use an HTTP(S) API base URL without credentials.");
  }
  const fetcher = options.fetch ?? globalThis.fetch.bind(globalThis);
  async function request<T>(path: string, init: RequestInit): Promise<T> {
    const token = (await options.getAccessToken()).trim();
    if (!token) throw new OntologySdkError(401, "An access token is required.");
    const headers = new Headers({ Accept: "application/json", Authorization: "Bearer " + token });
    if (init.body != null) headers.set("Content-Type", "application/json");
    const response = await fetcher(new URL(path, base), { ...init, headers, redirect: "error" });
    const payload: unknown = await response.json().catch(() => undefined);
    if (!response.ok) {
      const detail = payload && typeof payload === "object"
        ? ("errors" in payload ? payload.errors : "detail" in payload ? payload.detail : undefined)
        : undefined;
      throw new OntologySdkError(response.status, typeof detail === "string" ? detail : "Ontology request failed.");
    }
    if (payload === undefined) throw new OntologySdkError(response.status, "Expected a JSON response.");
    return (payload && typeof payload === "object" && "code" in payload && "data" in payload ? payload.data : payload) as T;
  }
  return {
${methods.join('\n')}
  };
}
`);
}

export function ontologySdkFilename(object: PublishedOntologyType) {
  return object.definition.id.replace(/[^a-zA-Z0-9_-]/g, '-') + '-ontology-sdk.ts';
}
