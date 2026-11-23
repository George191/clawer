import { createOnigurumaEngine } from '@shikijs/engine-oniguruma';
import wasm from '@shikijs/engine-oniguruma/wasm-inlined';
import { codeToTokensBase, createShikiPrimitiveAsync } from '@shikijs/primitive';
import draculaSoft from '@shikijs/themes/dracula-soft';
import type { SdkLanguage } from '../../services/publicationSdk';

export type ApiSnippetLanguage = SdkLanguage | 'json';

const theme = 'dracula-soft';
const highlighter = createShikiPrimitiveAsync({
  engine: createOnigurumaEngine(wasm),
  langs: [],
  themes: [draculaSoft],
});
const grammars = {
  c: () => import('@shikijs/langs/c'),
  java: () => import('@shikijs/langs/java'),
  json: () => import('@shikijs/langs/json'),
  python: () => import('@shikijs/langs/python'),
  typescript: () => import('@shikijs/langs/typescript'),
};

export async function highlightApiCode(source: string, language: ApiSnippetLanguage) {
  const [registry, grammar] = await Promise.all([highlighter, grammars[language]()]);
  if (!registry.getLoadedLanguages().includes(language)) registry.loadLanguageSync(grammar.default);
  const { bg, fg } = registry.getTheme(theme);
  return { bg, fg, lines: codeToTokensBase(registry, source, { lang: language, theme }) };
}
