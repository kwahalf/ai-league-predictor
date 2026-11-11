/**
 * Badge Component - Status badges
 */

import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import type { EngineComponentProps } from '@/engine/types';

interface EngineBadgeProps extends EngineComponentProps {
  variant?: 'default' | 'secondary' | 'destructive' | 'outline';
  className?: string;
}

export function EngineBadge({
  variant = 'default',
  className,
  children,
}: EngineBadgeProps) {
  return (
    <Badge variant={variant} className={cn(className)}>
      {children}
    </Badge>
  );
}
