import { useEffect, useRef, useState } from "react";
import { createBlackHole, type BlackHoleHandle, type BlackHoleOptions } from "./createBlackHole";

interface BlackHoleProps extends BlackHoleOptions {
  className?: string;
  /** Called once the scene is running. Handy for tweaking `handle.uniforms`. */
  onReady?: (handle: BlackHoleHandle) => void;
}

/**
 * Full-size black hole. It fills its parent, so give the parent a size
 * (for example `fixed inset-0`).
 */
export default function BlackHole({ className, onReady, ...options }: BlackHoleProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  // The scene is created once; later prop changes are not applied.
  const optionsRef = useRef(options);
  const onReadyRef = useRef(onReady);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    // A fresh canvas per mount: a canvas can't be handed to a second renderer
    // after the first one is disposed (React StrictMode remounts in dev).
    const canvas = document.createElement("canvas");
    canvas.style.display = "block";
    canvas.style.width = "100%";
    canvas.style.height = "100%";
    container.appendChild(canvas);

    let cancelled = false;
    let handle: BlackHoleHandle | null = null;

    createBlackHole(canvas, optionsRef.current)
      .then((created) => {
        if (cancelled) {
          created.dispose();
          return;
        }
        handle = created;
        onReadyRef.current?.(created);
      })
      .catch((err: unknown) => {
        console.error(err);
        if (!cancelled) setError(err instanceof Error ? err.message : "Could not start the black hole");
      });

    return () => {
      cancelled = true;
      handle?.dispose();
      canvas.remove();
    };
  }, []);

  return (
    <div ref={containerRef} className={className} style={{ position: "relative", width: "100%", height: "100%" }}>
      {error && (
        <p style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", color: "#888" }}>
          WebGPU / WebGL2 is not available in this browser.
        </p>
      )}
    </div>
  );
}
