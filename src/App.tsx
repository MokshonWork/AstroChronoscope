import { lazy, Suspense, useEffect, useState } from "react";
import { TwinOrbit } from "@/components/loading-ui/twin-orbit";

// three.js is large, so load the black hole in its own chunk.
const BlackHole = lazy(() => import("./components/black-hole/BlackHole"));

function App() {
  const [currentRoute, setCurrentRoute] = useState<"landing" | "dashboard">(() => {
    if (typeof window === "undefined") return "landing";
    const path = window.location.pathname;
    const hash = window.location.hash;
    return path === "/dashboard" || hash === "#dashboard" ? "dashboard" : "landing";
  });

  const [isBlackHoleReady, setIsBlackHoleReady] = useState(false);

  useEffect(() => {
    const handlePopState = () => {
      const path = window.location.pathname;
      const hash = window.location.hash;
      setCurrentRoute(path === "/dashboard" || hash === "#dashboard" ? "dashboard" : "landing");
    };

    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  const navigateToDashboard = () => {
    window.history.pushState(null, "", "/dashboard");
    setCurrentRoute("dashboard");
  };

  const navigateToLanding = () => {
    window.history.pushState(null, "", "/");
    setCurrentRoute("landing");
  };

  return (
    <>
      {/* 
        Persistent Black Hole Background:
        Kept mounted so the WebGPU renderer, textures, and geometry 
        remain cached in GPU memory when navigating between landing and dashboard.
      */}
      <div
        className={`fixed inset-0 bg-black transition-opacity duration-700 ${currentRoute === "dashboard"
          ? "opacity-0 pointer-events-none"
          : isBlackHoleReady
            ? "opacity-100"
            : "opacity-0"
          }`}
      >
        <Suspense fallback={null}>
          <BlackHole
            onReady={() => setIsBlackHoleReady(true)}
            onError={() => setIsBlackHoleReady(true)}
          />
        </Suspense>
      </div>

      {/* Loader with TwinOrbit while black hole initializes */}
      {!isBlackHoleReady && currentRoute === "landing" && (
        <div className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-black select-none pointer-events-none">
          <div className="flex flex-col items-center gap-6 text-center">
            <TwinOrbit
              className="size-3.5 text-amber-400"
              style={{ "--duration": "1.4s" } as React.CSSProperties}
            />
            <span
              className="text-xs uppercase tracking-[0.28em] text-neutral-400 font-medium font-bodoni"
              style={{ fontFamily: "'OPTIBodoni-Antiqua', serif" }}
            >
              Initializing Observatory
            </span>
          </div>
        </div>
      )}

      {/* Dashboard View */}
      {currentRoute === "dashboard" && (
        <main className="relative z-10 min-h-screen bg-black text-white flex flex-col items-center justify-center p-6 select-none">
          <div className="flex flex-col items-center gap-6 text-center">
            <h1
              className="text-2xl sm:text-3xl md:text-4xl font-bold tracking-[0.25em] font-bodoni text-white drop-shadow-[0_2px_14px_rgba(0,0,0,0.95)]"
              style={{ fontFamily: "'OPTIBodoni-Antiqua', serif" }}
            >
              DASHBOARD in CONSTRUCTION
            </h1>

            <button
              onClick={navigateToLanding}
              className="group relative inline-flex items-center justify-center gap-2 rounded-full border border-white/20 bg-black/40 hover:bg-white/10 hover:border-white/50 px-6 py-2 text-[11px] sm:text-xs font-medium tracking-[0.25em] uppercase text-neutral-400 hover:text-white backdrop-blur-md transition-all duration-300 cursor-pointer active:scale-95"
            >
              <span className="text-[11px] opacity-60 group-hover:-translate-x-0.5 transition-transform duration-200">
                ←
              </span>
              <span>Return</span>
            </button>
          </div>
        </main>
      )}

      {/* Landing Page Content */}
      {currentRoute === "landing" && isBlackHoleReady && (
        <main className="pointer-events-none relative z-10 min-h-screen text-white flex flex-col items-center justify-start p-6 pt-[22vh] sm:pt-[26vh] animate-in fade-in duration-700">
          <h1
            className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-[0.22em] uppercase font-bodoni text-white drop-shadow-[0_2px_14px_rgba(0,0,0,0.95)] select-none pl-[0.22em] text-center"
            style={{ fontFamily: "'OPTIBodoni-Antiqua', serif" }}
          >
            ASTROCHRONOSCOPE
          </h1>

          <div className="mt-[15vh] sm:mt-[20vh] flex flex-col items-center gap-3">
            <button
              onClick={navigateToDashboard}
              className="pointer-events-auto group relative inline-flex items-center justify-center gap-2 rounded-full border border-white/25 bg-black/40 hover:bg-white/10 hover:border-white/60 px-7 sm:px-8 py-2.5 sm:py-3 text-[11px] sm:text-xs font-medium tracking-[0.25em] uppercase text-neutral-300 hover:text-white backdrop-blur-md shadow-[0_4px_24px_rgba(0,0,0,0.6)] hover:shadow-[0_0_24px_rgba(255,200,120,0.25)] active:scale-95 transition-all duration-300 cursor-pointer"
            >
              <span>Go to dashboard</span>
              <span className="text-[11px] opacity-50 group-hover:opacity-100 group-hover:translate-x-0.5 transition-all duration-200">
                →
              </span>
            </button>
          </div>
        </main>
      )}
    </>
  );
}

export default App;
