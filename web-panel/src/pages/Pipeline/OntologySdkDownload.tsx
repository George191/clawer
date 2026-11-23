import { useEffect, useState } from 'react';
import { Button } from '@/components/ui';
import client from '../../services/api';
import { apiUrl } from '../../services/endpoints';
import { fetchPublishedOntologyType, listOntologyRecords, ontologyError } from '../../services/ontology';
import { generateOntologySdk, ontologySdkFilename, type OntologySdkFunction } from '../../services/ontology_sdk';
import { getSessionState } from '../../services/session';
import ApiCodeSnippet from './ApiCodeSnippet';
import useExplorerErrorToast from './useExplorerErrorToast';

type Download = { url: string; filename: string; revision: number; identity: string };
type FunctionsPage = { revision: number; items: OntologySdkFunction[] };

export default function OntologySdkDownload({ definitionId }: { definitionId: string }) {
  const [version, setVersion] = useState(0);
  const [download, setDownload] = useState<Download>();
  const [error, setError] = useState('');

  useEffect(() => {
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
    })().catch(failure => { if (active()) setError(ontologyError(failure, 'Unable to prepare the user-token SDK.')); });
    return () => {
      controller.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [definitionId, version]);

  const retry = () => {
    setDownload(undefined);
    setError('');
    setVersion(value => value + 1);
  };
  useExplorerErrorToast(error, 'Unable to prepare the user-token SDK', retry);
  const example = download && `import { createOntologyClient } from "./${download.filename.replace(/\.ts$/, '.js')}";
const api = createOntologyClient({
  baseUrl: ${JSON.stringify(new URL(apiUrl('/')).origin)},
  getAccessToken: () => process.env.ACCESS_TOKEN!,
});
console.log(await api.listRecords({ limit: 50 }));`;
  return <>
    {download ? <a className="ui-btn ui-btn-primary" href={download.url} download={download.filename}
      onClick={event => {
        const session = getSessionState();
        if (!session.authenticated || session.locked || session.identity !== download.identity) event.preventDefault();
      }}>Download user-token SDK</a> : <Button type="primary" disabled>Download user-token SDK</Button>}
    <p>Build the downloaded TypeScript file with your app. The calling account’s access token controls access to objects, actions and functions.</p>
    <p role="status">{error ? 'The user-token SDK is unavailable.'
      : download ? <>Published revision {download.revision} · <code>{download.filename}</code></>
        : 'Preparing the user-token SDK…'}</p>
    {example && <><h4>Usage example</h4><ApiCodeSnippet source={example} language="typescript" /></>}
  </>;
}
