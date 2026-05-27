import type { HTMLAttributes, ReactNode } from 'react';

/** A surface container: white panel, soft border, rounded corners. */
export function Card({ className = '', children, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={`rounded-lg border border-border bg-surface shadow-sm ${className}`} {...rest}>
      {children}
    </div>
  );
}

export function CardBody({
  className = '',
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return <div className={`p-5 ${className}`}>{children}</div>;
}
