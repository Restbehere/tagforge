import { useRef, useState, type MouseEvent, type ReactNode } from "react";

interface Spark {
  id: number;
  x: number;
  y: number;
}

const RAYS = [0, 60, 120, 180, 240, 300];

/** Wraps a clickable element and bursts 6 brand-colored rays from the click
 * point. Pure CSS animation (.pf-spark); hidden under reduced motion. */
export function ClickSpark({ children }: { children: ReactNode }) {
  const [sparks, setSparks] = useState<Spark[]>([]);
  const nextId = useRef(0);

  function burst(e: MouseEvent<HTMLDivElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    const spark: Spark = {
      id: nextId.current++,
      x: e.clientX - rect.left,
      y: e.clientY - rect.top,
    };
    setSparks((prev) => [...prev, spark]);
    window.setTimeout(() => {
      setSparks((prev) => prev.filter((s) => s.id !== spark.id));
    }, 500);
  }

  return (
    <div className="relative inline-flex" onClick={burst}>
      {children}
      {sparks.map((s) =>
        RAYS.map((angle) => (
          <span
            key={`${s.id}-${angle}`}
            className="pf-spark"
            style={{
              left: s.x,
              top: s.y,
              ["--spark-angle" as string]: `${angle}deg`,
            }}
          />
        )),
      )}
    </div>
  );
}
