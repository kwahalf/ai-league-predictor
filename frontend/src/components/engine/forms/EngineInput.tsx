/**
 * Input Component - Text input with label support
 */

import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';
import type { EngineComponentProps, ActionDefinition } from '@/engine/types';
import { useUIEngine } from '@/engine/UIEngineContext';

interface EngineInputProps extends EngineComponentProps {
  type?: 'text' | 'email' | 'password' | 'number' | 'tel' | 'url' | 'search';
  value?: string | number;
  onChange?: ActionDefinition;
  onBlur?: ActionDefinition;
  placeholder?: string;
  label?: string;
  name?: string;
  disabled?: boolean;
  required?: boolean;
  error?: string;
  className?: string;
}

export function EngineInput({
  type = 'text',
  value = '',
  onChange,
  onBlur,
  placeholder,
  label,
  name,
  disabled = false,
  required = false,
  error,
  className,
}: EngineInputProps) {
  const { dispatch } = useUIEngine();

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (onChange) {
      dispatch(onChange, { event: e });
    }
  };

  const handleBlur = (e: React.FocusEvent<HTMLInputElement>) => {
    if (onBlur) {
      dispatch(onBlur, { event: e });
    }
  };

  const inputId = name || `input-${Math.random().toString(36).slice(2, 9)}`;

  return (
    <div className={cn('space-y-2', className)}>
      {label && (
        <Label htmlFor={inputId}>
          {label}
          {required && <span className="text-destructive ml-1">*</span>}
        </Label>
      )}
      <Input
        id={inputId}
        type={type}
        name={name}
        value={value}
        onChange={handleChange}
        onBlur={handleBlur}
        placeholder={placeholder}
        disabled={disabled}
        required={required}
        className={cn(error && 'border-destructive')}
      />
      {error && <p className="text-sm text-destructive">{error}</p>}
    </div>
  );
}
