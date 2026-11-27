import { PageLoading } from '@/components/ui';

export default function OntologyCatalogSkeleton({ label }: { label: string }) {
  return <PageLoading tip={label} />;
}
