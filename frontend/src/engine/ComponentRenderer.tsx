/**
 * ComponentRenderer
 *
 * Recursively renders component trees from JSON definitions
 * Handles conditionals (if), loops (each), and expression evaluation
 */

import { useMemo } from 'react';
import { getGlobalRegistry } from '@/registry/createRegistry';
import { UIEngineScopeContext, useUIEngineScope } from './UIEngineContext';
import {
  evaluateExpression,
  resolveProps,
  mergeContext,
} from './ExpressionEvaluator';
import type {
  ComponentDefinition,
  ExpressionContext,
  RegisteredComponent,
} from './types';

/**
 * Publishes one loop iteration's bindings, merged over any enclosing loop's,
 * so nested `each` blocks keep the outer variables visible.
 */
function LoopScope({
  scope,
  children,
}: {
  scope: Partial<ExpressionContext>;
  children: React.ReactNode;
}) {
  const parentScope = useUIEngineScope();
  const value = useMemo(
    () => ({ ...parentScope, ...scope }),
    [parentScope, scope]
  );

  return (
    <UIEngineScopeContext.Provider value={value}>
      {children}
    </UIEngineScopeContext.Provider>
  );
}

interface ComponentRendererProps {
  /** Component definition to render */
  definition: ComponentDefinition;
  /** Expression context */
  context: ExpressionContext;
}

/**
 * ComponentRenderer - Renders a single component and its children
 */
export function ComponentRenderer({
  definition,
  context,
}: ComponentRendererProps) {
  const registry = getGlobalRegistry();

  // Handle conditional rendering
  if (definition.if !== undefined) {
    const condition = evaluateExpression(definition.if, context);
    if (!condition) {
      return null;
    }
  }

  // Handle list rendering (each)
  if (definition.each !== undefined) {
    const items = evaluateExpression(definition.each, context);

    if (!Array.isArray(items)) {
      // Missing values are normal while a data source is still loading; only a
      // present-but-wrong-shaped value is a page definition bug worth warning about.
      if (items !== undefined && items !== null) {
        console.warn(`"each" expression did not return an array:`, definition.each);
      }
      return null;
    }

    const itemKey = definition.as || 'item';
    const indexKey = definition.indexAs || 'index';

    return (
      <>
        {items.map((item, index) => {
          // Bindings this iteration adds. They go into the render context AND
          // into the scope provider, so actions dispatched from inside the loop
          // (which resolve later) can still see `{{entity.id}}` and friends.
          const itemScope = {
            [itemKey]: item,
            [indexKey]: index,
            row: typeof item === 'object' ? item as Record<string, unknown> : undefined,
            item,
            index,
          } as Partial<ExpressionContext>;
          const itemContext = mergeContext(context, itemScope);

          // Evaluate key for React reconciliation
          const key = definition.key
            ? String(evaluateExpression(definition.key, itemContext))
            : String(index);

          // Create a definition without 'each' to render normally
          const itemDefinition: ComponentDefinition = {
            ...definition,
            each: undefined,
            as: undefined,
            indexAs: undefined,
            key: undefined,
          };

          return (
            <LoopScope key={key} scope={itemScope}>
              <ComponentRenderer
                definition={itemDefinition}
                context={itemContext}
              />
            </LoopScope>
          );
        })}
      </>
    );
  }

  // Get the component from registry
  const Component = registry.get(definition.type);

  if (!Component) {
    console.warn(`Component not found in registry: ${definition.type}`);
    return (
      <div className="p-4 border border-red-300 bg-red-50 text-red-700 rounded">
        Unknown component: {definition.type}
      </div>
    );
  }

  // Hooks live in RegisteredComponent, below: the early returns above mean this
  // function cannot call hooks itself without changing hook order between renders.
  return (
    <ResolvedComponent
      Component={Component}
      definition={definition}
      context={context}
    />
  );
}

interface ResolvedComponentProps {
  /** Component resolved from the registry for `definition.type` */
  Component: RegisteredComponent;
  definition: ComponentDefinition;
  context: ExpressionContext;
}

/**
 * Renders one resolved registry component, its children and its slots.
 */
function ResolvedComponent({
  Component,
  definition,
  context,
}: ResolvedComponentProps) {
  // Resolve props (evaluate expressions)
  const resolvedProps = useMemo(() => {
    return definition.props ? resolveProps(definition.props, context) : {};
  }, [definition.props, context]);

  // Render children recursively
  const renderedChildren = useMemo(() => {
    if (!definition.children || definition.children.length === 0) {
      return null;
    }

    return definition.children.map((child, index) => (
      <ComponentRenderer key={index} definition={child} context={context} />
    ));
  }, [definition.children, context]);

  // Handle slots for complex components (like Tabs)
  const slots = useMemo(() => {
    if (!definition.slots) return undefined;

    const renderedSlots: Record<string, React.ReactNode> = {};

    for (const [slotName, slotContent] of Object.entries(definition.slots)) {
      if (Array.isArray(slotContent)) {
        renderedSlots[slotName] = slotContent.map((child, index) => (
          <ComponentRenderer key={index} definition={child} context={context} />
        ));
      } else {
        renderedSlots[slotName] = (
          <ComponentRenderer definition={slotContent} context={context} />
        );
      }
    }

    return renderedSlots;
  }, [definition.slots, context]);

  // Render the component
  // Only pass explicit children if we have rendered child components
  // This allows props.children (from expressions) to be used when no child components are defined
  if (renderedChildren) {
    return (
      <Component {...resolvedProps} slots={slots}>
        {renderedChildren}
      </Component>
    );
  }

  return <Component {...resolvedProps} slots={slots} />;
}

/**
 * Render multiple component definitions
 */
export function renderComponents(
  definitions: ComponentDefinition[] | undefined,
  context: ExpressionContext
): React.ReactNode {
  if (!definitions || definitions.length === 0) {
    return null;
  }

  return definitions.map((def, index) => (
    <ComponentRenderer key={index} definition={def} context={context} />
  ));
}
