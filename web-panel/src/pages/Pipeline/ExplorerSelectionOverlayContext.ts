import { createContext, useState } from 'react';

export function useExplorerSelectionOverlay() {
  const [host, setHost] = useState<HTMLDivElement | null>(null);
  const [active, setActive] = useState(false);
  return { host, setHost, active, setActive };
}

export const ExplorerSelectionOverlayContext = createContext<ReturnType<typeof useExplorerSelectionOverlay> | null>(null);
