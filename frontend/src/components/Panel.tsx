import type { MouseEvent, ReactNode } from "react";

import { cn } from "@/lib/cn";

/* Feeds the .pf-panel::before spotlight (styles.css) — dark mode only. */
function trackSpotlight(e: MouseEvent<HTMLElement>) {
  const rect = e.currentTarget.getBoundingClientRect();
  e.currentTarget.style.setProperty("--spot-x", `${e.clientX - rect.left}px`);
  e.currentTarget.style.setProperty("--spot-y", `${e.clientY - rect.top}px`);
}

export function Panel({
  title,
  description,
  actions,
  children,
  className,
  bodyClassName,
}: {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={cn("pf-panel", className)} onMouseMove={trackSpotlight}>
      {(title || description || actions) && (
        <header className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
          <div className="min-w-0">
            {title && (
              <h2 className="truncate text-sm font-semibold text-text">
                {title}
              </h2>
            )}
            {description && (
              <p className="mt-0.5 text-xs text-text-muted">{description}</p>
            )}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={cn("p-4", bodyClassName)}>{children}</div>
    </section>
  );
}

export function Stat({
  label,
  value,
  hint,
  className,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("pf-panel p-4", className)} onMouseMove={trackSpotlight}>
      <div className="pf-label">{label}</div>
      <div className="mt-1 pf-stat">{value}</div>
      {hint && <div className="mt-1 text-xs text-text-subtle">{hint}</div>}
    </div>
  );
}
