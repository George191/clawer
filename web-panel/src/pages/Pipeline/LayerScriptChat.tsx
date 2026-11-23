import { memo, useEffect, useRef, useState } from 'react';
import NativeDot, { type DotActivity } from '@/components/NativeDot';
import { Button, Input } from '@/components/ui';
import { chatLayerScript, fetchLayerScriptModel, type LayerScriptMessage } from '../../services/api';
import { useThemeStore } from '../../stores/settings';
import './layer-script-chat.css';

type Props = {
  layer: string; table: string; code: string; loaded: boolean;
  validating: boolean; saving: boolean;
  apply: (code: string) => void;
  error: string; setError: (detail: string) => void;
  errorText: (failure: unknown) => string;
};
type TranscriptMessage = LayerScriptMessage & { model?: string };

const ChangedRange = memo(function ChangedRange({ base, code }: { base: string; code: string }) {
  if (base === code) return <p>The proposal matches the editor.</p>;
  const before = base ? base.split('\n') : [];
  const after = code ? code.split('\n') : [];
  let start = 0;
  let endBefore = before.length;
  let endAfter = after.length;
  while (start < Math.min(endBefore, endAfter) && before[start] === after[start]) start++;
  while (endBefore > start && endAfter > start && before[endBefore - 1] === after[endAfter - 1]) {
    endBefore--; endAfter--;
  }
  return <details open className="layer-script-changes">
    <summary>Changes from line {start + 1}</summary>
    {endBefore > start && <div><strong>Before · lines {start + 1}–{endBefore}</strong>
      <pre aria-label="Original range">{before.slice(start, endBefore).join('\n')}</pre></div>}
    {endAfter > start && <div><strong>After · lines {start + 1}–{endAfter}</strong>
      <pre aria-label="Proposed range">{after.slice(start, endAfter).join('\n')}</pre></div>}
  </details>;
});

export default function LayerScriptChat({ layer, table, code, loaded, validating, saving, apply, error, setError, errorText }: Props) {
  const [messages, setMessages] = useState<TranscriptMessage[]>([]);
  const [model, setModel] = useState('');
  const [modelError, setModelError] = useState('');
  const [modelAttempt, setModelAttempt] = useState(0);
  const [input, setInput] = useState('');
  const [pending, setPending] = useState(false);
  const [proposal, setProposal] = useState<{ base: string; code: string }>();
  const request = useRef<AbortController>();
  const log = useRef<HTMLDivElement>(null);
  const dark = useThemeStore(settings => settings.mode === 'dark');
  useEffect(() => {
    const controller = new AbortController();
    setModelError('');
    fetchLayerScriptModel(controller.signal).then(result => {
      if (!result.model?.trim()) throw new Error('The gateway model ID is missing.');
      if (!controller.signal.aborted) setModel(result.model);
    }).catch(failure => { if (!controller.signal.aborted) setModelError(errorText(failure)); });
    return () => controller.abort();
  }, [modelAttempt, errorText]);
  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => { if (log.current) log.current.scrollTop = log.current.scrollHeight; }, [messages]);

  const blocked = !loaded || !model || validating || saving || pending;
  const atLimit = messages.length >= 21 && !pending;
  const stale = !!proposal && proposal.base !== code;
  const activity: DotActivity = pending ? 'Thinking' : saving || validating ? 'Working' : error || modelError ? 'Error'
    : !loaded || !model ? 'Paused' : input || stale || !messages.length ? 'NeedsInput' : 'Ready';
  let status = 'Ask about the script or describe a change.';
  if (pending) status = 'Thinking…';
  else if (saving) status = 'Saving draft…';
  else if (validating) status = 'Checking syntax…';
  else if (modelError) status = 'Model lookup failed';
  else if (error) status = 'Request failed';
  else if (!model) status = 'Loading gateway model…';
  else if (!loaded) status = 'Waiting for the editor';
  else if (atLimit) status = 'Conversation limit reached';
  else if (stale) status = 'The editor has changed. Send a message to review the current script.';
  else if (proposal) status = 'Review the proposal or ask Dot to revise it.';
  else if (messages.length) status = 'Ready for a follow-up';

  async function send() {
    const content = input.trim();
    if (!content || blocked || atLimit || request.current) return;
    const controller = new AbortController();
    request.current = controller;
    const next: TranscriptMessage[] = [...messages, { role: 'user', content }];
    setError(''); setInput(''); setPending(true); setMessages(next);
    try {
      const history = next.map(({ role, content }) => ({ role, content }));
      const result = await chatLayerScript(layer, table, proposal && !stale ? proposal.code : code, history, controller.signal);
      if (controller.signal.aborted) return;
      setMessages([...next, { role: 'assistant', content: result.reply, model: result.model }]);
      if (result.code !== null) setProposal({ base: code, code: result.code });
      else if (stale) setProposal(undefined);
    } catch (failure) {
      if (controller.signal.aborted) return;
      setError(errorText(failure)); setInput(content); setMessages(messages);
    } finally {
      if (!controller.signal.aborted) { setPending(false); request.current = undefined; }
    }
  }

  return <section className="layer-script-chat" aria-label="Layer script assistant">
    <header>
      <div className="layer-script-dot"><NativeDot activity={activity} dark={dark} /></div>
      <div><h3>Dot</h3>{model && <p>Gateway model <code>{model}</code></p>}<p role="status">{status}</p></div>
    </header>
    {modelError && <div><p role="alert">{modelError}</p>
      <Button size="small" onClick={() => setModelAttempt(value => value + 1)}>Retry model lookup</Button></div>}
    {!!messages.length && <div className="layer-script-chat-log" role="log" aria-label="Conversation" ref={log}>
      {messages.map((message, index) => <div key={index} className={`layer-script-message layer-script-message--${message.role}`}>
        <strong>{message.role === 'user' ? 'You' : 'Dot'}</strong>{message.role === 'assistant' && <> · <code title="Requested gateway model">{message.model}</code></>}
        <p>{message.content}</p>
      </div>)}
    </div>}
    {proposal && <div className="layer-script-proposal">
      <ChangedRange base={proposal.base} code={proposal.code} />
      <details><summary>Proposed script</summary><pre>{proposal.code}</pre></details>
      <Button size="small" disabled={blocked || stale} onClick={() => { apply(proposal.code); setProposal(undefined); }}>Apply to editor</Button>
    </div>}
    {error && <p role="alert">{error}</p>}
    {atLimit ? <div className="layer-script-chat-limit">
      <p>Apply any proposal you want to keep before starting again.</p>
      <Button onClick={() => { setMessages([]); setProposal(undefined); setError(''); }}>New conversation</Button>
    </div> : <form onSubmit={event => { event.preventDefault(); void send(); }}>
      <Input.TextArea aria-label="Message Dot" placeholder="Message Dot…" rows={2} maxLength={4000}
        disabled={blocked} value={input} onChange={event => setInput(event.target.value)} />
      <Button type="primary" htmlType="submit" disabled={blocked || !input.trim()}>Send</Button>
    </form>}
  </section>;
}
