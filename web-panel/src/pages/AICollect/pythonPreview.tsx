// Shared with the original collection editor; preserve its tokenization and colors.
type PythonPreviewTokenKind =
  | 'plain'
  | 'keyword'
  | 'builtin'
  | 'string'
  | 'comment'
  | 'number'
  | 'function-name'
  | 'class-name'
  | 'property'
  | 'decorator'
  | 'operator'
  | 'punctuation';
type PythonPreviewToken = {
  value: string;
  kind: PythonPreviewTokenKind;
};

const pythonPreviewKeywords = new Set([
  'and',
  'as',
  'async',
  'await',
  'class',
  'def',
  'elif',
  'else',
  'except',
  'finally',
  'for',
  'from',
  'if',
  'import',
  'in',
  'is',
  'lambda',
  'not',
  'or',
  'pass',
  'raise',
  'return',
  'try',
  'while',
  'with',
  'yield',
]);

const pythonPreviewBuiltins = new Set(['False', 'None', 'self', 'True']);

function tokenizePythonPreviewLine(line: string): PythonPreviewToken[] {
  const tokens: PythonPreviewToken[] = [];
  let index = 0;
  let previousSignificantKind: PythonPreviewTokenKind | null = null;
  let previousSignificantValue = '';

  const commitToken = (value: string, kind: PythonPreviewTokenKind) => {
    if (!value) {
      return;
    }
    const token = { value, kind };
    tokens.push(token);
    if (value.trim()) {
      previousSignificantKind = kind;
      previousSignificantValue = value;
    }
  };

  while (index < line.length) {
    const char = line[index];

    if (char === ' ' || char === '\t') {
      let end = index + 1;
      while (end < line.length && (line[end] === ' ' || line[end] === '\t')) {
        end += 1;
      }
      commitToken(line.slice(index, end), 'plain');
      index = end;
      continue;
    }

    if (char === '#') {
      commitToken(line.slice(index), 'comment');
      break;
    }

    if (char === '@') {
      let end = index + 1;
      while (end < line.length && /[A-Za-z0-9_.]/.test(line[end])) {
        end += 1;
      }
      commitToken(line.slice(index, end), 'decorator');
      index = end;
      continue;
    }

    if (char === '"' || char === "'") {
      const quote = char;
      let end = index + 1;
      while (end < line.length) {
        if (line[end] === '\\') {
          end += 2;
          continue;
        }
        if (line[end] === quote) {
          end += 1;
          break;
        }
        end += 1;
      }
      commitToken(line.slice(index, end), 'string');
      index = end;
      continue;
    }

    if (/\d/.test(char)) {
      let end = index + 1;
      while (end < line.length && /[\d._]/.test(line[end])) {
        end += 1;
      }
      commitToken(line.slice(index, end), 'number');
      index = end;
      continue;
    }

    if (/[A-Za-z_]/.test(char)) {
      let end = index + 1;
      while (end < line.length && /[A-Za-z0-9_]/.test(line[end])) {
        end += 1;
      }

      const value = line.slice(index, end);
      let kind: PythonPreviewTokenKind = 'plain';
      let nextNonSpaceIndex = end;
      while (nextNonSpaceIndex < line.length && /\s/.test(line[nextNonSpaceIndex])) {
        nextNonSpaceIndex += 1;
      }
      let previousNonSpaceIndex = index - 1;
      while (previousNonSpaceIndex >= 0 && /\s/.test(line[previousNonSpaceIndex])) {
        previousNonSpaceIndex -= 1;
      }

      const nextNonSpaceChar = line[nextNonSpaceIndex] ?? '';
      const previousNonSpaceChar = previousNonSpaceIndex >= 0 ? line[previousNonSpaceIndex] : '';

      if (pythonPreviewKeywords.has(value)) {
        kind = 'keyword';
      } else if (pythonPreviewBuiltins.has(value)) {
        kind = 'builtin';
      } else if (previousSignificantKind === 'keyword' && previousSignificantValue === 'class') {
        kind = 'class-name';
      } else if (previousSignificantKind === 'keyword' && previousSignificantValue === 'def') {
        kind = 'function-name';
      } else if (nextNonSpaceChar === '(') {
        kind = 'function-name';
      } else if (previousNonSpaceChar === '.') {
        kind = 'property';
      } else if (/^[A-Z]/.test(value)) {
        kind = 'class-name';
      }

      commitToken(value, kind);
      index = end;
      continue;
    }

    if (/[=:+\-*/%<>!&|]/.test(char)) {
      let end = index + 1;
      while (end < line.length && /[=:+\-*/%<>!&|]/.test(line[end])) {
        end += 1;
      }
      commitToken(line.slice(index, end), 'operator');
      index = end;
      continue;
    }

    commitToken(char, 'punctuation');
    index += 1;
  }

  return tokens;
}

export function renderPythonPreviewContent(line: string, keyPrefix: string) {
  const tokens = tokenizePythonPreviewLine(line);

  if (!tokens.length) {
    return ' ';
  }

  return tokens.map((token, index) => (
    <span className={`ai-session-python-token is-${token.kind}`} key={`${keyPrefix}-${index}`}>
      {token.value}
    </span>
  ));
}
