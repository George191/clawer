import { Fragment, useEffect, useState } from 'react';
import type { ApiSnippetLanguage, highlightApiCode } from './highlightApiCode';

type Highlight = Awaited<ReturnType<typeof highlightApiCode>>;

export default function ApiCodeSnippet({ source, language }: { source: string; language: ApiSnippetLanguage }) {
  const [result, setResult] = useState<{ source: string; language: ApiSnippetLanguage; highlight?: Highlight }>();
  useEffect(() => {
    let current = true;
    void import('./highlightApiCode')
      .then(module => module.highlightApiCode(source, language))
      .then(
        highlight => { if (current) setResult({ source, language, highlight }); },
        () => { if (current) setResult({ source, language }); },
      );
    return () => { current = false; };
  }, [source, language]);

  const active = result?.source === source && result.language === language ? result : undefined;
  const highlight = active?.highlight;
  return <>
    {active && !highlight && <small role="status">Syntax highlighting unavailable</small>}
    <pre className="api-code-snippet" data-language={language} style={{ backgroundColor: highlight?.bg, color: highlight?.fg }}><code>
      {highlight ? highlight.lines.map((line, index) => <Fragment key={index}>
        {index > 0 && '\n'}
        {line.map((token, tokenIndex) => {
          const font = Math.max(0, token.fontStyle ?? 0);
          return <span key={tokenIndex} style={{
            color: token.color,
            fontStyle: font & 1 ? 'italic' : undefined,
            fontWeight: font & 2 ? 'bold' : undefined,
            textDecoration: font & 4 ? 'underline' : undefined,
          }}>{token.content}</span>;
        })}
      </Fragment>) : source}
    </code></pre>
  </>;
}
