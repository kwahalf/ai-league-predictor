/**
 * UIEngine Component
 *
 * Main orchestrator that interprets page definitions and renders component trees
 */

import { useState, useMemo, useCallback } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { useQueryClient } from '@tanstack/react-query';
import { UIEngineContext } from './UIEngineContext';
import { ComponentRenderer } from './ComponentRenderer';
import { useDataSources } from './useDataSources';
import { createActionDispatcher } from './ActionDispatcher';
import type { CustomActionHandler } from './ActionDispatcher';
import {
  evaluateExpression,
  createExpressionContext,
  mergeContext,
} from './ExpressionEvaluator';
import { supabase } from '@/data/supabase';
import type {
  PageDefinition,
  ExpressionContext,
  ActionDefinition,
  UIEngineContextValue,
} from './types';

interface UIEngineProps {
  /** The page definition to render */
  page: PageDefinition;
  /** Route parameters */
  params?: Record<string, string>;
  /**
   * Handlers for `{ "action": "custom", "handler": "name" }` actions.
   *
   * The escape hatch for anything the declarative actions cannot express -- a
   * call to a service that is not Supabase, for instance. A page stays JSON;
   * only the effect is code.
   */
  customHandlers?: Record<string, CustomActionHandler>;
}

/**
 * UIEngine - Interprets JSON page definitions and renders component trees
 */
export function UIEngine({ page, params = {}, customHandlers }: UIEngineProps) {
  // Initialize page state
  const [state, setStateInternal] = useState<Record<string, unknown>>(
    () => page.state || {}
  );

  // Modal state
  const [openModals, setOpenModals] = useState<
    Record<string, { props?: Record<string, unknown> }>
  >({});

  // Router navigation
  const navigate = useNavigate();

  // Query client for cache invalidation
  const queryClient = useQueryClient();

  // Create base expression context
  const baseContext = useMemo<ExpressionContext>(
    () =>
      createExpressionContext({
        state,
        params,
      }),
    [state, params]
  );

  // Fetch data sources
  const { data, isLoading, errors, isPageLoading, refetch } = useDataSources(
    page.dataSources,
    baseContext
  );

  // Full context with data
  const fullContext = useMemo<ExpressionContext>(
    () =>
      mergeContext(baseContext, {
        data,
      }),
    [baseContext, data]
  );

  // State setter
  const setState = useCallback((key: string, value: unknown) => {
    setStateInternal((prev) => ({
      ...prev,
      [key]: value,
    }));
  }, []);

  // Modal handlers
  const openModal = useCallback(
    (modalId: string, props?: Record<string, unknown>) => {
      setOpenModals((prev) => ({
        ...prev,
        [modalId]: { props },
      }));
    },
    []
  );

  const closeModal = useCallback((modalId?: string) => {
    if (modalId) {
      setOpenModals((prev) => {
        const next = { ...prev };
        delete next[modalId];
        return next;
      });
    } else {
      setOpenModals({});
    }
  }, []);

  // Create action dispatcher
  const actionDispatcher = useMemo(
    () =>
      createActionDispatcher({
        setState,
        navigate,
        supabase,
        queryClient,
        refetch,
        openModal,
        closeModal,
        customHandlers,
      }),
    [setState, navigate, queryClient, refetch, openModal, closeModal, customHandlers]
  );

  // Dispatch function that merges contexts
  const dispatch = useCallback(
    async (
      action: ActionDefinition,
      additionalContext?: Partial<ExpressionContext>
    ) => {
      const context = additionalContext
        ? mergeContext(fullContext, additionalContext)
        : fullContext;
      await actionDispatcher.dispatch(action, context);
    },
    [actionDispatcher, fullContext]
  );

  // Expression evaluator for child components
  const evalExpression = useCallback(
    (expr: unknown, additionalContext?: Partial<ExpressionContext>) => {
      const context = additionalContext
        ? mergeContext(fullContext, additionalContext)
        : fullContext;
      return evaluateExpression(expr, context);
    },
    [fullContext]
  );

  // Build context value
  const contextValue = useMemo<UIEngineContextValue>(
    () => ({
      state,
      setState,
      data,
      params,
      isLoading,
      errors,
      isPageLoading,
      dispatch,
      refetch,
      openModals,
      openModal,
      closeModal,
      evaluateExpression: evalExpression,
    }),
    [
      state,
      setState,
      data,
      params,
      isLoading,
      errors,
      isPageLoading,
      dispatch,
      refetch,
      openModals,
      openModal,
      closeModal,
      evalExpression,
    ]
  );

  return (
    <UIEngineContext.Provider value={contextValue}>
      {/* Render main layout */}
      <ComponentRenderer definition={page.layout} context={fullContext} />

      {/* Render modals */}
      {page.modals &&
        Object.entries(openModals).map(([modalId, modalState]) => {
          const modalDef = page.modals?.[modalId];
          if (!modalDef) return null;

          // Create context with modal props
          const modalContext = mergeContext(fullContext, {
            state: { ...state, ...(modalState.props || {}) },
          });

          return (
            <ModalRenderer
              key={modalId}
              modalId={modalId}
              definition={modalDef}
              context={modalContext}
              onClose={() => closeModal(modalId)}
            />
          );
        })}
    </UIEngineContext.Provider>
  );
}

/**
 * Modal Renderer Component
 */
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type { ModalDefinition } from './types';

interface ModalRendererProps {
  modalId: string;
  definition: ModalDefinition;
  context: ExpressionContext;
  onClose: () => void;
}

function ModalRenderer({
  definition,
  context,
  onClose,
}: ModalRendererProps) {
  const title = definition.title
    ? (evaluateExpression(definition.title, context) as string)
    : undefined;

  const description = definition.description
    ? (evaluateExpression(definition.description, context) as string)
    : undefined;

  const sizeClasses: Record<string, string> = {
    sm: 'max-w-sm',
    md: 'max-w-md',
    lg: 'max-w-lg',
    xl: 'max-w-xl',
    full: 'max-w-full',
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      {/* Radix warns when a dialog points at a description that was never
          rendered. Page definitions may omit one, so drop the attribute then. */}
      <DialogContent
        className={sizeClasses[definition.size || 'md']}
        {...(description ? {} : { 'aria-describedby': undefined })}
      >
        {(title || description) && (
          <DialogHeader>
            {title && <DialogTitle>{title}</DialogTitle>}
            {description && <DialogDescription>{description}</DialogDescription>}
          </DialogHeader>
        )}
        <ComponentRenderer definition={definition.content} context={context} />
      </DialogContent>
    </Dialog>
  );
}
