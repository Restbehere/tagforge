/* Shared form primitives. One definition, used everywhere — replaces the
 * per-page Field/Checkbox/segmented-control/rating-class copies. */

import { useEffect, useRef, useState, type ReactNode } from "react";
import { Check, Copy } from "lucide-react";
import { toast } from "sonner";

import { cn } from "@/lib/cn";

export function Field({
  label,
  children,
  className,
}: {
  label?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={className}>
      {label !== undefined && <label className="pf-label">{label}</label>}
      <div className={label !== undefined ? "mt-1" : undefined}>{children}</div>
    </div>
  );
}

export function Checkbox({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <label className="flex cursor-pointer select-none items-center gap-2">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 rounded border-line bg-bg-subtle accent-brand"
      />
      <span className="text-sm text-text">{label}</span>
    </label>
  );
}

export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  className,
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "inline-flex h-9 overflow-hidden rounded-md border border-line bg-bg-subtle text-xs",
        className,
      )}
    >
      {options.map((opt) => (
        <button
          key={opt.value || "all"}
          type="button"
          onClick={() => onChange(opt.value)}
          className={cn(
            "px-3 transition",
            value === opt.value
              ? "bg-brand text-brand-fg"
              : "text-text-muted hover:text-text",
          )}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Ratings                                                            */
/* ------------------------------------------------------------------ */

export const RATINGS = [
  { value: "g", label: "general" },
  { value: "s", label: "sensitive" },
  { value: "q", label: "questionable" },
  { value: "e", label: "explicit" },
] as const;

export function ratingPillClass(rating: string | null | undefined): string {
  switch (rating) {
    case "g":
      return "border-accent-green/40 text-accent-green";
    case "s":
      return "border-accent-amber/40 text-accent-amber";
    case "q":
      return "border-orange-500/40 text-orange-700 dark:text-orange-400";
    case "e":
      return "border-rose-500/40 text-rose-700 dark:text-rose-400";
    default:
      return "";
  }
}

/** Toggle pills for the four booru ratings. Emits the same comma list
 * ("g,s") the API expects, replacing error-prone free-text inputs. */
export function RatingFilter({
  value,
  onChange,
}: {
  value: string;
  onChange: (commaList: string) => void;
}) {
  const selected = new Set(
    value
      .split(",")
      .map((r) => r.trim())
      .filter(Boolean),
  );
  function toggle(r: string) {
    const next = new Set(selected);
    if (next.has(r)) next.delete(r);
    else next.add(r);
    onChange(RATINGS.map((x) => x.value).filter((x) => next.has(x)).join(","));
  }
  return (
    <div className="flex h-9 items-center gap-1.5">
      {RATINGS.map((r) => {
        const active = selected.has(r.value);
        return (
          <button
            key={r.value}
            type="button"
            title={r.label}
            onClick={() => toggle(r.value)}
            className={cn(
              "pf-pill cursor-pointer font-mono transition",
              active
                ? cn("border-current", ratingPillClass(r.value))
                : "opacity-50 hover:opacity-100",
            )}
          >
            {r.value}
          </button>
        );
      })}
      {selected.size === 0 && (
        <span className="text-[11px] text-text-subtle">all ratings</span>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Action buttons                                                     */
/* ------------------------------------------------------------------ */

/** Destructive-action guard: first click arms it for 3s, second confirms. */
export function ConfirmButton({
  onConfirm,
  children,
  confirmLabel = "Confirm?",
  className,
  disabled,
  title,
}: {
  onConfirm: () => void;
  children: ReactNode;
  confirmLabel?: ReactNode;
  className?: string;
  disabled?: boolean;
  title?: string;
}) {
  const [armed, setArmed] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  function handleClick() {
    if (!armed) {
      setArmed(true);
      timer.current = window.setTimeout(() => setArmed(false), 3000);
      return;
    }
    window.clearTimeout(timer.current);
    setArmed(false);
    onConfirm();
  }

  return (
    <button
      type="button"
      className={cn(
        className,
        armed && "border-accent-rose/60 text-accent-rose",
      )}
      onClick={handleClick}
      disabled={disabled}
      title={title}
    >
      {armed ? confirmLabel : children}
    </button>
  );
}

/** Icon button that copies text to the clipboard with toast feedback. */
export function CopyButton({
  text,
  title = "Copy to clipboard",
  className,
  size = 12,
}: {
  text: string;
  title?: string;
  className?: string;
  size?: number;
}) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Clipboard unavailable");
    }
  }

  return (
    <button
      type="button"
      title={title}
      onClick={copy}
      className={cn(
        "rounded p-1 text-text-subtle transition hover:bg-bg-hover hover:text-text",
        copied && "text-accent-green hover:text-accent-green",
        className,
      )}
    >
      {copied ? <Check size={size} /> : <Copy size={size} />}
    </button>
  );
}
