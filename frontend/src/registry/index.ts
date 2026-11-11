/**
 * Default Component Registry
 *
 * Registers all built-in engine components
 */

import { createRegistry, setGlobalRegistry } from './createRegistry';
import type { ComponentRegistry } from '@/engine/types';

// Import all engine components
import {
  Stack,
  Grid,
  Container,
  Card,
  Text,
  Heading,
  Input,
  Select,
  Checkbox,
  Textarea,
  Button,
  Link,
  Alert,
  Skeleton,
  Badge,
} from '@/components/engine';

/**
 * Create and initialize the default component registry
 */
export function createDefaultRegistry(): ComponentRegistry {
  const registry = createRegistry({
    // Layout
    Stack,
    Grid,
    Container,
    Card,

    // Typography
    Text,
    Heading,

    // Forms
    Input,
    Select,
    Checkbox,
    Textarea,

    // Actions
    Button,
    Link,

    // Feedback
    Alert,
    Skeleton,

    // Data
    Badge,
  });

  return registry;
}

/**
 * Initialize the global registry with default components
 */
export function initializeRegistry(): ComponentRegistry {
  const registry = createDefaultRegistry();
  setGlobalRegistry(registry);
  return registry;
}

// Re-export registry utilities
export { getGlobalRegistry, registerComponent, getComponent } from './createRegistry';
