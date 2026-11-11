/**
 * Index Route - Dashboard
 */

import { createFileRoute } from '@tanstack/react-router';
import { UIEngine } from '@/engine';
import dashboardPage from '@/pages/dashboard.json';
import type { PageDefinition } from '@/engine/types';

export const Route = createFileRoute('/')({
  component: DashboardPage,
});

function DashboardPage() {
  return <UIEngine page={dashboardPage as PageDefinition} />;
}
