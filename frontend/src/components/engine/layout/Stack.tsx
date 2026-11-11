/**
 * Stack Component - Flexbox layout with configurable direction and spacing
 */

import { cn } from '@/lib/utils';
import type { EngineComponentProps } from '@/engine/types';

interface StackProps extends EngineComponentProps {
  direction?: 'vertical' | 'horizontal';
  spacing?: number | string;
  align?: 'start' | 'center' | 'end' | 'stretch' | 'baseline';
  justify?: 'start' | 'center' | 'end' | 'between' | 'around' | 'evenly';
  wrap?: boolean;
  className?: string;
}

const alignMap: Record<string, string> = {
  start: 'items-start',
  center: 'items-center',
  end: 'items-end',
  stretch: 'items-stretch',
  baseline: 'items-baseline',
};

const justifyMap: Record<string, string> = {
  start: 'justify-start',
  center: 'justify-center',
  end: 'justify-end',
  between: 'justify-between',
  around: 'justify-around',
  evenly: 'justify-evenly',
};

export function Stack({
  direction = 'vertical',
  spacing = 4,
  align = 'stretch',
  justify = 'start',
  wrap = false,
  className,
  children,
}: StackProps) {
  const isHorizontal = direction === 'horizontal';
  const gapClass = typeof spacing === 'number' ? `gap-${spacing}` : '';
  const gapStyle = typeof spacing === 'string' ? { gap: spacing } : undefined;

  return (
    <div
      className={cn(
        'flex',
        isHorizontal ? 'flex-row' : 'flex-col',
        gapClass,
        alignMap[align],
        justifyMap[justify],
        wrap && 'flex-wrap',
        className
      )}
      style={gapStyle}
    >
      {children}
    </div>
  );
}
