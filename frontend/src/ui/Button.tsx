import type { ButtonHTMLAttributes } from 'react';

type Variant = 'primary' | 'secondary' | 'danger' | 'ghost';
type Size = 'sm' | 'md';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-brand text-white hover:bg-brand-dark border border-transparent',
  secondary: 'bg-surface text-ink border border-border hover:bg-canvas',
  danger: 'bg-danger text-white hover:brightness-95 border border-transparent',
  ghost: 'bg-transparent text-brand-dark hover:bg-brand-light border border-transparent',
};

const SIZES: Record<Size, string> = {
  sm: 'px-2.5 py-1 text-sm',
  md: 'px-3.5 py-2 text-sm',
};

/**
 * The single button primitive for the operator chrome. Defaults to the primary
 * brand fill; `secondary`/`danger`/`ghost` cover the rest. Native `type` is the
 * caller's responsibility (the project lints implicit submit buttons).
 */
export function Button({ variant = 'primary', size = 'md', className = '', ...rest }: ButtonProps) {
  const classes = [
    'inline-flex items-center justify-center gap-1.5 rounded-md font-medium',
    'transition-colors disabled:cursor-not-allowed disabled:opacity-50',
    'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand',
    VARIANTS[variant],
    SIZES[size],
    className,
  ].join(' ');
  return <button className={classes} {...rest} />;
}
