/**
 * Supabase Query Builder
 *
 * Transforms DataSourceDefinition into Supabase queries
 */

import type { SupabaseClient } from '@supabase/supabase-js';
import type {
  SupabaseDataSource,
  FilterDefinition,
  ExpressionContext,
} from '@/engine/types';
import { evaluateExpression } from '@/engine/ExpressionEvaluator';

/**
 * Apply a filter to a Supabase query
 */
/**
 * Start a select query. Filters, ordering and limits are layered on top of it,
 * and `SupabaseFilterBuilder` is derived from this call so the two never drift.
 */
function selectFrom(client: SupabaseClient, source: SupabaseDataSource) {
  return client.from(source.table).select(source.select || '*');
}

type SupabaseFilterBuilder = ReturnType<typeof selectFrom>;

function applyFilter(
  query: SupabaseFilterBuilder,
  filter: FilterDefinition,
  context: ExpressionContext
) {
  const value = evaluateExpression(filter.value, context);

  switch (filter.op) {
    case 'eq':
      return query.eq(filter.field, value);
    case 'neq':
      return query.neq(filter.field, value);
    case 'gt':
      return query.gt(filter.field, value);
    case 'gte':
      return query.gte(filter.field, value);
    case 'lt':
      return query.lt(filter.field, value);
    case 'lte':
      return query.lte(filter.field, value);
    case 'like':
      return query.like(filter.field, value as string);
    case 'ilike':
      return query.ilike(filter.field, value as string);
    case 'in':
      return query.in(filter.field, value as unknown[]);
    case 'is':
      return query.is(filter.field, value);
    case 'contains':
      return query.contains(filter.field, value as unknown[]);
    case 'containedBy':
      return query.containedBy(filter.field, value as unknown[]);
    default:
      console.warn(`Unknown filter operator: ${filter.op}`);
      return query;
  }
}

/**
 * Build a Supabase query from a data source definition
 */
export function buildSupabaseQuery(
  client: SupabaseClient,
  source: SupabaseDataSource,
  context: ExpressionContext
) {
  // Start with the table and select
  let query = selectFrom(client, source);

  // Apply filters
  if (source.filters) {
    for (const filter of source.filters) {
      query = applyFilter(query, filter, context) as typeof query;
    }
  }

  // Apply ordering
  if (source.order) {
    for (const order of source.order) {
      query = query.order(order.column, { ascending: order.ascending ?? true });
    }
  }

  // Apply limit
  if (source.limit) {
    query = query.limit(source.limit);
  }

  return query;
}

/**
 * Execute a Supabase query and handle single vs multiple results
 */
export async function executeSupabaseQuery(
  client: SupabaseClient,
  source: SupabaseDataSource,
  context: ExpressionContext
): Promise<unknown> {
  const query = buildSupabaseQuery(client, source, context);

  if (source.single) {
    // maybeSingle, not single: a page whose record was deleted (or a bad id in
    // the URL) should render empty rather than fail the request with HTTP 406.
    const { data, error } = await query.maybeSingle();
    if (error) throw error;
    return data;
  }

  const { data, error } = await query;
  if (error) throw error;
  return data;
}

/**
 * Create a query key for TanStack Query
 */
export function createQueryKey(
  source: SupabaseDataSource,
  context: ExpressionContext
): unknown[] {
  // Evaluate filter values to include in query key
  const evaluatedFilters = source.filters?.map((f) => ({
    ...f,
    value: evaluateExpression(f.value, context),
  }));

  return [
    'supabase',
    source.table,
    source.select || '*',
    evaluatedFilters,
    source.order,
    source.limit,
    source.single,
  ];
}
