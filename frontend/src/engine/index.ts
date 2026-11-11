/**
 * UI Engine Exports
 */

// Main components
export { UIEngine } from './UIEngine';
export { ComponentRenderer, renderComponents } from './ComponentRenderer';

// Context and hooks
export {
  UIEngineContext,
  useUIEngine,
  useDispatch,
  usePageState,
  usePageData,
  useExpression,
} from './UIEngineContext';

// Expression evaluation
export {
  evaluateExpression,
  evaluateExpressionContent,
  resolveProps,
  resolveValue,
  hasExpression,
  isPureExpression,
  createExpressionContext,
  mergeContext,
} from './ExpressionEvaluator';

// Action dispatcher
export { createActionDispatcher } from './ActionDispatcher';
export type { ActionDispatch, CustomActionHandler, ActionDispatcherConfig } from './ActionDispatcher';

// Data sources
export { useDataSources } from './useDataSources';

// Types
export type {
  PageDefinition,
  ComponentDefinition,
  DataSourceDefinition,
  SupabaseDataSource,
  ApiDataSource,
  StaticDataSource,
  FilterDefinition,
  OrderDefinition,
  ActionDefinition,
  SetStateAction,
  NavigateAction,
  ApiCallAction,
  RefetchAction,
  OpenModalAction,
  CloseModalAction,
  CustomAction,
  SequenceAction,
  ConditionalAction,
  ModalDefinition,
  ExpressionContext,
  UIEngineContextValue,
  EngineComponentProps,
  RegisteredComponent,
  ComponentRegistry,
  ColumnDefinition,
  PageMeta,
} from './types';
