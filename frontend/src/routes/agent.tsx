/**
 * Agent Route
 *
 * The page itself is JSON (`src/pages/agent.json`). This file supplies the two
 * things a JSON page cannot express: the three custom action handlers that talk
 * to the agent API, and the currently selected run.
 *
 * The selected run is held here rather than in page state because it has to be
 * set from a handler's callback, and handlers receive the expression context
 * rather than a state setter. Passing it back in as `params.runId` is what makes
 * the page's data sources re-read against the new run.
 */

import { useCallback, useMemo, useState } from 'react';
import { createFileRoute } from '@tanstack/react-router';
import { UIEngine } from '@/engine';
import type { PageDefinition } from '@/engine/types';
import agentPage from '@/pages/agent.json';
import { decideToolCall, startAgentRun } from '@/data/agentApi';

export const Route = createFileRoute('/agent')({
  component: AgentPage,
});

function AgentPage() {
  const [runId, setRunId] = useState<string>('');
  const [error, setError] = useState<string | null>(null);

  const handlers = useMemo(
    () => ({
      startAgentRun: async (payload: unknown) => {
        setError(null);
        const input = payload as { agent: string; question: string };
        try {
          const started = await startAgentRun(input);
          if (started) setRunId(started.run_id);
        } catch (cause) {
          setError(cause instanceof Error ? cause.message : String(cause));
        }
      },

      decideToolCall: async (payload: unknown) => {
        setError(null);
        const input = payload as { tool_call_id: string; approved: boolean };
        try {
          await decideToolCall(runId, input);
        } catch (cause) {
          setError(cause instanceof Error ? cause.message : String(cause));
        }
      },

      selectRun: async (payload: unknown) => {
        setError(null);
        setRunId((payload as { run_id: string }).run_id);
      },
    }),
    [runId]
  );

  const params = useMemo(() => ({ runId }), [runId]);

  const dismiss = useCallback(() => setError(null), []);

  return (
    <div className="space-y-4">
      {error && (
        <div
          role="alert"
          className="flex items-start justify-between gap-4 rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive"
        >
          <span>{error}</span>
          <button type="button" onClick={dismiss} className="underline">
            Dismiss
          </button>
        </div>
      )}
      <UIEngine
        page={agentPage as PageDefinition}
        params={params}
        customHandlers={handlers}
      />
    </div>
  );
}
