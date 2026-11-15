import { useState, useEffect, useCallback, useId } from "react";
import {
  getBackendHealth,
  getTargetTimeline,
  createCutout,
  inspectImage,
  compareImages,
  getFileUrl,
  type BackendHealthResponse,
  type TimelineResponse,
  type TimelineEpoch,
  type TimelineProduct,
  type CutoutResponse,
  type ComparisonResponse,
  type InspectResponse,
} from "@/lib/api";
import { TwinOrbit } from "@/components/loading-ui/twin-orbit";

interface DashboardProps {
  onReturnToLanding: () => void;
}

// Benchmark preset for Planet X candidate search
const BENCHMARK_TARGET = {
  name: "Planet X Benchmark Candidate",
  ra: 156.09328159,
  dec: -41.64466331,
  radius: 0.01,
  size: 0.1,
  frame: "ICRS (J2000)",
  collection: "spherex_qr3",
};

// SPHEREx Spectral Band Channels
const SPHEREX_BANDS = [
  { id: "SPHEREx-D1", name: "D1", range: "0.75 - 1.11 µm", color: "text-blue-400 border-blue-500/40 bg-blue-500/10" },
  { id: "SPHEREx-D2", name: "D2", range: "1.11 - 1.64 µm", color: "text-cyan-400 border-cyan-500/40 bg-cyan-500/10" },
  { id: "SPHEREx-D3", name: "D3", range: "1.64 - 2.42 µm", color: "text-emerald-400 border-emerald-500/40 bg-emerald-500/10" },
  { id: "SPHEREx-D4", name: "D4", range: "2.42 - 3.82 µm", color: "text-amber-400 border-amber-500/40 bg-amber-500/10" },
  { id: "SPHEREx-D5", name: "D5", range: "3.82 - 4.42 µm", color: "text-orange-400 border-orange-500/40 bg-orange-500/10" },
  { id: "SPHEREx-D6", name: "D6", range: "4.42 - 5.00 µm", color: "text-rose-400 border-rose-500/40 bg-rose-500/10" },
];

export default function Dashboard({ onReturnToLanding }: DashboardProps) {
  const raInputId = useId();
  const decInputId = useId();
  const radiusInputId = useId();
  const sizeInputId = useId();

  // Mission Clock
  const [currentTimeUtc, setCurrentTimeUtc] = useState<string>(() => new Date().toISOString().replace("T", " ").replace("Z", " UTC"));
  const [currentMjd, setCurrentMjd] = useState<string>(() => {
    const jd = new Date().getTime() / 86400000 + 2440587.5;
    return (jd - 2400000.5).toFixed(4);
  });

  useEffect(() => {
    const timer = setInterval(() => {
      const now = new Date();
      setCurrentTimeUtc(now.toISOString().replace("T", " ").replace("Z", " UTC"));
      const jd = now.getTime() / 86400000 + 2440587.5;
      setCurrentMjd((jd - 2400000.5).toFixed(4));
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  // Backend Health Telemetry
  const [health, setHealth] = useState<BackendHealthResponse | null>(null);
  const [healthStatus, setHealthStatus] = useState<"connecting" | "online" | "offline">("connecting");
  const [healthError, setHealthError] = useState<string | null>(null);

  // Target Query Coordinates
  const [ra, setRa] = useState<string>(BENCHMARK_TARGET.ra.toString());
  const [dec, setDec] = useState<string>(BENCHMARK_TARGET.dec.toString());
  const [radius, setRadius] = useState<string>(BENCHMARK_TARGET.radius.toString());
  const [cutoutSize, setCutoutSize] = useState<string>(BENCHMARK_TARGET.size.toString());

  // Timeline State
  const [isTimelineLoading, setIsTimelineLoading] = useState(false);
  const [timelineData, setTimelineData] = useState<TimelineResponse | null>(null);
  const [timelineError, setTimelineError] = useState<string | null>(null);
  const [selectedBandFilter, setSelectedBandFilter] = useState<string>("ALL");

  // Selection & Inspector State
  const [selectedEpochIndex, setSelectedEpochIndex] = useState<number>(0);
  const [selectedProduct, setSelectedProduct] = useState<TimelineProduct | null>(null);

  // Cutout State
  const [isCutoutLoading, setIsCutoutLoading] = useState(false);
  const [cutoutData, setCutoutData] = useState<CutoutResponse | null>(null);
  const [cutoutError, setCutoutError] = useState<string | null>(null);

  // Inspection State
  const [isInspectLoading, setIsInspectLoading] = useState(false);
  const [inspectData, setInspectData] = useState<InspectResponse | null>(null);
  const [inspectError, setInspectError] = useState<string | null>(null);

  // Comparison State (Pairwise Differential Imaging)
  const [comparisonProductA, setComparisonProductA] = useState<TimelineProduct | null>(null);
  const [comparisonProductB, setComparisonProductB] = useState<TimelineProduct | null>(null);
  const [isComparing, setIsComparing] = useState(false);
  const [comparisonResult, setComparisonResult] = useState<ComparisonResponse | null>(null);
  const [comparisonError, setComparisonError] = useState<string | null>(null);

  // Active Tab in the Right-hand Inspector
  const [inspectorTab, setInspectorTab] = useState<"cutout" | "inspection" | "comparison">("cutout");

  // Synchronize Backend Health Status on Mount
  useEffect(() => {
    let isCancelled = false;

    getBackendHealth()
      .then((res) => {
        if (!isCancelled) {
          setHealth(res);
          setHealthStatus("online");
          setHealthError(null);
        }
      })
      .catch((err: unknown) => {
        if (!isCancelled) {
          setHealthStatus("offline");
          setHealthError(err instanceof Error ? err.message : "Failed to connect to backend");
        }
      });

    return () => {
      isCancelled = true;
    };
  }, []);

  const handleRefreshHealth = async () => {
    setHealthStatus("connecting");
    setHealthError(null);
    try {
      const res = await getBackendHealth();
      setHealth(res);
      setHealthStatus("online");
    } catch (err: unknown) {
      setHealthStatus("offline");
      setHealthError(err instanceof Error ? err.message : "Failed to connect to backend");
    }
  };

  // Execute Timeline Query
  const handleQueryTimeline = useCallback(async () => {
    const raNum = parseFloat(ra);
    const decNum = parseFloat(dec);
    const radiusNum = parseFloat(radius);

    if (isNaN(raNum) || isNaN(decNum) || isNaN(radiusNum)) {
      setTimelineError("ERR_VAL: Target coordinates must be valid floating point values.");
      return;
    }

    try {
      setIsTimelineLoading(true);
      setTimelineError(null);
      const result = await getTargetTimeline({
        ra: raNum,
        dec: decNum,
        radius: radiusNum,
      });
      setTimelineData(result);
      if (result.epochs.length > 0) {
        setSelectedEpochIndex(0);
        const firstEpoch = result.epochs[0];
        if (firstEpoch.products.length > 0) {
          const firstProd = firstEpoch.products[0];
          setSelectedProduct(firstProd);
          setComparisonProductA(firstProd);
          if (result.epochs.length > 1 && result.epochs[1].products.length > 0) {
            setComparisonProductB(result.epochs[1].products[0]);
          }
        }
      }
    } catch (err) {
      setTimelineError(err instanceof Error ? err.message : "ERR_SIA2: IRSA discovery pipeline failure");
    } finally {
      setIsTimelineLoading(false);
    }
  }, [ra, dec, radius]);

  // Load Benchmark Preset
  const handleLoadBenchmark = () => {
    setRa(BENCHMARK_TARGET.ra.toString());
    setDec(BENCHMARK_TARGET.dec.toString());
    setRadius(BENCHMARK_TARGET.radius.toString());
    setCutoutSize(BENCHMARK_TARGET.size.toString());
  };

  // Generate Cutout for Selected Product
  const handleFetchCutout = useCallback(async (product: TimelineProduct) => {
    if (!product.observation_id) return;
    const raNum = parseFloat(ra);
    const decNum = parseFloat(dec);
    const sizeNum = parseFloat(cutoutSize) || 0.1;

    try {
      setIsCutoutLoading(true);
      setCutoutError(null);
      setInspectorTab("cutout");
      const res = await createCutout(product.observation_id, {
        ra: raNum,
        dec: decNum,
        size: sizeNum,
      });
      setCutoutData(res);
    } catch (err) {
      setCutoutError(err instanceof Error ? err.message : "ERR_IBE: Cutout extraction failed");
    } finally {
      setIsCutoutLoading(false);
    }
  }, [ra, dec, cutoutSize]);

  // Inspect FITS for Selected Product
  const handleInspectFITS = useCallback(async (product: TimelineProduct) => {
    if (!product.observation_id) return;
    const raNum = parseFloat(ra);
    const decNum = parseFloat(dec);
    const sizeNum = parseFloat(cutoutSize) || 0.1;

    try {
      setIsInspectLoading(true);
      setInspectError(null);
      setInspectorTab("inspection");
      const res = await inspectImage({
        observation_id: product.observation_id,
        ra: raNum,
        dec: decNum,
        size: sizeNum,
      });
      setInspectData(res);
    } catch (err) {
      setInspectError(err instanceof Error ? err.message : "ERR_HDU: FITS header inspection failed");
    } finally {
      setIsInspectLoading(false);
    }
  }, [ra, dec, cutoutSize]);

  // Run Differential Comparison between Product A and Product B
  const handleRunComparison = useCallback(async () => {
    if (!comparisonProductA?.observation_id || !comparisonProductB?.observation_id) {
      setComparisonError("ERR_DIFF: Two distinct observation epochs required for differential comparison.");
      return;
    }

    const raNum = parseFloat(ra);
    const decNum = parseFloat(dec);
    const sizeNum = parseFloat(cutoutSize) || 0.1;

    try {
      setIsComparing(true);
      setComparisonError(null);
      setInspectorTab("comparison");
      const res = await compareImages({
        observation_id_a: comparisonProductA.observation_id,
        observation_id_b: comparisonProductB.observation_id,
        ra: raNum,
        dec: decNum,
        size: sizeNum,
      });
      setComparisonResult(res);
    } catch (err) {
      setComparisonError(err instanceof Error ? err.message : "ERR_DIFF: Image reprojection/subtraction failed");
    } finally {
      setIsComparing(false);
    }
  }, [comparisonProductA, comparisonProductB, ra, dec, cutoutSize]);

  const handleSelectProduct = (product: TimelineProduct) => {
    setSelectedProduct(product);
    void handleFetchCutout(product);
  };

  const filteredEpochs = timelineData?.epochs.filter((epoch) => {
    if (selectedBandFilter === "ALL") return true;
    return epoch.bands.includes(selectedBandFilter);
  }) ?? [];

  const currentEpoch: TimelineEpoch | undefined = timelineData?.epochs[selectedEpochIndex];

  return (
    <div className="relative h-screen w-full bg-black text-neutral-200 overflow-y-auto selection:bg-amber-400/30 selection:text-white font-mono antialiased">
      {/* =========================================================================
          MISSION CONTROL TOP FLIGHT DECK (HEADER)
         ========================================================================= */}
      <header className="sticky top-0 z-40 w-full border-b border-neutral-800 bg-black/95 backdrop-blur-md">
        <div className="max-w-7xl mx-auto px-3 sm:px-6 h-16 flex items-center justify-between gap-4">
          {/* Left: Esc Command & Mission Identity */}
          <div className="flex items-center gap-3 sm:gap-6">
            <button
              onClick={onReturnToLanding}
              className="group inline-flex items-center gap-2 rounded border border-neutral-700 bg-neutral-900/80 hover:bg-neutral-800 hover:border-amber-400/60 px-3 py-1.5 text-[11px] font-mono tracking-wider uppercase text-neutral-300 hover:text-white transition-all cursor-pointer active:scale-95"
              title="Return to Core Spacecraft View"
            >
              <span className="text-amber-400 opacity-70 group-hover:-translate-x-0.5 transition-transform">◀</span>
              <span>[ ESC · CORE ]</span>
            </button>

            <div className="flex flex-col">
              <div className="flex items-center gap-2">
                <span
                  className="text-base sm:text-lg font-bold tracking-[0.2em] text-white font-bodoni uppercase"
                  style={{ fontFamily: "'OPTIBodoni-Antiqua', serif" }}
                >
                  ASTROCHRONOSCOPE
                </span>
                <span className="hidden md:inline-block rounded border border-amber-400/40 bg-amber-500/10 px-1.5 py-0.2 text-[9px] font-mono text-amber-400 font-bold uppercase tracking-widest">
                  FLIGHT DECK
                </span>
              </div>
              <div className="flex items-center gap-2 text-[10px] tracking-wider text-neutral-400">
                <span className="text-amber-400/90 font-semibold">MISSION: SPHEREx</span>
                <span className="text-neutral-600">|</span>
                <span className="text-neutral-400">NASA/IPAC IRSA SIA2</span>
              </div>
            </div>
          </div>

          {/* Center: Live UTC Mission Clock & MJD */}
          <div className="hidden lg:flex items-center gap-4 rounded border border-neutral-800 bg-[#07080a] px-3.5 py-1 text-xs">
            <div className="flex items-center gap-2">
              <span className="h-1.5 w-1.5 rounded-full bg-cyan-400 animate-pulse" />
              <span className="text-neutral-500 uppercase text-[10px]">UTC:</span>
              <span className="text-white font-bold tabular-nums">{currentTimeUtc}</span>
            </div>
            <span className="text-neutral-700">|</span>
            <div className="flex items-center gap-2">
              <span className="text-neutral-500 uppercase text-[10px]">MJD:</span>
              <span className="text-amber-400 font-bold tabular-nums">{currentMjd}</span>
            </div>
          </div>

          {/* Right: Telemetry & Backend Link Status */}
          <div className="flex items-center gap-2.5">
            <div className="flex items-center gap-2 rounded border border-neutral-800 bg-[#07080a] px-3 py-1 text-xs">
              <span className="relative flex h-2 w-2">
                {healthStatus === "online" && (
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                )}
                <span
                  className={`relative inline-flex rounded-full h-2 w-2 ${
                    healthStatus === "online"
                      ? "bg-emerald-400"
                      : healthStatus === "connecting"
                        ? "bg-amber-400"
                        : "bg-rose-500"
                  }`}
                />
              </span>
              <div className="flex flex-col text-right">
                <span className="text-[9px] text-neutral-500 uppercase tracking-widest leading-none">
                  {healthStatus === "online" ? "SYS_NOMINAL" : healthStatus === "connecting" ? "ACQUIRING..." : "LINK_OFFLINE"}
                </span>
                <span className="text-[11px] text-white font-bold tracking-wider leading-tight">
                  {health ? `${health.default_collection} [v${health.version}]` : "127.0.0.1:8000"}
                </span>
              </div>
            </div>

            <button
              onClick={handleRefreshHealth}
              disabled={healthStatus === "connecting"}
              className="p-1.5 rounded border border-neutral-800 bg-neutral-900/80 hover:bg-neutral-800 hover:border-neutral-600 text-neutral-400 hover:text-white transition-all cursor-pointer text-xs disabled:opacity-50"
              title="Ping Telemetry Stream"
            >
              [ ↻ ]
            </button>
          </div>
        </div>
      </header>

      {/* =========================================================================
          MISSION CONTROL WORKSPACE BODY
         ========================================================================= */}
      <main className="max-w-7xl mx-auto px-3 sm:px-6 py-5 flex flex-col gap-5">
        {/* Backend Offline Warning Banner */}
        {healthStatus === "offline" && (
          <div className="rounded border border-rose-500/60 bg-rose-950/30 p-3.5 text-xs text-rose-300 flex items-center justify-between gap-4">
            <div className="flex flex-col gap-0.5">
              <div className="flex items-center gap-2">
                <span className="text-rose-400 font-bold">[ CRITICAL: TELEMETRY DISCONNECTED ]</span>
                <span>SPHEREx daemon unreachable on <code className="text-white underline">http://127.0.0.1:8000</code></span>
              </div>
              {healthError && <span className="text-[10px] text-rose-400/90 pl-4">Exception: {healthError}</span>}
            </div>
            <button
              onClick={handleRefreshHealth}
              className="px-3 py-1 bg-rose-500/20 hover:bg-rose-500/40 border border-rose-500 rounded text-[10px] text-white uppercase tracking-widest cursor-pointer"
            >
              [ RE-ESTABLISH LINK ]
            </button>
          </div>
        )}

        {/* =========================================================================
            PANEL 01: TARGET ACQUISITION & OPTICAL RADAR CONSOLE
           ========================================================================= */}
        <section className="relative rounded border border-neutral-800 bg-[#050608] p-4 sm:p-5 shadow-2xl flex flex-col gap-4">
          {/* Mission Control Corner Accents */}
          <div className="pointer-events-none absolute top-0 left-0 w-2 h-2 border-t-2 border-l-2 border-amber-400/80" />
          <div className="pointer-events-none absolute top-0 right-0 w-2 h-2 border-t-2 border-r-2 border-amber-400/80" />
          <div className="pointer-events-none absolute bottom-0 left-0 w-2 h-2 border-b-2 border-l-2 border-amber-400/80" />
          <div className="pointer-events-none absolute bottom-0 right-0 w-2 h-2 border-b-2 border-r-2 border-amber-400/80" />

          {/* Tactical Panel Header */}
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-neutral-800 pb-3">
            <div className="flex items-center gap-3">
              <span className="rounded bg-amber-400/10 border border-amber-400/30 px-1.5 py-0.5 text-[10px] text-amber-400 font-bold uppercase tracking-wider">
                SYS.01
              </span>
              <div>
                <h2 className="text-sm sm:text-base font-bold tracking-[0.16em] text-white uppercase">
                  Target Acquisition & Pointing Console
                </h2>
                <span className="text-[10px] text-neutral-400 block tracking-wider">
                  NASA/IPAC IRSA SIA2 QUERY PROTOCOL · ICRS CELESTIAL FRAME
                </span>
              </div>
            </div>

            {/* Benchmark Auto-Lock Button */}
            <div className="flex items-center gap-2">
              <button
                onClick={handleLoadBenchmark}
                className="inline-flex items-center gap-2 rounded border border-amber-400/40 bg-amber-500/10 hover:bg-amber-500/20 px-3 py-1.5 text-xs text-amber-300 transition-all cursor-pointer active:scale-95"
                title="Lock on Benchmark Candidate: RA 156.09328°, Dec -41.64466°"
              >
                <span className="text-amber-400">⌖</span>
                <span className="font-bold tracking-wider">[ LOCK BENCHMARK: PLANET X ]</span>
              </button>
            </div>
          </div>

          {/* Coordinate Precision Input Grid */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3.5">
            <div className="flex flex-col gap-1 rounded border border-neutral-800/80 bg-black/60 p-2.5">
              <label htmlFor={raInputId} className="text-[9px] uppercase tracking-wider text-neutral-400 flex justify-between">
                <span>Right Ascension (RA)</span>
                <span className="text-neutral-500">ICRS DEG</span>
              </label>
              <input
                id={raInputId}
                type="text"
                value={ra}
                onChange={(e) => setRa(e.target.value)}
                placeholder="156.09328159"
                className="w-full bg-transparent text-sm text-white font-bold placeholder-neutral-700 focus:outline-none focus:text-amber-400 tabular-nums"
              />
            </div>

            <div className="flex flex-col gap-1 rounded border border-neutral-800/80 bg-black/60 p-2.5">
              <label htmlFor={decInputId} className="text-[9px] uppercase tracking-wider text-neutral-400 flex justify-between">
                <span>Declination (Dec)</span>
                <span className="text-neutral-500">ICRS DEG</span>
              </label>
              <input
                id={decInputId}
                type="text"
                value={dec}
                onChange={(e) => setDec(e.target.value)}
                placeholder="-41.64466331"
                className="w-full bg-transparent text-sm text-white font-bold placeholder-neutral-700 focus:outline-none focus:text-amber-400 tabular-nums"
              />
            </div>

            <div className="flex flex-col gap-1 rounded border border-neutral-800/80 bg-black/60 p-2.5">
              <label htmlFor={radiusInputId} className="text-[9px] uppercase tracking-wider text-neutral-400 flex justify-between">
                <span>Search Cone Radius</span>
                <span className="text-neutral-500">DEG</span>
              </label>
              <input
                id={radiusInputId}
                type="text"
                value={radius}
                onChange={(e) => setRadius(e.target.value)}
                placeholder="0.01"
                className="w-full bg-transparent text-sm text-white font-bold placeholder-neutral-700 focus:outline-none focus:text-amber-400 tabular-nums"
              />
            </div>

            <div className="flex flex-col gap-1 rounded border border-neutral-800/80 bg-black/60 p-2.5">
              <label htmlFor={sizeInputId} className="text-[9px] uppercase tracking-wider text-neutral-400 flex justify-between">
                <span>Cutout FOV Diameter</span>
                <span className="text-neutral-500">DEG</span>
              </label>
              <input
                id={sizeInputId}
                type="text"
                value={cutoutSize}
                onChange={(e) => setCutoutSize(e.target.value)}
                placeholder="0.1"
                className="w-full bg-transparent text-sm text-white font-bold placeholder-neutral-700 focus:outline-none focus:text-amber-400 tabular-nums"
              />
            </div>

            {/* Tactical Execute Scan CTA */}
            <div className="flex flex-col justify-end">
              <button
                onClick={handleQueryTimeline}
                disabled={isTimelineLoading}
                className="w-full h-full min-h-[44px] rounded border border-amber-400/60 bg-amber-500/15 hover:bg-amber-500/30 text-amber-200 hover:text-white px-4 py-2 text-xs font-bold uppercase tracking-[0.18em] transition-all cursor-pointer active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2 shadow-[0_0_15px_rgba(245,158,11,0.15)]"
              >
                {isTimelineLoading ? (
                  <>
                    <TwinOrbit className="size-3 text-amber-400" />
                    <span>[ SCANNING SIA2... ]</span>
                  </>
                ) : (
                  <>
                    <span>[ EXECUTE RADAR SCAN ]</span>
                    <span className="text-amber-400">▶</span>
                  </>
                )}
              </button>
            </div>
          </div>

          {timelineError && (
            <div className="rounded border border-rose-500/50 bg-rose-950/20 p-2.5 text-xs text-rose-300">
              {timelineError}
            </div>
          )}
        </section>

        {/* =========================================================================
            PANEL 02: FLIGHT TELEMETRY HUD (METRICS RIBBON)
           ========================================================================= */}
        {timelineData && (
          <section className="grid grid-cols-2 md:grid-cols-4 gap-3 animate-in fade-in duration-300">
            {/* Metric 1 */}
            <div className="relative rounded border border-neutral-800 bg-[#060709] p-3.5 flex flex-col gap-1">
              <div className="flex items-center justify-between text-[9px] uppercase tracking-wider text-neutral-400">
                <span>UNIQUE OBSERVATION EPOCHS</span>
                <span className="text-amber-400">01</span>
              </div>
              <div className="flex items-baseline gap-2">
                <span className="text-2xl font-bold text-white tabular-nums">
                  {timelineData.unique_epochs}
                </span>
                <span className="text-[10px] text-neutral-500 uppercase">PASSES</span>
              </div>
              <span className="text-[9px] text-neutral-400 truncate">
                {timelineData.earliest_observation_time?.split("T")[0]} → {timelineData.latest_observation_time?.split("T")[0]}
              </span>
            </div>

            {/* Metric 2 */}
            <div className="relative rounded border border-neutral-800 bg-[#060709] p-3.5 flex flex-col gap-1">
              <div className="flex items-center justify-between text-[9px] uppercase tracking-wider text-neutral-400">
                <span>CALIBRATED SCIENCE PRODUCTS</span>
                <span className="text-cyan-400">02</span>
              </div>
              <div className="flex items-baseline gap-2">
                <span className="text-2xl font-bold text-white tabular-nums">
                  {timelineData.total_products}
                </span>
                <span className="text-[10px] text-neutral-500 uppercase">FITS IMAGES</span>
              </div>
              <span className="text-[9px] text-emerald-400 truncate">
                ✓ {timelineData.quality.metadata_complete_products} WITH FULL WCS AROMETRY
              </span>
            </div>

            {/* Metric 3 */}
            <div className="relative rounded border border-neutral-800 bg-[#060709] p-3.5 flex flex-col gap-1">
              <div className="flex items-center justify-between text-[9px] uppercase tracking-wider text-neutral-400">
                <span>MISSION TEMPORAL BASELINE</span>
                <span className="text-amber-400">03</span>
              </div>
              <div className="flex items-baseline gap-2">
                <span className="text-2xl font-bold text-white tabular-nums">
                  {timelineData.temporal_baseline_days !== null
                    ? timelineData.temporal_baseline_days.toFixed(1)
                    : "0.0"}
                </span>
                <span className="text-[10px] text-neutral-500 uppercase">DAYS SPAN</span>
              </div>
              <span className="text-[9px] text-neutral-400 truncate">
                ARCHIVE: {timelineData.query.collection} · RADIUS: {timelineData.query.radius_deg}°
              </span>
            </div>

            {/* Metric 4 */}
            <div className="relative rounded border border-neutral-800 bg-[#060709] p-3.5 flex flex-col gap-1">
              <div className="flex items-center justify-between text-[9px] uppercase tracking-wider text-neutral-400">
                <span>SPECTRAL CHANNELS ACTIVE</span>
                <span className="text-cyan-400">04</span>
              </div>
              <div className="flex flex-wrap gap-1 mt-0.5">
                {timelineData.available_bands.map((band) => (
                  <span
                    key={band}
                    className="rounded border border-neutral-700 bg-neutral-900 px-1 py-0.2 text-[9px] font-bold text-neutral-300"
                  >
                    {band.replace("SPHEREx-", "")}
                  </span>
                ))}
              </div>
              <span className="text-[9px] text-cyan-400 truncate">
                6-BAND INFRARED SPECTROPHOTOMETRY
              </span>
            </div>
          </section>
        )}

        {/* =========================================================================
            PANEL 03 & 04: SPLIT WORKSPACE
            Left: Multi-Epoch Timeline Matrix (5 cols)
            Right: Optical Cutout & Differential Science Deck (7 cols)
           ========================================================================= */}
        {timelineData ? (
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 items-start">
            {/* =====================================================================
                LEFT COLUMN: MULTI-EPOCH TIMELINE MATRIX (5 cols)
               ===================================================================== */}
            <div className="lg:col-span-5 flex flex-col gap-4">
              {/* Spectral Channel Filter Tabs */}
              <div className="flex items-center gap-1.5 overflow-x-auto pb-1 scrollbar-none">
                <button
                  onClick={() => setSelectedBandFilter("ALL")}
                  className={`rounded px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider transition-all cursor-pointer ${
                    selectedBandFilter === "ALL"
                      ? "border border-amber-400 bg-amber-500/20 text-amber-200"
                      : "border border-neutral-800 bg-neutral-900/60 text-neutral-400 hover:text-white"
                  }`}
                >
                  ALL [{timelineData.epochs.length}]
                </button>
                {SPHEREX_BANDS.map((channel) => {
                  const count = timelineData.band_counts[channel.id] || 0;
                  const isActive = selectedBandFilter === channel.id;
                  return (
                    <button
                      key={channel.id}
                      onClick={() => setSelectedBandFilter(channel.id)}
                      className={`rounded px-2 py-1 text-[10px] font-bold uppercase tracking-wider transition-all cursor-pointer ${
                        isActive
                          ? "border border-cyan-400 bg-cyan-500/20 text-cyan-200"
                          : "border border-neutral-800 bg-neutral-900/60 text-neutral-400 hover:text-white"
                      }`}
                      title={`${channel.name}: ${channel.range}`}
                    >
                      {channel.name} [{count}]
                    </button>
                  );
                })}
              </div>

              {/* Epoch List Panel */}
              <div className="rounded border border-neutral-800 bg-[#050608] p-3.5 flex flex-col gap-2.5 max-h-[640px] overflow-y-auto">
                <div className="flex items-center justify-between border-b border-neutral-800 pb-2 text-[10px] uppercase text-neutral-400">
                  <span className="font-bold tracking-wider">
                    SUBSYSTEM 02 : FLIGHT TIMELINE [{filteredEpochs.length} EPOCHS]
                  </span>
                  <span className="text-neutral-500">SELECT TO VIEW</span>
                </div>

                <div className="flex flex-col gap-1.5">
                  {filteredEpochs.map((epoch) => {
                    const isSelected = epoch.epoch_index === selectedEpochIndex;
                    const dateStr = epoch.observation_time
                      ? new Date(epoch.observation_time).toISOString().replace("T", " ").replace(".000Z", "Z")
                      : "TIME_UNKNOWN";

                    return (
                      <div
                        key={epoch.epoch_key}
                        onClick={() => {
                          setSelectedEpochIndex(epoch.epoch_index);
                          if (epoch.products.length > 0) {
                            handleSelectProduct(epoch.products[0]);
                          }
                        }}
                        className={`p-2.5 rounded border transition-all cursor-pointer flex flex-col gap-1.5 ${
                          isSelected
                            ? "border-amber-400/80 bg-amber-500/10 shadow-[0_0_12px_rgba(245,158,11,0.1)]"
                            : "border-neutral-800/80 bg-neutral-950/60 hover:bg-neutral-900/60 hover:border-neutral-700"
                        }`}
                      >
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2">
                            <span className="text-[11px] font-bold text-white">
                              EPOCH #{String(epoch.epoch_index + 1).padStart(2, "0")}
                            </span>
                            <span className="text-[10px] text-neutral-400 tabular-nums">
                              MJD {epoch.midpoint_mjd ? epoch.midpoint_mjd.toFixed(4) : "—"}
                            </span>
                          </div>
                          <span className="text-[10px] text-amber-400 font-bold">
                            {epoch.product_count} {epoch.product_count === 1 ? "FILE" : "FILES"}
                          </span>
                        </div>

                        <div className="text-[10px] text-neutral-300 truncate tabular-nums">
                          {dateStr}
                        </div>

                        <div className="flex items-center justify-between gap-2 pt-1 border-t border-neutral-800/60">
                          <div className="flex items-center gap-1">
                            {epoch.bands.map((b) => (
                              <span
                                key={b}
                                className="rounded px-1.5 py-0.2 bg-neutral-800 text-[9px] font-bold text-neutral-300"
                              >
                                {b.replace("SPHEREx-", "")}
                              </span>
                            ))}
                          </div>

                          {/* Quick Set Buttons for Differential Analysis */}
                          <div className="flex items-center gap-1">
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                if (epoch.products[0]) setComparisonProductA(epoch.products[0]);
                              }}
                              className={`px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider transition-colors ${
                                comparisonProductA?.obs_id === epoch.products[0]?.obs_id
                                  ? "bg-amber-400 text-black"
                                  : "border border-neutral-700 text-neutral-400 hover:text-white"
                              }`}
                              title="Designate as Reference Image (Epoch A)"
                            >
                              [ SET A ]
                            </button>
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                if (epoch.products[0]) setComparisonProductB(epoch.products[0]);
                              }}
                              className={`px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider transition-colors ${
                                comparisonProductB?.obs_id === epoch.products[0]?.obs_id
                                  ? "bg-cyan-400 text-black"
                                  : "border border-neutral-700 text-neutral-400 hover:text-white"
                              }`}
                              title="Designate as Target Image (Epoch B)"
                            >
                              [ SET B ]
                            </button>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Products within Current Epoch */}
              {currentEpoch && currentEpoch.products.length > 0 && (
                <div className="rounded border border-neutral-800 bg-[#050608] p-3.5 flex flex-col gap-2">
                  <div className="flex items-center justify-between text-[10px] uppercase text-neutral-400 border-b border-neutral-800 pb-1.5">
                    <span className="font-bold tracking-wider">
                      EPOCH #{String(currentEpoch.epoch_index + 1).padStart(2, "0")} SCIENCE PRODUCTS
                    </span>
                    <span className="text-amber-400">{currentEpoch.products.length} AVAILABLE</span>
                  </div>

                  <div className="flex flex-col gap-1.5">
                    {currentEpoch.products.map((p) => {
                      const isSelected = selectedProduct?.observation_id === p.observation_id;
                      return (
                        <div
                          key={p.observation_id || p.obs_publisher_did}
                          className={`p-2 rounded border transition-all flex flex-col gap-1.5 ${
                            isSelected
                              ? "border-amber-400/80 bg-amber-500/10"
                              : "border-neutral-800 bg-black/40 hover:bg-neutral-900/60"
                          }`}
                        >
                          <div className="flex items-center justify-between">
                            <span className="rounded bg-neutral-800 px-1.5 py-0.5 text-[9px] font-bold text-amber-300">
                              {p.band || "BAND_UNKNOWN"}
                            </span>
                            <span className="text-[10px] text-neutral-400 tabular-nums">
                              EXP: {p.exposure_time_seconds || 1}s · LEVEL {p.calib_level ?? 2}
                            </span>
                          </div>

                          <div className="text-[9px] text-neutral-400 truncate" title={p.obs_publisher_did || p.observation_id || ""}>
                            {p.obs_publisher_did || p.observation_id}
                          </div>

                          <div className="flex items-center gap-2 pt-1 border-t border-neutral-800/60">
                            <button
                              onClick={() => handleSelectProduct(p)}
                              className="flex-1 py-1 rounded bg-neutral-800 hover:bg-neutral-700 text-[10px] uppercase tracking-wider text-white transition-all cursor-pointer font-bold"
                            >
                              {isCutoutLoading && isSelected ? "[ ACQUIRING... ]" : "[ VIEW CUTOUT ]"}
                            </button>
                            <button
                              onClick={() => void handleInspectFITS(p)}
                              className="py-1 px-3 rounded border border-neutral-700 hover:bg-neutral-800 text-[10px] uppercase tracking-wider text-neutral-300 hover:text-white transition-all cursor-pointer font-bold"
                            >
                              [ INSPECT HDU ]
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>

            {/* =====================================================================
                RIGHT COLUMN: OPTICAL CUTOUT & DIFFERENTIAL SCIENCE DECK (7 cols)
               ===================================================================== */}
            <div className="lg:col-span-7 flex flex-col gap-3.5">
              {/* Tactical Sub-Deck Tabs */}
              <div className="flex items-center justify-between border-b border-neutral-800 pb-2">
                <div className="flex items-center gap-1.5">
                  <button
                    onClick={() => setInspectorTab("cutout")}
                    className={`px-3 py-1.5 rounded text-[11px] font-bold uppercase tracking-wider transition-all cursor-pointer ${
                      inspectorTab === "cutout"
                        ? "border border-amber-400/80 bg-amber-500/20 text-amber-200"
                        : "text-neutral-400 hover:text-white"
                    }`}
                  >
                    [ 01 · OPTICAL CUTOUT ]
                  </button>
                  <button
                    onClick={() => setInspectorTab("inspection")}
                    className={`px-3 py-1.5 rounded text-[11px] font-bold uppercase tracking-wider transition-all cursor-pointer ${
                      inspectorTab === "inspection"
                        ? "border border-amber-400/80 bg-amber-500/20 text-amber-200"
                        : "text-neutral-400 hover:text-white"
                    }`}
                  >
                    [ 02 · FITS HDU MATRIX ]
                  </button>
                  <button
                    onClick={() => setInspectorTab("comparison")}
                    className={`px-3 py-1.5 rounded text-[11px] font-bold uppercase tracking-wider transition-all cursor-pointer ${
                      inspectorTab === "comparison"
                        ? "border border-cyan-400/80 bg-cyan-500/20 text-cyan-200"
                        : "text-neutral-400 hover:text-white"
                    }`}
                  >
                    [ 03 · DIFFERENTIAL RESIDUAL ]
                  </button>
                </div>

                {selectedProduct && (
                  <span className="text-[11px] text-amber-400 font-bold">
                    CHANNEL: {selectedProduct.band}
                  </span>
                )}
              </div>

              {/* ===================================================================
                  TAB 01: OPTICAL CUTOUT & PREVIEW HUD
                 =================================================================== */}
              {inspectorTab === "cutout" && (
                <div className="relative rounded border border-neutral-800 bg-[#050608] p-4 sm:p-5 flex flex-col gap-4">
                  {/* Panel Corner Accents */}
                  <div className="pointer-events-none absolute top-0 left-0 w-2 h-2 border-t-2 border-l-2 border-amber-400/80" />
                  <div className="pointer-events-none absolute top-0 right-0 w-2 h-2 border-t-2 border-r-2 border-amber-400/80" />
                  <div className="pointer-events-none absolute bottom-0 left-0 w-2 h-2 border-b-2 border-l-2 border-amber-400/80" />
                  <div className="pointer-events-none absolute bottom-0 right-0 w-2 h-2 border-b-2 border-r-2 border-amber-400/80" />

                  <div className="flex items-center justify-between border-b border-neutral-800 pb-2">
                    <div>
                      <h3 className="text-sm font-bold tracking-[0.16em] text-white uppercase">
                        Optical Telescope Focal Plane Terminal
                      </h3>
                      <span className="text-[10px] text-neutral-400 block tracking-wider">
                        SPHEREx LEVEL-2/3 CALIBRATED SCIENCE IMAGE · IBE CUTOUT ENGINE
                      </span>
                    </div>

                    {cutoutData?.cutout.fits_url && (
                      <a
                        href={getFileUrl(cutoutData.cutout.fits_url)}
                        download
                        className="inline-flex items-center gap-1.5 rounded border border-neutral-700 bg-neutral-900 hover:bg-neutral-800 px-3 py-1 text-[11px] font-bold text-white transition-all uppercase"
                      >
                        <span>[ ↓ EXPORT FITS RAW ]</span>
                      </a>
                    )}
                  </div>

                  {/* High-Tech Optical Viewer Frame */}
                  <div className="relative aspect-square max-h-[460px] w-full rounded border border-neutral-800 bg-black overflow-hidden flex items-center justify-center">
                    {isCutoutLoading ? (
                      <div className="flex flex-col items-center gap-3">
                        <TwinOrbit className="size-4 text-amber-400" />
                        <span className="text-xs text-neutral-400 uppercase tracking-widest">
                          [ EXTRACTING FITS CUTOUT FROM IRSA ARCHIVE... ]
                        </span>
                      </div>
                    ) : cutoutData ? (
                      <div className="relative h-full w-full flex items-center justify-center group select-none">
                        <img
                          src={getFileUrl(cutoutData.cutout.preview_url)}
                          alt="SPHEREx Optical Cutout"
                          className="h-full w-full object-contain filter contrast-125"
                        />

                        {/* Astronomical Optical HUD Overlay */}
                        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
                          {/* Outer concentric target circle */}
                          <div className="h-40 w-40 rounded-full border border-dashed border-amber-400/20" />
                          {/* Inner concentric reticle */}
                          <div className="absolute h-16 w-16 rounded-full border border-amber-400/40 flex items-center justify-center">
                            <div className="h-2 w-2 rounded-full border border-amber-400 bg-amber-400/30" />
                          </div>
                          {/* Vertical & Horizontal Crosshair Axes */}
                          <div className="absolute h-full w-[1px] bg-amber-400/20" />
                          <div className="absolute w-full h-[1px] bg-amber-400/20" />

                          {/* Cardinal Compass Markers */}
                          <span className="absolute top-2 text-[10px] font-bold text-amber-400/60">N ▲</span>
                          <span className="absolute left-2 text-[10px] font-bold text-amber-400/60">◄ E</span>
                        </div>

                        {/* WCS Target Lock Corner Pill */}
                        <div className="absolute bottom-3 left-3 rounded border border-neutral-800 bg-black/90 px-3 py-1.5 text-[10px] text-neutral-300 flex flex-col gap-0.5 shadow-2xl">
                          <div className="flex items-center gap-1.5">
                            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                            <span className="text-white font-bold uppercase tracking-wider">
                              {cutoutData.wcs.target_inside_image ? "TARGET ACQUIRED IN FOV" : "OUTSIDE FOV"}
                            </span>
                          </div>
                          <span className="text-neutral-400 tabular-nums">
                            PIXEL WCS: X={cutoutData.wcs.pixel_x?.toFixed(1) ?? "—"}, Y={cutoutData.wcs.pixel_y?.toFixed(1) ?? "—"}
                          </span>
                        </div>

                        {/* Image Metadata Corner Pill */}
                        <div className="absolute top-3 right-3 rounded border border-neutral-800 bg-black/90 px-3 py-1.5 text-[10px] text-neutral-300 text-right shadow-2xl">
                          <span className="text-white font-bold tabular-nums">
                            DIM: {cutoutData.cutout.width} × {cutoutData.cutout.height} PX
                          </span>
                          <span className="text-amber-400 block uppercase">
                            FLUX: {cutoutData.cutout.unit || "MJy / sr"}
                          </span>
                        </div>
                      </div>
                    ) : (
                      <div className="flex flex-col items-center gap-2 p-6 text-center">
                        <span className="text-neutral-500 text-xs uppercase tracking-wider">
                          [ NO ACTIVE PRODUCT SELECTED · SELECT AN EPOCH IN TIMELINE ]
                        </span>
                        {selectedProduct && (
                          <button
                            onClick={() => handleFetchCutout(selectedProduct)}
                            className="mt-2 rounded border border-neutral-700 bg-neutral-900 hover:bg-neutral-800 px-4 py-1.5 text-xs text-white uppercase tracking-wider transition-all cursor-pointer font-bold"
                          >
                            [ GENERATE CUTOUT NOW ]
                          </button>
                        )}
                      </div>
                    )}
                  </div>

                  {cutoutError && (
                    <div className="rounded border border-rose-500/50 bg-rose-950/20 p-2.5 text-xs text-rose-300">
                      {cutoutError}
                    </div>
                  )}

                  {/* Cutout Telemetry Grid */}
                  {cutoutData && (
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs pt-2 border-t border-neutral-800">
                      <div className="flex flex-col">
                        <span className="text-neutral-500 text-[9px] uppercase">WCS SOLUTION</span>
                        <span className="text-white font-bold">
                          {cutoutData.wcs.available ? "VALID (ICRS J2000)" : "UNAVAILABLE"}
                        </span>
                      </div>
                      <div className="flex flex-col">
                        <span className="text-neutral-500 text-[9px] uppercase">RESOLUTION</span>
                        <span className="text-white font-bold tabular-nums">
                          {cutoutData.cutout.width} × {cutoutData.cutout.height} PX
                        </span>
                      </div>
                      <div className="flex flex-col">
                        <span className="text-neutral-500 text-[9px] uppercase">POINTING CENTER</span>
                        <span className="text-white font-bold truncate tabular-nums">
                          {ra}° / {dec}°
                        </span>
                      </div>
                      <div className="flex flex-col">
                        <span className="text-neutral-500 text-[9px] uppercase">CALIBRATION UNIT</span>
                        <span className="text-amber-300 font-bold">
                          {cutoutData.cutout.unit || "MJy / sr"}
                        </span>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* ===================================================================
                  TAB 02: FITS HDU & INSTRUMENT INSPECTION MATRIX
                 =================================================================== */}
              {inspectorTab === "inspection" && (
                <div className="relative rounded border border-neutral-800 bg-[#050608] p-4 sm:p-5 flex flex-col gap-4">
                  {/* Panel Corner Accents */}
                  <div className="pointer-events-none absolute top-0 left-0 w-2 h-2 border-t-2 border-l-2 border-amber-400/80" />
                  <div className="pointer-events-none absolute top-0 right-0 w-2 h-2 border-t-2 border-r-2 border-amber-400/80" />
                  <div className="pointer-events-none absolute bottom-0 left-0 w-2 h-2 border-b-2 border-l-2 border-amber-400/80" />
                  <div className="pointer-events-none absolute bottom-0 right-0 w-2 h-2 border-b-2 border-r-2 border-amber-400/80" />

                  <div className="flex items-center justify-between border-b border-neutral-800 pb-2">
                    <div>
                      <h3 className="text-sm font-bold tracking-[0.16em] text-white uppercase">
                        Scientific HDU Header & WCS Matrix
                      </h3>
                      <span className="text-[10px] text-neutral-400 block tracking-wider">
                        MULTI-EXTENSION FITS STRUCTURE · VARIANCE, ZODI & EPSF CALIBRATION
                      </span>
                    </div>

                    {selectedProduct && (
                      <button
                        onClick={() => void handleInspectFITS(selectedProduct)}
                        disabled={isInspectLoading}
                        className="px-3 py-1 rounded border border-neutral-700 bg-neutral-900 hover:bg-neutral-800 text-[11px] font-bold text-white transition-all cursor-pointer uppercase disabled:opacity-50"
                      >
                        {isInspectLoading ? "[ INSPECTING... ]" : "[ RE-INSPECT ]"}
                      </button>
                    )}
                  </div>

                  {isInspectLoading ? (
                    <div className="py-12 flex flex-col items-center justify-center gap-3">
                      <TwinOrbit className="size-4 text-amber-400" />
                      <span className="text-xs text-neutral-400 uppercase tracking-widest">
                        [ ANALYZING FITS HEADER EXTENSIONS & ASTROMETRIC WCS... ]
                      </span>
                    </div>
                  ) : inspectData ? (
                    <div className="flex flex-col gap-4 text-xs">
                      {/* HDU Extensions Table */}
                      <div className="rounded border border-neutral-800 overflow-hidden">
                        <table className="w-full text-left">
                          <thead className="bg-neutral-900/80 text-[9px] uppercase tracking-wider text-neutral-400 border-b border-neutral-800">
                            <tr>
                              <th className="px-3 py-2">HDU</th>
                              <th className="px-3 py-2">NAME</th>
                              <th className="px-3 py-2">KIND</th>
                              <th className="px-3 py-2">MATRIX SHAPE</th>
                              <th className="px-3 py-2">UNIT</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-neutral-800/80 text-[11px]">
                            {((inspectData.inspection as { hdus?: Array<{ index: number; name: string; kind: string; shape?: number[]; unit?: string }> })?.hdus || []).map((hdu) => (
                              <tr key={hdu.index} className="hover:bg-neutral-900/40">
                                <td className="px-3 py-2 text-neutral-500 font-bold">[{hdu.index}]</td>
                                <td className="px-3 py-2 font-bold text-white">{hdu.name}</td>
                                <td className="px-3 py-2 text-neutral-400 uppercase">{hdu.kind}</td>
                                <td className="px-3 py-2 text-amber-300 tabular-nums">
                                  {hdu.shape ? `${hdu.shape.join(" × ")}` : "—"}
                                </td>
                                <td className="px-3 py-2 text-neutral-300">{hdu.unit || "—"}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>

                      {/* Flux Statistics & WCS Grid */}
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <div className="rounded border border-neutral-800 bg-black/60 p-3 flex flex-col gap-1.5">
                          <span className="text-[9px] uppercase tracking-wider text-neutral-400 font-bold">
                            PIXEL FLUX DISTRIBUTION METRICS
                          </span>
                          {Boolean((inspectData.inspection as { stats?: Record<string, unknown> })?.stats) && (
                            <div className="flex flex-col gap-1 text-[11px] text-neutral-300">
                              <div className="flex justify-between">
                                <span className="text-neutral-500">Finite Pixels:</span>
                                <span className="tabular-nums font-bold">{(inspectData.inspection as { stats: { finite_pixels: number } }).stats.finite_pixels}</span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-neutral-500">Mean Flux:</span>
                                <span className="tabular-nums font-bold text-amber-300">{(inspectData.inspection as { stats: { mean?: number } }).stats.mean?.toFixed(4) ?? "—"}</span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-neutral-500">Median Flux:</span>
                                <span className="tabular-nums font-bold">{(inspectData.inspection as { stats: { median?: number } }).stats.median?.toFixed(4) ?? "—"}</span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-neutral-500">Standard Deviation:</span>
                                <span className="tabular-nums font-bold">{(inspectData.inspection as { stats: { std?: number } }).stats.std?.toFixed(4) ?? "—"}</span>
                              </div>
                            </div>
                          )}
                        </div>

                        <div className="rounded border border-neutral-800 bg-black/60 p-3 flex flex-col gap-1.5">
                          <span className="text-[9px] uppercase tracking-wider text-neutral-400 font-bold">
                            CELESTIAL ASTROMETRIC SOLUTION (WCS)
                          </span>
                          {Boolean((inspectData.inspection as { wcs?: Record<string, unknown> })?.wcs) && (
                            <div className="flex flex-col gap-1 text-[11px] text-neutral-300">
                              <div className="flex justify-between">
                                <span className="text-neutral-500">Projection:</span>
                                <span className="font-bold">{(inspectData.inspection as { wcs: { projection?: string } }).wcs.projection || "TAN (Gnomonic)"}</span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-neutral-500">Pixel Scale:</span>
                                <span className="tabular-nums font-bold text-cyan-300">
                                  {(inspectData.inspection as { wcs: { pixel_scale_arcsec?: number } }).wcs.pixel_scale_arcsec?.toFixed(3) ?? "6.2"} arcsec/px
                                </span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-neutral-500">Center RA / Dec:</span>
                                <span className="tabular-nums font-bold truncate">
                                  {(inspectData.inspection as { wcs: { center_ra_deg?: number } }).wcs.center_ra_deg?.toFixed(3) ?? "—"}° /{" "}
                                  {(inspectData.inspection as { wcs: { center_dec_deg?: number } }).wcs.center_dec_deg?.toFixed(3) ?? "—"}°
                                </span>
                              </div>
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div className="py-8 text-center text-xs text-neutral-500 uppercase tracking-wider">
                      [ CLICK &quot;INSPECT HDU&quot; ON AN OBSERVATION PRODUCT TO EXECUTE ANALYSIS ]
                    </div>
                  )}

                  {inspectError && (
                    <div className="rounded border border-rose-500/50 bg-rose-950/20 p-2.5 text-xs text-rose-300">
                      {inspectError}
                    </div>
                  )}
                </div>
              )}

              {/* ===================================================================
                  TAB 03: DIFFERENTIAL ANALYSIS ENGINE
                 =================================================================== */}
              {inspectorTab === "comparison" && (
                <div className="relative rounded border border-neutral-800 bg-[#050608] p-4 sm:p-5 flex flex-col gap-4">
                  {/* Panel Corner Accents */}
                  <div className="pointer-events-none absolute top-0 left-0 w-2 h-2 border-t-2 border-l-2 border-cyan-400/80" />
                  <div className="pointer-events-none absolute top-0 right-0 w-2 h-2 border-t-2 border-r-2 border-cyan-400/80" />
                  <div className="pointer-events-none absolute bottom-0 left-0 w-2 h-2 border-b-2 border-l-2 border-cyan-400/80" />
                  <div className="pointer-events-none absolute bottom-0 right-0 w-2 h-2 border-b-2 border-r-2 border-cyan-400/80" />

                  <div className="flex items-center justify-between border-b border-neutral-800 pb-2">
                    <div>
                      <h3 className="text-sm font-bold tracking-[0.16em] text-white uppercase">
                        Differential Reprojection & Residual Engine
                      </h3>
                      <span className="text-[10px] text-neutral-400 block tracking-wider">
                        PIXEL SUBTRACTION: D = I_b - α · I_a · BACKGROUND MATCHING & VARIABILITY
                      </span>
                    </div>

                    <button
                      onClick={handleRunComparison}
                      disabled={isComparing || !comparisonProductA || !comparisonProductB}
                      className="inline-flex items-center gap-2 rounded border border-cyan-400/60 bg-cyan-500/20 hover:bg-cyan-500/30 px-3.5 py-1.5 text-xs font-bold uppercase tracking-wider text-cyan-200 transition-all cursor-pointer disabled:opacity-50"
                    >
                      {isComparing ? (
                        <>
                          <TwinOrbit className="size-3 text-cyan-300" />
                          <span>[ COMPUTING... ]</span>
                        </>
                      ) : (
                        <span>[ EXECUTE DIFFERENCE ]</span>
                      )}
                    </button>
                  </div>

                  {/* Pair Selection Status Banner */}
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div className="rounded border border-amber-400/40 bg-amber-500/10 p-2.5 text-xs">
                      <span className="text-[9px] uppercase tracking-wider text-amber-300 font-bold block mb-1">
                        REFERENCE BASELINE (EPOCH A)
                      </span>
                      {comparisonProductA ? (
                        <div className="flex flex-col gap-0.5">
                          <span className="text-white font-bold">{comparisonProductA.band}</span>
                          <span className="text-neutral-400 text-[10px] truncate tabular-nums">
                            {comparisonProductA.observation_time?.split("T")[0] || comparisonProductA.obs_id}
                          </span>
                        </div>
                      ) : (
                        <span className="text-neutral-500">[ SELECT VIA TIMELINE ]</span>
                      )}
                    </div>

                    <div className="rounded border border-cyan-400/40 bg-cyan-500/10 p-2.5 text-xs">
                      <span className="text-[9px] uppercase tracking-wider text-cyan-300 font-bold block mb-1">
                        TARGET PROSPECT (EPOCH B)
                      </span>
                      {comparisonProductB ? (
                        <div className="flex flex-col gap-0.5">
                          <span className="text-white font-bold">{comparisonProductB.band}</span>
                          <span className="text-neutral-400 text-[10px] truncate tabular-nums">
                            {comparisonProductB.observation_time?.split("T")[0] || comparisonProductB.obs_id}
                          </span>
                        </div>
                      ) : (
                        <span className="text-neutral-500">[ SELECT VIA TIMELINE ]</span>
                      )}
                    </div>
                  </div>

                  {/* Comparison Result Display */}
                  {comparisonResult && (
                    <div className="flex flex-col gap-3.5 pt-2 border-t border-neutral-800 animate-in fade-in duration-300">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <span
                            className={`rounded px-2 py-0.5 text-[9px] font-bold uppercase tracking-wider ${
                              comparisonResult.scientifically_valid
                                ? "bg-emerald-500/20 border border-emerald-500 text-emerald-300"
                                : "bg-amber-500/20 border border-amber-500 text-amber-300"
                            }`}
                          >
                            {comparisonResult.scientifically_valid ? "✓ SCIENTIFICALLY VALID" : "⚠ FLAGGED CAVEATS"}
                          </span>
                          <span className="text-xs text-neutral-400">
                            STATUS: {comparisonResult.status.toUpperCase()}
                          </span>
                        </div>

                        {comparisonResult.difference?.fits_url && (
                          <a
                            href={getFileUrl(comparisonResult.difference.fits_url)}
                            download
                            className="inline-flex items-center gap-1.5 rounded border border-neutral-700 bg-neutral-900 hover:bg-neutral-800 px-3 py-1 text-[11px] font-bold text-white transition-all uppercase"
                          >
                            <span>[ ↓ EXPORT DIFFERENCE FITS ]</span>
                          </a>
                        )}
                      </div>

                      {/* Difference Preview Frame */}
                      {comparisonResult.difference?.preview_url && (
                        <div className="relative aspect-square max-h-[360px] w-full rounded border border-neutral-800 bg-black overflow-hidden flex items-center justify-center">
                          <img
                            src={getFileUrl(comparisonResult.difference.preview_url)}
                            alt="Difference Map Preview"
                            className="h-full w-full object-contain filter contrast-150"
                          />
                          <div className="absolute top-2 left-2 rounded border border-neutral-800 bg-black/90 px-2 py-0.5 text-[9px] text-white uppercase tracking-wider font-bold">
                            RESIDUAL MAP: D = I_b - α · I_a
                          </div>
                        </div>
                      )}

                      {/* Difference Statistics Grid */}
                      {comparisonResult.difference?.statistics && (
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 text-xs rounded border border-neutral-800 bg-black/60 p-3">
                          <div>
                            <span className="text-[9px] text-neutral-500 uppercase block">Mean Residual</span>
                            <span className="text-white font-bold tabular-nums">
                              {comparisonResult.difference.statistics.mean?.toFixed(4) ?? "—"}
                            </span>
                          </div>
                          <div>
                            <span className="text-[9px] text-neutral-500 uppercase block">Std Deviation</span>
                            <span className="text-white font-bold tabular-nums">
                              {comparisonResult.difference.statistics.std?.toFixed(4) ?? "—"}
                            </span>
                          </div>
                          <div>
                            <span className="text-[9px] text-neutral-500 uppercase block">Valid Pixels</span>
                            <span className="text-white font-bold tabular-nums">
                              {((comparisonResult.difference.statistics.valid_fraction || 1) * 100).toFixed(1)}%
                            </span>
                          </div>
                          <div>
                            <span className="text-[9px] text-neutral-500 uppercase block">Dimensions</span>
                            <span className="text-white font-bold tabular-nums">
                              {comparisonResult.difference.width} × {comparisonResult.difference.height}
                            </span>
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {comparisonError && (
                    <div className="rounded border border-rose-500/50 bg-rose-950/20 p-2.5 text-xs text-rose-300">
                      {comparisonError}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        ) : (
          /* Empty / Standby Mission Console State */
          <div className="relative rounded border border-neutral-800 bg-[#050608] p-12 text-center flex flex-col items-center justify-center gap-4">
            {/* Panel Corner Accents */}
            <div className="pointer-events-none absolute top-0 left-0 w-2 h-2 border-t-2 border-l-2 border-amber-400/80" />
            <div className="pointer-events-none absolute top-0 right-0 w-2 h-2 border-t-2 border-r-2 border-amber-400/80" />
            <div className="pointer-events-none absolute bottom-0 left-0 w-2 h-2 border-b-2 border-l-2 border-amber-400/80" />
            <div className="pointer-events-none absolute bottom-0 right-0 w-2 h-2 border-b-2 border-r-2 border-amber-400/80" />

            <div className="h-12 w-12 rounded border border-amber-400/40 bg-amber-500/10 flex items-center justify-center text-amber-400 text-lg font-bold">
              ⌖
            </div>

            <div className="max-w-md flex flex-col gap-1">
              <h3 className="text-sm sm:text-base font-bold tracking-[0.2em] text-white uppercase">
                MISSION CONTROL STANDBY
              </h3>
              <p className="text-xs text-neutral-400">
                Awaiting celestial coordinates. Click &quot;LOCK BENCHMARK: PLANET X&quot; or input celestial coordinates to initiate SIA2 multi-epoch acquisition.
              </p>
            </div>

            <button
              onClick={handleQueryTimeline}
              disabled={isTimelineLoading}
              className="mt-2 inline-flex items-center gap-2 rounded border border-amber-400/60 bg-amber-500/15 hover:bg-amber-500/25 px-5 py-2 text-xs font-bold uppercase tracking-wider text-amber-200 transition-all cursor-pointer active:scale-95"
            >
              <span>[ INITIALIZE BENCHMARK SCAN ]</span>
              <span className="text-amber-400">▶</span>
            </button>
          </div>
        )}
      </main>
    </div>
  );
}
