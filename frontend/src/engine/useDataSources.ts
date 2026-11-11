/**
 * useDataSources Hook
 *
 * Manages data fetching for all data sources defined in a page
 */

import { useQueries } from '@tanstack/react-query';
import { useMemo, useCallback } from 'react';
import { supabase } from '@/data/supabase';
import { executeSupabaseQuery, createQueryKey } from '@/data/queryBuilder';
import { evaluateExpression, mergeContext } from './ExpressionEvaluator';
import type {
  DataSourceDefinition,
  ApiDataSource,
  StaticDataSource,
  ExpressionContext,
} from './types';

interface UseDataSourcesResult {
  /** Query results by source name */
  data: Record<string, unknown>;
  /** Loading state by source name */
  isLoading: Record<string, boolean>;
  /** Error state by source name */
  errors: Record<string, Error | null>;
  /** Overall loading state */
  isPageLoading: boolean;
  /** Refetch a specific data source */
  refetch: (sourceName: string) => void;
  /** Refetch all data sources */
  refetchAll: () => void;
}

/**
 * Execute an API data source
 */
async function executeApiSource(
  source: ApiDataSource,
  context: ExpressionContext
): Promise<unknown> {
  const url = evaluateExpression(source.url, context) as string;
  const method = source.method || 'GET';

  const headers: Record<string, string> = {};
  if (source.headers) {
    for (const [key, value] of Object.entries(source.headers)) {
      headers[key] = evaluateExpression(value, context) as string;
    }
  }

  const options: RequestInit = {
    method,
    headers,
  };

  if (source.body && method !== 'GET') {
    options.body = JSON.stringify(evaluateExpression(source.body, context));
    headers['Content-Type'] = 'application/json';
  }

  const response = await fetch(url, options);

  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`);
  }

  return response.json();
}

/**
 * Hook to manage all data sources for a page
 */
export function useDataSources(
  dataSources: Record<string, DataSourceDefinition> | undefined,
  context: ExpressionContext
): UseDataSourcesResult {
  // Create query configurations for each data source
  const sourceEntries = useMemo(() => {
    return Object.entries(dataSources || {});
  }, [dataSources]);

  // Build queries array
  const queries = useQueries({
    queries: sourceEntries.map(([name, source]) => {
      // Check if query should be enabled
      const enabled =
        source.type === 'static' ||
        !('enabled' in source) ||
        !source.enabled ||
        Boolean(evaluateExpression(source.enabled, context));

      // Create query based on source type
      if (source.type === 'supabase') {
        const queryKey = createQueryKey(source, context);
        return {
          queryKey: ['datasource', name, ...queryKey],
          queryFn: () => executeSupabaseQuery(supabase, source, context),
          enabled,
          staleTime: 1000 * 60 * 5, // 5 minutes
          refetchInterval: resolveRefetchInterval(source, context),
        };
      }

      if (source.type === 'api') {
        return {
          queryKey: ['datasource', name, source.url, source.method, context.params],
          queryFn: () => executeApiSource(source, context),
          enabled,
          staleTime: 1000 * 60 * 5,
          refetchInterval: resolveRefetchInterval(source, context),
        };
      }

      // Static data source
      if (source.type === 'static') {
        return {
          queryKey: ['datasource', name, 'static'],
          queryFn: () => Promise.resolve((source as StaticDataSource).data),
          enabled: true,
          staleTime: Infinity,
        };
      }

      // Unknown source type
      return {
        queryKey: ['datasource', name, 'unknown'],
        queryFn: () => Promise.resolve(null),
        enabled: false,
      };
    }),
  });

  // Build results object
  const data = useMemo(() => {
    const result: Record<string, unknown> = {};
    sourceEntries.forEach(([name], index) => {
      result[name] = queries[index]?.data ?? null;
    });
    return result;
  }, [sourceEntries, queries]);

  const isLoading = useMemo(() => {
    const result: Record<string, boolean> = {};
    sourceEntries.forEach(([name], index) => {
      result[name] = queries[index]?.isLoading ?? false;
    });
    return result;
  }, [sourceEntries, queries]);

  const errors = useMemo(() => {
    const result: Record<string, Error | null> = {};
    sourceEntries.forEach(([name], index) => {
      result[name] = (queries[index]?.error as Error) ?? null;
    });
    return result;
  }, [sourceEntries, queries]);

  const isPageLoading = useMemo(() => {
    return queries.some((q) => q.isLoading);
  }, [queries]);

  // Refetch functions
  const refetch = useCallback(
    (sourceName: string) => {
      const index = sourceEntries.findIndex(([name]) => name === sourceName);
      if (index !== -1) {
        queries[index]?.refetch();
      }
    },
    [sourceEntries, queries]
  );

  const refetchAll = useCallback(() => {
    queries.forEach((query) => query.refetch());
  }, [queries]);

  return {
    data,
    isLoading,
    errors,
    isPageLoading,
    refetch,
    refetchAll,
  };
}

/**
 * Poll interval for a data source, in milliseconds.
 *
 * A page watching something that changes on its own -- an agent run in flight,
 * a queue draining -- needs to re-read it without the user pressing anything.
 *
 * `refetchWhile` stops that polling from running forever. It is evaluated with
 * `result` bound to this source's own last value, which is the only extra
 * context available at query time (page `data` is assembled after the queries
 * are built), so a source can stop as soon as its own result says it is done:
 *
 *   "refetchInterval": 1500,
 *   "refetchWhile": "{{result.status !== 'completed'}}"
 */
function resolveRefetchInterval(
  source: DataSourceDefinition,
  context: ExpressionContext
): number | false | ((query: { state: { data?: unknown } }) => number | false) {
  if (!('refetchInterval' in source) || !source.refetchInterval) {
    return false;
  }
  const interval = source.refetchInterval;
  const condition = 'refetchWhile' in source ? source.refetchWhile : undefined;
  if (!condition) {
    return interval;
  }
  return (query) => {
    const withResult = mergeContext(context, {
      result: query.state.data,
    } as Partial<ExpressionContext>);
    return evaluateExpression(condition, withResult) ? interval : false;
  };
}
