/**
 * Agent API client
 *
 * The agent is the one thing the browser cannot reach through Supabase: starting
 * a run means starting a durable workflow, and approving a tool call means
 * signalling one. Both go through the agent API service (compose: `agent-api`).
 *
 * Everything else about a run — its status, transcript and pending approvals —
 * is read straight from Supabase by the page's own data sources, so this file
 * stays small.
 */

const AGENT_API_URL = import.meta.env.VITE_AGENT_API_URL || 'http://localhost:8000';

export interface StartRunResult {
  run_id: string;
  workflow_id: string;
}

async function post<T>(path: string, body: unknown): Promise<T | null> {
  const response = await fetch(`${AGENT_API_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    // The API returns a readable `detail` for the cases a user can act on: an
    // unknown agent, a run that has already finished. Surface it rather than a
    // bare status code.
    const detail = await response.json().catch(() => null);
    throw new Error(
      detail?.detail ?? `Agent API returned HTTP ${response.status} for ${path}`
    );
  }

  return response.status === 204 ? null : ((await response.json()) as T);
}

export function startAgentRun(input: {
  agent: string;
  question: string;
}): Promise<StartRunResult | null> {
  return post<StartRunResult>('/agent/runs', input);
}

export function decideToolCall(
  runId: string,
  input: { tool_call_id: string; approved: boolean; decided_by?: string }
): Promise<null> {
  return post<null>(`/agent/runs/${runId}/decisions`, {
    decided_by: 'browser',
    ...input,
  }) as Promise<null>;
}

export function listAgents(): Promise<{ agents: string[] }> {
  return fetch(`${AGENT_API_URL}/agent/agents`).then((r) => r.json());
}
