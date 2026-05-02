export interface ListingResult {
	id: number;
	source: string;
	source_url: string;
	title: string;
	property_type: string;
	listing_type: string;
	price: number | null;
	price_per_sqm: number | null;
	currency: string;
	area_sqm: number | null;
	plot_area_ares: number | null;
	rooms: number | null;
	floor: number | null;
	total_floors: number | null;
	year_built: number | null;
	building_type: string;
	is_new_construction: boolean;
	address_raw: string;
	city: string;
	district: string;
	lat: number;
	lng: number;
	photo_urls: string[];
	distance_m: number;
}

export interface SearchResponse {
	center: { lat: number; lng: number };
	radius_m: number;
	count: number;
	results: ListingResult[];
}

export interface SearchParams {
	lat: number;
	lng: number;
	radius_m?: number;
	min_price?: number;
	max_price?: number;
	rooms?: number;
	property_type?: string;
	listing_type?: string;
	is_new_construction?: boolean;
}

export interface GeocodeResult {
	lat: number;
	lng: number;
	display_name: string;
}

export interface EvaluationResult {
	listing_id: number;
	listing_title: string;
	verdict: string;
	match_score: number;
	summary: string;
	hard_filter_results: Record<string, boolean>;
	quality_notes: string[];
	red_flags: string[];
	classified_at: string;
	model_used: string;
}

export interface FeedbackResponse {
	id: number;
	listing_id: number;
	feedback_type: string;
	reason: string;
	extracted_preferences: string[];
}

export interface PreferenceResult {
	id: number;
	preference_type: string;
	pattern: string;
	weight: number;
	is_active: boolean;
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
	const resp = await fetch(url, init);
	if (!resp.ok) {
		throw new Error(`API error: ${resp.status} ${resp.statusText}`);
	}
	return resp.json();
}

function buildQuery(params: Record<string, unknown>): string {
	const entries = Object.entries(params).filter(
		([, v]) => v !== undefined && v !== null && v !== ''
	);
	if (!entries.length) return '';
	return '?' + entries.map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join('&');
}

export async function searchListings(params: SearchParams): Promise<SearchResponse> {
	const query = buildQuery(params as unknown as Record<string, unknown>);
	return fetchJson<SearchResponse>(`/api/search${query}`);
}

export async function geocode(q: string): Promise<GeocodeResult[]> {
	return fetchJson<GeocodeResult[]>(`/api/geocode?q=${encodeURIComponent(q)}`);
}

export async function classifyListing(listingId: number): Promise<EvaluationResult> {
	return fetchJson<EvaluationResult>(`/api/classifier/classify/${listingId}`, {
		method: 'POST'
	});
}

export async function submitFeedback(
	listingId: number,
	feedbackType: 'like' | 'dislike',
	reason: string
): Promise<FeedbackResponse> {
	return fetchJson<FeedbackResponse>(`/api/classifier/feedback/${listingId}`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ feedback_type: feedbackType, reason })
	});
}

export async function getEvaluated(
	verdict?: string,
	minScore?: number
): Promise<EvaluationResult[]> {
	const query = buildQuery({ verdict, min_score: minScore });
	return fetchJson<EvaluationResult[]>(`/api/classifier/evaluated${query}`);
}

export async function getPreferences(): Promise<PreferenceResult[]> {
	return fetchJson<PreferenceResult[]>('/api/classifier/preferences');
}

export async function deletePreference(id: number): Promise<void> {
	await fetch(`/api/classifier/preferences/${id}`, { method: 'DELETE' });
}

// --- Saved searches ---

export interface SavedSearch {
	id: number;
	name: string;
	lat: number;
	lng: number;
	radius_m: number;
	min_price: number | null;
	max_price: number | null;
	rooms: number | null;
	property_type: string;
	listing_type: string;
	is_new_construction: boolean | null;
	is_active: boolean;
	created_at: string;
	last_notified_at: string;
}

export interface SavedSearchInput {
	name: string;
	lat: number;
	lng: number;
	radius_m?: number;
	min_price?: number;
	max_price?: number;
	rooms?: number;
	property_type?: string;
	is_new_construction?: boolean;
}

export async function createSavedSearch(params: SavedSearchInput): Promise<SavedSearch> {
	return fetchJson<SavedSearch>('/api/searches', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(params)
	});
}

export async function getSavedSearches(): Promise<SavedSearch[]> {
	return fetchJson<SavedSearch[]>('/api/searches');
}

export async function deleteSavedSearch(id: number): Promise<void> {
	await fetch(`/api/searches/${id}`, { method: 'DELETE' });
}

// --- GeoJSON layers ---

export interface GeoJsonFeature {
	type: 'Feature';
	geometry: {
		type: string;
		coordinates: unknown;
	};
	properties: Record<string, unknown>;
}

export interface GeoJsonCollection {
	type: 'FeatureCollection';
	features: GeoJsonFeature[];
}

export async function fetchLayer(
	layer: 'cadastre' | 'heritage' | 'restrictions',
	bbox: string
): Promise<GeoJsonCollection> {
	return fetchJson<GeoJsonCollection>(`/api/layers/${layer}?bbox=${bbox}`);
}

// --- Developers ---

export interface DeveloperResult {
	id: number;
	company_code: string;
	name: string;
	nace_codes: string[];
	registered_address: string;
	lat: number | null;
	lng: number | null;
	founded: string | null;
	status: string;
	employee_count: number | null;
}

export async function fetchDevelopers(bbox: string): Promise<DeveloperResult[]> {
	return fetchJson<DeveloperResult[]>(`/api/developers?bbox=${bbox}`);
}

// --- Permits ---

export interface PermitResult {
	id: number;
	permit_number: string;
	permit_type: string;
	status: string;
	issued_at: string | null;
	applicant_name: string;
	address_raw: string;
	lat: number | null;
	lng: number | null;
	project_type: string;
	building_purpose: string;
	project_description: string;
	source_url: string;
}

export async function fetchPermits(
	bbox: string,
	issuedAfter?: string,
	status?: string
): Promise<PermitResult[]> {
	const params = new URLSearchParams({ bbox });
	if (issuedAfter) params.set('issued_after', issuedAfter);
	if (status) params.set('status', status);
	return fetchJson<PermitResult[]>(`/api/permits?${params.toString()}`);
}

// --- Planning ---

export interface PlanningResult {
	id: number;
	tpdris_id: string;
	title: string;
	doc_type: string;
	status: string;
	municipality: string;
	approved_at: string | null;
	allowed_uses: string[];
	max_height_m: number | null;
	max_floors: number | null;
	max_density: number | null;
	extraction_confidence: number | null;
	source_url: string;
}

export async function fetchPlanning(bbox: string): Promise<PlanningResult[]> {
	return fetchJson<PlanningResult[]>(`/api/planning?bbox=${bbox}`);
}

// --- Property report ---

export interface PropertyReport {
	cadastral_number: string;
	plot: {
		area_sqm: number;
		purpose: string;
		purpose_category: string;
		municipality: string;
	} | null;
	listings: ListingResult[];
	permits: PermitResult[];
	planning: PlanningResult[];
	heritage: { name: string; category: string; protection_level: string }[];
	restrictions: { category: string; description: string }[];
	developers: DeveloperResult[];
}

export async function getPropertyReport(cadastralNumber: string): Promise<PropertyReport> {
	return fetchJson<PropertyReport>(`/api/property/${encodeURIComponent(cadastralNumber)}`);
}
