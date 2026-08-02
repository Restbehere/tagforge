import { useEffect, useState } from "react";

/** Returns `value` after it has been stable for `delayMs`.
 * Use for free-text filter inputs so each keystroke doesn't refetch. */
export function useDebouncedValue<T>(value: T, delayMs = 250): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(t);
  }, [value, delayMs]);
  return debounced;
}
