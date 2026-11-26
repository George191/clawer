// Shared historical streaming YAML parser: nesting, sequences and block scalars.
export type YamlEntry = { id: string; key: string; value: string; nodeType: 'group' | 'value'; multiline: boolean; depth: number };
const isStructuredInlineYamlValue = (value: string) => {
  const trimmed = value.trim();
  if (!trimmed) return false;
  if (trimmed.startsWith('[') && trimmed.endsWith(']')) return true;
  if (trimmed.startsWith('{') && trimmed.endsWith('}') && /[:,]/.test(trimmed.slice(1, -1))) return true;
  return false;
};
export function parseTemplateEntries<T>(raw: string, classify: (key: string, path: string) => T): Array<YamlEntry & T> {
  const entries: Array<YamlEntry & T> = [];
  const lines = raw.replace(/\r\n/g, '\n').replace(/\t/g, '  ').split('\n');
  const indentationUnit = (() => {
    const indents = lines
      .map((line) => line.match(/^\s+/)?.[0].length ?? 0)
      .filter((indent) => indent > 0);
    if (!indents.length) return 2;
    const gcd = (left: number, right: number): number => right ? gcd(right, left % right) : left;
    return Math.max(1, indents.reduce(gcd));
  })();
  const keyPathStack: Array<{ indent: number; path: string }> = [];
  const listItemContextStack: Array<{ indent: number; path: string }> = [];
  const listIndexMap = new Map<string, number>();

  const normalizeYamlValue = (value: string) => {
    const trimmed = value.trim();
    return trimmed ? trimmed : 'null';
  };

  const collectBlockValue = (startIndex: number, parentIndent: number) => {
    const blockLines: string[] = [];
    let nextIndex = startIndex;

    while (nextIndex < lines.length) {
      const nextLine = lines[nextIndex];
      const nextTrimmed = nextLine.trim();

      if (!nextTrimmed) {
        blockLines.push('');
        nextIndex += 1;
        continue;
      }

      const nextIndent = nextLine.match(/^\s*/)?.[0].length ?? 0;
      if (nextIndent <= parentIndent) break;

      blockLines.push(nextLine.slice(Math.min(nextLine.length, parentIndent + 2)));
      nextIndex += 1;
    }

    return {
      nextIndex: nextIndex - 1,
      value: blockLines.join('\n').trimEnd() || 'null',
    };
  };

  const pruneStacks = (indent: number, isListItem: boolean) => {
    while (
      keyPathStack.length
      && (
        keyPathStack[keyPathStack.length - 1].indent > indent
        || (!isListItem && keyPathStack[keyPathStack.length - 1].indent >= indent)
      )
    ) {
      keyPathStack.pop();
    }
    while (
      listItemContextStack.length
      && (
        listItemContextStack[listItemContextStack.length - 1].indent > indent
        || (isListItem && listItemContextStack[listItemContextStack.length - 1].indent === indent)
      )
    ) {
      listItemContextStack.pop();
    }
  };

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;

    const indent = line.match(/^\s*/)?.[0].length ?? 0;
    const isListItem = trimmed.startsWith('- ');
    pruneStacks(indent, isListItem);

    const keyPathParent = keyPathStack[keyPathStack.length - 1];
    const listItemParent = listItemContextStack[listItemContextStack.length - 1];
    const parentPath = listItemParent && (!keyPathParent || listItemParent.indent >= keyPathParent.indent)
      ? listItemParent.path
      : keyPathParent?.path ?? '';
    const depth = Math.max(0, Math.floor(indent / indentationUnit));

    const listItemMatch = trimmed.match(/^- (.+)$/);
    if (listItemMatch) {
      const listBase = parentPath || 'list';
      const listKey = `${listBase}@${indent}`;
      const listIndex = (listIndexMap.get(listKey) ?? -1) + 1;
      const itemPath = `${listBase}[${listIndex}]`;
      listIndexMap.set(listKey, listIndex);

      const value = listItemMatch[1];
      const nestedKeyMatch = value.match(/^([A-Za-z_][\w-]*):(?:\s*(.*))?$/);
      if (nestedKeyMatch) {
        const keyName = nestedKeyMatch[1];
        const rawValue = (nestedKeyMatch[2] ?? '').trim();
        const key = `${itemPath}.${keyName}`;
        let keyValue = normalizeYamlValue(rawValue);
        let multiline = false;

        entries.push({
          id: itemPath,
          key: itemPath,
          value: '',
          nodeType: 'group',
          ...classify(listBase.split('.').pop() ?? listBase, itemPath),
          multiline: false,
          depth,
        });

        if (rawValue === '|' || rawValue === '>') {
          const block = collectBlockValue(index + 1, indent);
          keyValue = block.value;
          multiline = true;
          index = block.nextIndex;
        } else if (isStructuredInlineYamlValue(keyValue)) {
          multiline = true;
        }

        entries.push({
          id: key,
          key,
          value: keyValue,
          nodeType: rawValue ? 'value' : 'group',
          ...classify(keyName, key),
          multiline,
          depth: depth + 1,
        });

        if (!rawValue) {
          listItemContextStack.push({ indent, path: itemPath });
          keyPathStack.push({ indent: indent + 2, path: key });
        } else {
          listItemContextStack.push({ indent, path: itemPath });
        }
      } else {
        entries.push({
          id: itemPath,
          key: itemPath,
          value: normalizeYamlValue(value),
          nodeType: 'value',
          ...classify(listBase.split('.').pop() ?? listBase, itemPath),
          multiline: value.includes('[') || value.includes('{'),
          depth,
        });
      }
      continue;
    }

    const keyMatch = trimmed.match(/^([A-Za-z_][\w-]*):(?:\s*(.*))?$/);
    if (!keyMatch) continue;

    const keyName = keyMatch[1];
    const rawValue = (keyMatch[2] ?? '').trim();
    if (indent === 0 && keyName === 'priority') continue;
    const path = parentPath ? `${parentPath}.${keyName}` : keyName;
    let value = normalizeYamlValue(rawValue);
    let multiline = false;

    if (rawValue === '|' || rawValue === '>') {
      const block = collectBlockValue(index + 1, indent);
      value = block.value;
      multiline = true;
      index = block.nextIndex;
    } else if (isStructuredInlineYamlValue(value)) {
      multiline = true;
    }

    entries.push({
      id: path,
      key: path,
      value,
      nodeType: rawValue ? 'value' : 'group',
      ...classify(keyName, path),
      multiline,
      depth,
    });

    if (!rawValue) {
      keyPathStack.push({ indent, path });
    }
  }

  return entries;
};
