import { useEffect, useState } from 'react';
import { Button, Modal } from '@/components/ui';
import client from '../../services/api';
import { fetchPublishedOntologyType, listOntologyRecords, ontologyError } from '../../services/ontology';
import { generateOntologySdk, ontologySdkFilename, type OntologySdkFunction } from '../../services/ontology_sdk';
import { getSessionState } from '../../services/session';
import useExplorerErrorToast from './useExplorerErrorToast';
import './explorer-resource-tools.css';

type Download = { url: string; filename: string; revision: number; identity: string };
type FunctionsPage = { revision: number; items: OntologySdkFunction[] };

export default function OntologySdkDownload({ definitionId, open, onClose }: { definitionId: string; open: boolean; onClose: () => void }) {
  const [version, setVersion] = useState(0);
  const [download, setDownload] = useState<Download>();
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    const identity = getSessionState().identity;
    let url: string | undefined;
    const active = () => {
      const session = getSessionState();
      return !controller.signal.aborted && session.authenticated && !session.locked && session.identity === identity;
    };
    setDownload(undefined);
    setError('');
    void (async () => {
      const definition = await fetchPublishedOntologyType(definitionId);
      if (!active()) return;
      const [records, functions] = await Promise.all([
        listOntologyRecords(definitionId, 1, 0),
        definition.definition.source.mode === 'none'
          ? client.get<FunctionsPage>('/object-types/' + encodeURIComponent(definitionId) + '/functions', {
              baseURL: '/api/ontology/v1', signal: controller.signal,
            }).then(result => result.data)
          : Promise.resolve({ revision: definition.revision, items: [] }),
      ]);
      if (!active()) return;
      const confirmed = await fetchPublishedOntologyType(definitionId);
      if (!active()) return;
      if (confirmed.revision !== definition.revision || functions.revision !== definition.revision) {
        throw new Error('The published type changed. Refresh the SDK to use the current revision.');
      }
      const source = generateOntologySdk(confirmed, records.actions ?? [], functions.items);
      url = URL.createObjectURL(new Blob([source], { type: 'text/plain;charset=utf-8' }));
      setDownload({ url, filename: ontologySdkFilename(confirmed), revision: confirmed.revision, identity });
    })().catch(failure => { if (active()) setError(ontologyError(failure, 'Unable to prepare the TypeScript SDK.')); });
    return () => {
      controller.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [definitionId, open, version]);

  const close = () => {
    onClose();
    setDownload(undefined);
    setError('');
  };
  const retry = () => {
    setDownload(undefined);
    setError('');
    setVersion(value => value + 1);
  };
  useExplorerErrorToast(open ? error : undefined, 'Unable to prepare the TypeScript SDK', retry);
  return <>
    <Modal open={open} centered className="flow-surface-modal flow-published-dialog explorer-resource-dialog ontology-sdk-dialog" title="TypeScript SDK" onCancel={close} footer={<>
      <Button onClick={close}>Close</Button>
      {download && <a className="ui-btn ui-btn-primary" href={download.url} download={download.filename}
        onClick={event => {
          const session = getSessionState();
          if (!session.authenticated || session.locked || session.identity !== download.identity) {
            event.preventDefault();
            close();
          }
        }}>Download TypeScript SDK</a>}
    </>}>
      <p className="ontology-sdk-description">Use the generated client with your API URL and an access token for the account making requests.</p>
      {error ? null
        : download ? <p className="ontology-sdk-metadata">Published revision {download.revision} · <code>{download.filename}</code></p>
          : <p role="status">Preparing the TypeScript SDK…</p>}
    </Modal>
  </>;
}
