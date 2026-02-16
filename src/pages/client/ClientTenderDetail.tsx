import { ClientLayout } from '@/components/layout/ClientLayout';
import TenderDetail from '@/pages/TenderDetail';

// Reuse existing TenderDetail but wrapped in ClientLayout
export default function ClientTenderDetail() {
  return <TenderDetail layoutWrapper={ClientLayout} />;
}
