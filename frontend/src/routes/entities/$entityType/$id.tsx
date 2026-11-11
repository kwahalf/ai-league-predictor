/**
 * Entity Detail Route
 */

import { createFileRoute } from '@tanstack/react-router';
import { UIEngine } from '@/engine';
import entityDetailPage from '@/pages/entity-detail.json';
import type { PageDefinition } from '@/engine/types';

export const Route = createFileRoute('/entities/$entityType/$id')({
  component: EntityDetailPage,
});

function EntityDetailPage() {
  const { entityType, id } = Route.useParams();
  return (
    <UIEngine
      page={entityDetailPage as PageDefinition}
      params={{ entityType, id }}
    />
  );
}
