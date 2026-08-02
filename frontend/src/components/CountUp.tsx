import { useEffect, useRef, useState } from "react";

/** Animates a number toward `value` (ease-out-quart, ~650ms). Animates on
 * first mount and on later value changes; instant under reduced motion.
 *
 * fromRef tracks the number actually on screen (updated every frame), so
 * StrictMode's dev double-invoke replays the animation correctly and a
 * mid-flight value change retargets smoothly from the displayed number
 * instead of teleporting. */
export function CountUp({
  value,
  duration = 650,
}: {
  value: number;
  duration?: number;
}) {
  const [display, setDisplay] = useState(0);
  const fromRef = useRef(0);
  const rafRef = useRef<number>();

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      fromRef.current = value;
      setDisplay(value);
      return;
    }
    const from = fromRef.current;
    if (from === value) {
      setDisplay(value);
      return;
    }
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 4);
      const current = Math.round(from + (value - from) * eased);
      fromRef.current = current;
      setDisplay(current);
      if (t < 1) {
        rafRef.current = requestAnimationFrame(tick);
      }
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [value, duration]);

  return <>{display.toLocaleString()}</>;
}
