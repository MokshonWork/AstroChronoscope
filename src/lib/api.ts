/**
 * Planet X — SPHEREx Data Acquisition API Client
 * Type-safe interface to the NASA/IPAC IRSA SPHEREx backend.
 */

export const API_BASE_URL = (import.meta.env.VITE_API_URL || 'http://localhost:8000').replace(/\/+$/, '');

export interface TargetCoordinates {
  ra: number;
  dec: number;
  frame?: 'ICRS';
}

export interface BackendHealthResponse {
  success: boolean;
  status: string;
  version: string;
  default_collection: string;
}

export interface ObservationSearchRequest {
  ra: number;
  dec: number;
  radius?: number;
  collection?: string;
  start_date?: string;
  end_date?: string;
}

export interface ObservationSummary {
  observation_id: string;
  obs_id?: string | null;
  obs_publisher_did?: string | null;
  observation_date?: string | null;
  observation_date_mjd?: number | null;
  instrument?: string | null;
  facility?: string | null;
  collection?: string | null;
  band?: string | null;
  product_type?: string | null;
  product_subtype?: string | null;
  calib_level?: number | null;
  fits_available: boolean;
}

export interface ObservationSearchResponse {
  success: boolean;
  target: TargetCoordinates;
  collection: string | null;
  total_observations: number;
  observations: ObservationSummary[];
}

export interface ObservationDetail extends ObservationSummary {
  s_ra?: number | null;
  s_dec?: number | null;
  t_min_mjd?: number | null;
  t_max_mjd?: number | null;
  start_time?: string | null;
  end_time?: string | null;
  exposure_time_seconds?: number | null;
  access_format?: string | null;
  access_estsize_bytes?: number | null;
  width?: number | null;
  height?: number | null;
  pixel_scale_arcsec?: number | null;
  fov_deg?: number | null;
  fits_access_url?: string | null;
  processing_status: 'ready' | 'no_fits_product';
}

export interface ObservationDetailResponse {
  success: boolean;
  observation: ObservationDetail;
}

export interface CutoutRequest {
  ra: number;
  dec: number;
  size?: number;
}

export interface CutoutResponse {
  success: boolean;
  observation_id: string;
  cutout: {
    fits_url: string;
    preview_url: string;
    width: number;
    height: number;
    unit: string;
  };
  wcs: {
    available: boolean;
    target_inside_image: boolean;
    pixel_x?: number | null;
    pixel_y?: number | null;
    message?: string | null;
  };
}

export interface TimelineProduct {
  observation_id?: string | null;
  obs_publisher_did?: string | null;
  obs_id?: string | null;
  band?: string | null;
  em_min_m?: number | null;
  em_max_m?: number | null;
  t_min_mjd?: number | null;
  t_max_mjd?: number | null;
  start_time?: string | null;
  end_time?: string | null;
  observation_time?: string | null;
  timeline_eligible: boolean;
  exposure_time_seconds?: number | null;
  calib_level?: number | null;
  dataproduct_type?: string | null;
  dataproduct_subtype?: string | null;
  target_name?: string | null;
  facility?: string | null;
  instrument?: string | null;
  collection?: string | null;
  fits_available: boolean;
  fits_access_url?: string | null;
  access_url_trusted: boolean;
  processing_status: 'ready' | 'no_fits_product' | 'untrusted_access_url';
  s_ra?: number | null;
  s_dec?: number | null;
  fov_deg?: number | null;
  width?: number | null;
  height?: number | null;
  pixel_scale_arcsec?: number | null;
  target_covered?: boolean | null;
  angular_distance_deg?: number | null;
  metadata_complete: boolean;
  missing_fields: string[];
  warnings: string[];
}

export interface TimelineEpoch {
  epoch_index: number;
  epoch_key: string;
  obs_id: string | null;
  identified: boolean;
  observation_time: string | null;
  start_time: string | null;
  end_time: string | null;
  midpoint_mjd: number | null;
  duration_seconds: number | null;
  timeline_eligible: boolean;
  bands: string[];
  product_count: number;
  target_covered: boolean | null;
  target_covered_products: number;
  footprint_center_ra: number | null;
  footprint_center_dec: number | null;
  footprint_fov_deg: number | null;
  facility: string | null;
  instrument: string | null;
  collection: string | null;
  products: TimelineProduct[];
  warnings: string[];
}

export interface TimelineResponse {
  success: boolean;
  target: TargetCoordinates;
  query: {
    radius_deg: number;
    collection: string;
    bands?: string[] | null;
    start_date?: string | null;
    end_date?: string | null;
  };
  total_product_rows: number;
  total_products: number;
  unique_observations: number;
  unique_epochs: number;
  earliest_observation_time: string | null;
  latest_observation_time: string | null;
  temporal_baseline_days: number | null;
  available_bands: string[];
  band_counts: Record<string, number>;
  epochs: TimelineEpoch[];
  quality: {
    total_product_rows: number;
    products_included: number;
    duplicate_products_removed: number;
    products_with_valid_timestamps: number;
    products_without_valid_timestamps: number;
    products_with_band: number;
    products_without_band: number;
    products_with_spatial_metadata: number;
    products_without_spatial_metadata: number;
    products_target_covered: number;
    products_target_outside: number;
    products_target_coverage_unknown: number;
    products_missing_identifier: number;
    inconsistent_epochs: number;
    metadata_complete_products: number;
    metadata_incomplete_products: number;
  };
  warnings: Array<{ code: string; message: string; count: number }>;
  metadata: {
    time_standard: string;
    time_format: string;
    timestamp_source: string;
    observation_time_definition: string;
    epoch_grouping: string;
    frame: string;
    comparability_assessed: boolean;
    comparability_note: string;
  };
}

export interface TimelineQueryParams {
  ra: number;
  dec: number;
  radius?: number;
  collection?: string;
  bands?: string[];
  start_date?: string;
  end_date?: string;
}

export interface InspectRequest {
  observation_id: string;
  ra?: number;
  dec?: number;
  size?: number;
}

export interface InspectResponse {
  success: boolean;
  inspection: Record<string, unknown>;
}

export interface CompareRequest {
  observation_id_a: string;
  observation_id_b: string;
  ra: number;
  dec: number;
  size?: number;
  config?: {
    background_correction?: 'none' | 'median';
    photometric_scaling?: 'none' | 'median_ratio';
    psf_matching?: 'none' | 'gaussian';
    reference?: 'first' | 'second';
    interpolation?: 'bilinear' | 'nearest';
  };
}

export interface DifferenceProduct {
  fits_url: string;
  preview_url: string | null;
  uncertainty_url?: string | null;
  mask_url?: string | null;
  width: number;
  height: number;
  unit?: string | null;
  uncertainty_available: boolean;
  uncertainty_valid_fraction?: number | null;
  uncertainty_note: string;
  statistics: {
    finite_fraction: number;
    valid_fraction: number;
    min?: number | null;
    max?: number | null;
    mean?: number | null;
    median?: number | null;
    std?: number | null;
    sum?: number | null;
  };
}

export interface ComparisonCheckResult {
  name: string;
  status: 'PASS' | 'WARNING' | 'FAIL' | 'NOT_VERIFIED';
  critical: boolean;
  message: string;
  details?: Record<string, unknown> | null;
}

export interface ComparisonResponse {
  success: boolean;
  comparison_id: string;
  status: 'completed' | 'rejected';
  scientifically_valid: boolean;
  comparability_assessed: boolean;
  target: TargetCoordinates;
  difference?: DifferenceProduct | null;
  quality?: {
    overall_status: 'PASS' | 'WARNING' | 'FAIL' | 'NOT_VERIFIED';
    checks: ComparisonCheckResult[];
    critical_checks: string[];
    failed_checks: string[];
    unverified_checks: string[];
    warnings: string[];
  };
  metadata?: {
    size_deg: number;
    difference_model: string;
    note: string;
  };
}

export interface ApiErrorPayload {
  code: string;
  message: string;
  details?: unknown;
}

export class ApiError extends Error {
  code: string;
  status: number;
  details?: unknown;

  constructor(status: number, error: ApiErrorPayload) {
    super(error.message || `API Request failed with status ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.code = error.code || 'UNKNOWN_ERROR';
    this.details = error.details;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const url = `${API_BASE_URL}${path.startsWith('/') ? path : `/${path}`}`;
  const headers = new Headers(options.headers);
  if (!headers.has('Accept')) {
    headers.set('Accept', 'application/json');
  }

  const response = await fetch(url, {
    ...options,
    headers,
  });

  if (!response.ok) {
    let errorData: ApiErrorPayload = {
      code: `HTTP_${response.status}`,
      message: `Request failed with status ${response.status}`,
    };
    try {
      const json = await response.json();
      if (json && json.error) {
        errorData = json.error;
      }
    } catch {
      // Non-JSON response
    }
    throw new ApiError(response.status, errorData);
  }

  return response.json();
}

/** Check connectivity to root backend endpoint */
export async function checkBackend(): Promise<{ message: string }> {
  return request<{ message: string }>('/');
}

/** Get structured backend health and SPHEREx collection info */
export async function getBackendHealth(): Promise<BackendHealthResponse> {
  return request<BackendHealthResponse>('/api/v1/health');
}

/** Search IRSA for SPHEREx observations matching cone and filters */
export async function searchObservations(payload: ObservationSearchRequest): Promise<ObservationSearchResponse> {
  return request<ObservationSearchResponse>('/api/v1/observations/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

/** Get cached or retrieved metadata for a specific observation */
export async function getObservation(observationId: string): Promise<ObservationDetailResponse> {
  return request<ObservationDetailResponse>(`/api/v1/observations/${encodeURIComponent(observationId)}`);
}

/** Create spatial FITS cutout and generate PNG preview */
export async function createCutout(observationId: string, payload: CutoutRequest): Promise<CutoutResponse> {
  return request<CutoutResponse>(`/api/v1/observations/${encodeURIComponent(observationId)}/cutout`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

/** Query multi-epoch target timeline from IRSA */
export async function getTargetTimeline(params: TimelineQueryParams): Promise<TimelineResponse> {
  const searchParams = new URLSearchParams();
  searchParams.set('ra', params.ra.toString());
  searchParams.set('dec', params.dec.toString());
  if (params.radius !== undefined) searchParams.set('radius', params.radius.toString());
  if (params.collection) searchParams.set('collection', params.collection);
  if (params.start_date) searchParams.set('start_date', params.start_date);
  if (params.end_date) searchParams.set('end_date', params.end_date);
  if (params.bands && params.bands.length > 0) {
    for (const b of params.bands) {
      searchParams.append('bands', b);
    }
  }

  return request<TimelineResponse>(`/api/v1/targets/timeline?${searchParams.toString()}`);
}

/** Inspect a FITS product HDUs, WCS and image statistics */
export async function inspectImage(payload: InspectRequest): Promise<InspectResponse> {
  return request<InspectResponse>('/api/v1/images/inspect', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

/** Compare two SPHEREx epochs on a common grid */
export async function compareImages(payload: CompareRequest): Promise<ComparisonResponse> {
  return request<ComparisonResponse>('/api/v1/images/compare', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

/** Retrieve an existing comparison record */
export async function getComparison(comparisonId: string): Promise<ComparisonResponse> {
  return request<ComparisonResponse>(`/api/v1/images/comparisons/${encodeURIComponent(comparisonId)}`);
}

/** Helper to convert backend file paths into absolute URLs */
export function getFileUrl(path: string): string {
  if (!path) return '';
  if (path.startsWith('http://') || path.startsWith('https://')) return path;
  return `${API_BASE_URL}${path.startsWith('/') ? path : `/${path}`}`;
}
