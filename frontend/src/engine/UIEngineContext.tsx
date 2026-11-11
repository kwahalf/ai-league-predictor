/**
 * UIEngine Context
 *
 * Provides engine state and functions to all child components
 */

import { createContext, useContext, useMemo } from 'react';
import type { ExpressionContext, UIEngineContextValue } from './types';

// Default no-op context value
const defaultContext: UIEngineContextValue = {
  state: {},
  setState: () => {
    console.warn('UIEngine context not initialized');
  },
  data: {},
  params: {},
  isLoading: {},
  errors: {},
  isPageLoading: false,
  dispatch: async () => {
    console.warn('UIEngine context not initialized');
  },
  refetch: () => {
    console.warn('UIEngine context not initialized');
  },
  openModals: {},
  openModal: () => {
    console.warn('UIEngine context not initialized');
  },
  closeModal: () => {
    console.warn('UIEngine context not initialized');
  },
  evaluateExpression: (expr) => expr,
};

/**
 * UIEngine React context
 */
export const UIEngineContext = createContext<UIEngineContextValue>(defaultContext);

/**
 * Scope context for values that only exist inside a subtree, such as the
 * `entity`/`item`/`index` bindings created by an `each` loop.
 *
 * Actions are resolved at dispatch time (so `{{event.*}}` works), by which
 * point the loop's render context is gone. Providing the scope here keeps
 * expressions like `{{entity.id}}` resolvable from a click handler.
 */
export const UIEngineScopeContext = createContext<Partial<ExpressionContext>>({});

/** Scope bindings visible at this point in the component tree. */
export function useUIEngineScope(): Partial<ExpressionContext> {
  return useContext(UIEngineScopeContext);
}

/**
 * Hook to access UIEngine context
 *
 * `dispatch` and `evaluateExpression` are bound to the enclosing scope, so
 * components do not have to thread loop variables through themselves.
 */
export function useUIEngine(): UIEngineContextValue {
  const context = useContext(UIEngineContext);
  const scope = useContext(UIEngineScopeContext);
  if (!context) {
    throw new Error('useUIEngine must be used within a UIEngineProvider');
  }

  return useMemo(() => {
    if (Object.keys(scope).length === 0) {
      return context;
    }
    return {
      ...context,
      dispatch: (action, additionalContext) =>
        context.dispatch(action, { ...scope, ...additionalContext }),
      evaluateExpression: (expr, additionalContext) =>
        context.evaluateExpression(expr, { ...scope, ...additionalContext }),
    };
  }, [context, scope]);
}

/**
 * Hook to access just the dispatch function
 */
export function useDispatch() {
  const { dispatch } = useUIEngine();
  return dispatch;
}

/**
 * Hook to access page state
 */
export function usePageState<T = unknown>(key?: string): T {
  const { state } = useUIEngine();
  if (key) {
    return state[key] as T;
  }
  return state as T;
}

/**
 * Hook to access query data
 */
export function usePageData<T = unknown>(sourceName?: string): T {
  const { data } = useUIEngine();
  if (sourceName) {
    return data[sourceName] as T;
  }
  return data as T;
}

/**
 * Hook to evaluate an expression with current context
 */
export function useExpression<T = unknown>(expression: unknown): T {
  const { evaluateExpression } = useUIEngine();
  return evaluateExpression(expression) as T;
}
