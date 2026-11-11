/**
 * Text Component - Typography for paragraphs and spans
 */

import { cn } from '@/lib/utils';
import type { EngineComponentProps } from '@/engine/types';

interface TextProps extends EngineComponentProps {
  variant?: 'default' | 'muted' | 'primary' | 'destructive';
  size?: 'xs' | 'sm' | 'base' | 'lg' | 'xl';
  weight?: 'normal' | 'medium' | 'semibold' | 'bold';
  as?: 'p' | 'span' | 'div';
  className?: string;
}

const variantMap: Record<string, string> = {
  default: 'text-foreground',
  muted: 'text-muted-foreground',
  primary: 'text-primary',
  destructive: 'text-destructive',
};

const sizeMap: Record<string, string> = {
  xs: 'text-xs',
  sm: 'text-sm',
  base: 'text-base',
  lg: 'text-lg',
  xl: 'text-xl',
};

const weightMap: Record<string, string> = {
  normal: 'font-normal',
  medium: 'font-medium',
  semibold: 'font-semibold',
  bold: 'font-bold',
};

export function Text({
  variant = 'default',
  size = 'base',
  weight = 'normal',
  as: Component = 'p',
  className,
  children,
}: TextProps) {
  return (
    <Component
      className={cn(
        variantMap[variant],
        sizeMap[size],
        weightMap[weight],
        className
      )}
    >
      {children}
    </Component>
  );
}
