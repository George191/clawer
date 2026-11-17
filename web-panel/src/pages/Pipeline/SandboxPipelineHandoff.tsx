import { useEffect, useRef, useState } from 'react';
import { Button, Modal } from '@/components/ui';
import { checkSandboxHandoffEligibility } from '../../services/api';
import type { SandboxHandoffEligibility, SandboxHandoffSink, SandboxPipelineOutput } from '../../services/api';
import { SandboxMarkings, sandboxScopeDenied } from './SandboxMarkings';

export default function SandboxPipelineHandoff({ output, sink, onClose, onScopeDenied }: {
  output: SandboxPipelineOutput; sink: SandboxHandoffSink; onClose: () => void; onScopeDenied: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<SandboxHandoffEligibility>();
  const [failed, setFailed] = useState(false);
  const request = useRef<AbortController>();
  useEffect(() => () => request.current?.abort(), []);
  const qualify = async () => {
    if (request.current) return;
    const read = new AbortController();
    request.current = read; setBusy(true); setResult(undefined); setFailed(false);
    try {
      const value = await checkSandboxHandoffEligibility({
        outputId: output.id, expectedConfigHash: output.configHash, expectedResultHash: output.resultHash, expectedSinkGeneration: sink.generation,
      }, read.signal);
      if (read.signal.aborted) return;
      if (value.outputId === output.id && value.sink.id === sink.id && value.sink.generation === sink.generation) setResult(value);
      else setFailed(true);
    } catch (failure) {
      if (!read.signal.aborted) { if (sandboxScopeDenied(failure)) onScopeDenied(); else setFailed(true); }
    } finally {
      if (request.current === read && !read.signal.aborted) { request.current = undefined; setBusy(false); }
    }
  };
  return <Modal open destroyOnHidden footer={null} className="flow-surface-modal" title="Check governed handoff?"
    onCancel={() => { request.current?.abort(); onClose(); }}>
    <section className="flow-sandbox-builder flow-sandbox-builder__compare" aria-label="Governed handoff eligibility">
      <p>Check whether the sealed output may be considered for the fixed local review sink <strong>{sink.label}</strong>.</p>
      <p>Committed output <code>{output.id}</code> · reviewed configuration {output.reviewedGeneration}.</p>
      <SandboxMarkings markings={output.markings} label="Output markings" />
      <p>The check evaluates the output markings and inherited source markings. Any actual handoff requires separate audience approval. This check does not authorize transmission, send data, or produce a delivery receipt.</p>
      <div className="flow-sandbox-builder__actions"><Button size="small" type="primary" loading={busy}
        onClick={() => void qualify()}>Confirm eligibility check</Button></div>
      {failed && <p role="status">Eligibility could not be verified for this sealed output and sink review.</p>}
      {result && <>
        <strong role="status">{result.eligible ? 'Eligible for separate review' : 'Not eligible for this review sink'}</strong>
        <SandboxMarkings markings={result.markings} />
        {!result.eligible && result.reason === 'clearance_mismatch' && <p>The sink’s reviewed clearance does not cover the output’s effective markings.</p>}
        <p>No data was sent.</p>
      </>}
    </section>
  </Modal>;
}
