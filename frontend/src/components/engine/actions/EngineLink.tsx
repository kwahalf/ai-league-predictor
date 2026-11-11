/**
 * Link Component - Navigation link
 */

import { Link } from '@tanstack/react-router';
import { cn } from '@/lib/utils';
import type { EngineComponentProps } from '@/engine/types';

interface EngineLinkProps extends EngineComponentProps {
  /** Optional: props come from JSON, so the target may be missing at runtime. */
  to?: string;
  external?: boolean;
  className?: string;
}

/** External targets can be data-driven, so only navigable schemes are allowed. */
const SAFE_EXTERNAL_URL = /^(https?:|mailto:|\/|#)/i;

export function EngineLink({
  to,
  external = false,
  className,
  children,
}: EngineLinkProps) {
  const linkClasses = cn(
    'text-primary underline-offset-4 hover:underline',
    className
  );

  if (!to) {
    console.warn('Link component is missing a "to" prop');
    return <span className={className}>{children}</span>;
  }

  if (external) {
    if (!SAFE_EXTERNAL_URL.test(to)) {
      console.warn(`Blocked link to unsupported URL scheme: ${to}`);
      return <span className={className}>{children}</span>;
    }
    return (
      <a
        href={to}
        target="_blank"
        rel="noopener noreferrer"
        className={linkClasses}
      >
        {children}
      </a>
    );
  }

  return (
    <Link to={to} className={linkClasses}>
      {children}
    </Link>
  );
}
