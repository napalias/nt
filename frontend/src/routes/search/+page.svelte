<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import {
		searchListings,
		getEvaluated,
		createSavedSearch,
		fetchLayer,
		fetchFullSearch,
		type ListingResult,
		type GeocodeResult,
		type EvaluationResult,
		type GeoJsonCollection,
		type FullSearchLayerCounts
	} from '$lib/api/client';
	import SearchBar from '$lib/components/SearchBar.svelte';
	import Filters from '$lib/components/Filters.svelte';
	import ListingCard from '$lib/components/ListingCard.svelte';
	import ListingDetail from '$lib/components/ListingDetail.svelte';
	import LayerPanel from '$lib/components/LayerPanel.svelte';
	import Map from '$lib/components/Map.svelte';

	let { data } = $props();

	type SortMode = 'distance' | 'score' | 'price_asc' | 'price_desc';

	function buildEvalMap(evals: EvaluationResult[]): Record<number, EvaluationResult> {
		const m: Record<number, EvaluationResult> = {};
		for (const e of evals) m[e.listing_id] = e;
		return m;
	}

	let listings = $state<ListingResult[]>(data.searchData?.results ?? []);
	let evaluations = $state<Record<number, EvaluationResult>>(buildEvalMap(data.evaluations ?? []));
	let count = $state(data.searchData?.count ?? 0);
	let loading = $state(false);
	let error = $state('');
	let activeId = $state<number | null>(null);
	let selectedListing = $state<ListingResult | null>(null);
	let sortMode = $state<SortMode>('score');

	let center = $state(data.center ?? { lat: 55.8835, lng: 21.242 });
	let radiusM = $state(data.radiusM ?? 5000);

	let minPrice = $state<number | undefined>(undefined);
	let maxPrice = $state<number | undefined>(undefined);
	let rooms = $state<number | undefined>(undefined);
	let propertyType = $state<string | undefined>(undefined);
	let isNewConstruction = $state<boolean | undefined>(undefined);

	let sortedListings = $derived.by(() => {
		const arr = [...listings];
		switch (sortMode) {
			case 'score':
				return arr.sort((a, b) => {
					const sa = evaluations[a.id]?.match_score ?? -1;
					const sb = evaluations[b.id]?.match_score ?? -1;
					return sb - sa;
				});
			case 'price_asc':
				return arr.sort((a, b) => (a.price ?? Infinity) - (b.price ?? Infinity));
			case 'price_desc':
				return arr.sort((a, b) => (b.price ?? 0) - (a.price ?? 0));
			case 'distance':
			default:
				return arr.sort((a, b) => a.distance_m - b.distance_m);
		}
	});

	function readUrlParams() {
		const p = $page.url.searchParams;
		if (p.has('lat') && p.has('lng')) {
			center = { lat: parseFloat(p.get('lat')!), lng: parseFloat(p.get('lng')!) };
		}
		if (p.has('radius')) radiusM = parseInt(p.get('radius')!);
		if (p.has('min_price')) minPrice = parseInt(p.get('min_price')!);
		if (p.has('max_price')) maxPrice = parseInt(p.get('max_price')!);
		if (p.has('rooms')) rooms = parseInt(p.get('rooms')!);
		if (p.has('property_type')) propertyType = p.get('property_type')!;
		if (p.has('new')) isNewConstruction = p.get('new') === '1';
	}

	function updateUrl() {
		const params = new URLSearchParams();
		params.set('lat', center.lat.toString());
		params.set('lng', center.lng.toString());
		params.set('radius', radiusM.toString());
		if (minPrice) params.set('min_price', minPrice.toString());
		if (maxPrice) params.set('max_price', maxPrice.toString());
		if (rooms) params.set('rooms', rooms.toString());
		if (propertyType) params.set('property_type', propertyType);
		if (isNewConstruction) params.set('new', '1');
		goto(`/search?${params.toString()}`, { replaceState: true, noScroll: true });
	}

	async function doSearch() {
		loading = true;
		error = '';
		updateUrl();
		try {
			const [searchResp, evalResp] = await Promise.all([
				searchListings({
					lat: center.lat,
					lng: center.lng,
					radius_m: radiusM,
					min_price: minPrice,
					max_price: maxPrice,
					rooms: rooms,
					property_type: propertyType || undefined,
					is_new_construction: isNewConstruction
				}),
				getEvaluated().catch(() => [] as EvaluationResult[])
			]);
			listings = searchResp.results;
			count = searchResp.count;

			const evalMap: Record<number, EvaluationResult> = {};
			for (const e of evalResp) {
				evalMap[e.listing_id] = e;
			}
			evaluations = evalMap;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Paieškos klaida';
			listings = [];
			count = 0;
		} finally {
			loading = false;
		}
	}

	function handleGeocode(result: GeocodeResult) {
		center = { lat: result.lat, lng: result.lng };
		doSearch();
	}

	function handleMarkerClick(listing: ListingResult) {
		activeId = listing.id;
		selectedListing = listing;
		const el = document.getElementById(`listing-${listing.id}`);
		el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
	}

	function handleListHover(id: number | null) {
		activeId = id;
	}

	function handleListClick(listing: ListingResult) {
		selectedListing = listing;
		activeId = listing.id;
	}

	function closeDetail() {
		selectedListing = null;
	}

	let saveSearchName = $state('');
	let showSaveDialog = $state(false);
	let savingSearch = $state(false);
	let savedMessage = $state('');

	async function saveSearch() {
		if (!saveSearchName.trim()) return;
		savingSearch = true;
		try {
			await createSavedSearch({
				name: saveSearchName,
				lat: center.lat,
				lng: center.lng,
				radius_m: radiusM,
				min_price: minPrice,
				max_price: maxPrice,
				rooms: rooms,
				property_type: propertyType,
				is_new_construction: isNewConstruction
			});
			savedMessage = 'Paieška išsaugota!';
			showSaveDialog = false;
			saveSearchName = '';
			setTimeout(() => (savedMessage = ''), 3000);
		} catch {
			savedMessage = 'Klaida saugant';
		} finally {
			savingSearch = false;
		}
	}

	const emptyGeoJson: GeoJsonCollection = { type: 'FeatureCollection', features: [] };

	let layerConfigs = $state([
		{ id: 'cadastre', label: 'Kadastras', color: '#8b5cf6', count: 0, enabled: false },
		{ id: 'heritage', label: 'Paveldas', color: '#ef4444', count: 0, enabled: false },
		{ id: 'restrictions', label: 'Apribojimai', color: '#f97316', count: 0, enabled: false },
		{ id: 'permits', label: 'Leidimai', color: '#0ea5e9', count: 0, enabled: false },
		{ id: 'planning', label: 'Planai', color: '#10b981', count: 0, enabled: false },
		{ id: 'kretinga/zoning', label: 'Kretinga zonos', color: '#6366f1', count: 0, enabled: false },
		{ id: 'kretinga/utilities', label: 'Kretinga tinklai', color: '#84cc16', count: 0, enabled: false }
	]);

	let geoJsonLayers = $state<
		Record<string, { data: GeoJsonCollection; color: string; visible: boolean }>
	>({});

	let currentBbox = $state('');

	async function loadLayer(layerId: string) {
		if (!currentBbox) return;
		try {
			const data = await fetchLayer(layerId as 'cadastre' | 'heritage' | 'restrictions', currentBbox);
			const config = layerConfigs.find((l) => l.id === layerId);
			if (config) {
				config.count = data.features.length;
				geoJsonLayers[layerId] = {
					data,
					color: config.color,
					visible: config.enabled
				};
				geoJsonLayers = { ...geoJsonLayers };
			}
		} catch {
			// Layer endpoint might not exist yet
		}
	}

	function handleLayerToggle(layerId: string, enabled: boolean) {
		const config = layerConfigs.find((l) => l.id === layerId);
		if (config) {
			config.enabled = enabled;
			layerConfigs = [...layerConfigs];
		}
		if (enabled && !geoJsonLayers[layerId]) {
			loadLayer(layerId);
		} else if (geoJsonLayers[layerId]) {
			geoJsonLayers[layerId].visible = enabled;
			geoJsonLayers = { ...geoJsonLayers };
		}
	}

	function handleBboxChange(bbox: string) {
		currentBbox = bbox;
		for (const config of layerConfigs) {
			if (config.enabled) {
				loadLayer(config.id);
			}
		}
	}

	let fullSearchCounts = $state<FullSearchLayerCounts | null>(null);
	let fullSearchLoading = $state(false);
	let fullSearchError = $state('');

	async function loadFullSearch() {
		fullSearchLoading = true;
		fullSearchError = '';
		try {
			const resp = await fetchFullSearch({
				lat: center.lat,
				lng: center.lng,
				radius_m: radiusM
			});
			fullSearchCounts = {
				listings: resp.listings.length,
				permits: resp.permits.length,
				developers: resp.developers.length,
				planning: resp.planning.length,
				heritage: resp.restrictions.heritage.length,
				restrictions: resp.restrictions.special_land_use.length
			};
		} catch {
			fullSearchError = 'Nepavyko gauti duomenu';
		} finally {
			fullSearchLoading = false;
		}
	}

	// Data is loaded server-side via +page.server.ts
	// doSearch() is called when filters change

	const sortOptions: { value: SortMode; label: string }[] = [
		{ value: 'score', label: 'AI balas' },
		{ value: 'distance', label: 'Atstumas' },
		{ value: 'price_asc', label: 'Kaina ↑' },
		{ value: 'price_desc', label: 'Kaina ↓' }
	];
</script>

<svelte:head>
	<title>NT Paieška — {count} skelbimai</title>
</svelte:head>

<div class="flex h-screen flex-col">
	<header class="z-10 border-b border-gray-200 bg-white px-4 py-3 shadow-sm">
		<div class="mx-auto flex max-w-screen-2xl items-center gap-4">
			<a href="/" class="flex-shrink-0 text-lg font-bold text-gray-900">NT Paieška</a>
			<div class="max-w-xl flex-1">
				<SearchBar onselect={handleGeocode} />
			</div>
			<div class="relative flex flex-shrink-0 items-center gap-3">
				{#if showSaveDialog}
					<div class="absolute right-0 top-full z-50 mt-2 w-64 rounded-lg border border-gray-200 bg-white p-3 shadow-lg">
						<input
							type="text"
							bind:value={saveSearchName}
							placeholder="Pavadinimas (pvz. Kretinga namai)"
							class="w-full rounded border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
						/>
						<div class="mt-2 flex gap-2">
							<button
								onclick={saveSearch}
								disabled={savingSearch || !saveSearchName.trim()}
								class="flex-1 rounded bg-blue-600 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
							>
								{savingSearch ? '...' : 'Išsaugoti'}
							</button>
							<button
								onclick={() => (showSaveDialog = false)}
								class="rounded border border-gray-300 px-3 py-1.5 text-xs text-gray-500"
							>
								Atšaukti
							</button>
						</div>
					</div>
				{/if}
				<button
					onclick={() => (showSaveDialog = !showSaveDialog)}
					class="rounded border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-600 transition hover:bg-gray-50"
				>
					Išsaugoti paiešką
				</button>
				{#if savedMessage}
					<span class="text-xs font-medium text-green-600">{savedMessage}</span>
				{/if}
				<a href="/searches" class="text-sm text-gray-500 transition hover:text-blue-600">Paieškos</a>
				<a href="/preferences" class="text-sm text-gray-500 transition hover:text-blue-600">Prioritetai</a>
			</div>
			<div class="flex items-center gap-2 text-sm text-gray-500">
				{#if loading}
					<div
						class="h-4 w-4 animate-spin rounded-full border-2 border-blue-500 border-t-transparent"
					></div>
				{:else}
					<span class="font-semibold text-gray-900">{count}</span> skelbimai
				{/if}
			</div>
		</div>
	</header>

	<div class="flex flex-1 overflow-hidden">
		<!-- Filters -->
		<aside class="w-56 flex-shrink-0 overflow-y-auto border-r border-gray-200 bg-gray-50 p-4">
			<Filters
				bind:minPrice
				bind:maxPrice
				bind:rooms
				bind:propertyType
				bind:isNewConstruction
				onapply={doSearch}
			/>
			<div class="mt-6 border-t border-gray-200 pt-4">
				<LayerPanel layers={layerConfigs} ontoggle={handleLayerToggle} />

				<button
					type="button"
					onclick={loadFullSearch}
					disabled={fullSearchLoading}
					class="mt-3 w-full rounded border border-gray-300 py-1.5 text-xs font-medium text-gray-600 transition hover:bg-gray-50 disabled:opacity-50"
				>
					{fullSearchLoading ? 'Kraunama...' : 'Rodyti visus sluoksnius'}
				</button>

				{#if fullSearchError}
					<p class="mt-2 text-xs text-red-500">{fullSearchError}</p>
				{/if}

				{#if fullSearchCounts}
					<div class="mt-3 space-y-1.5">
						<h4 class="text-[10px] font-semibold uppercase tracking-wide text-gray-400">Rasta aplinkoje</h4>
						<div class="flex items-center justify-between text-xs">
							<span class="text-gray-600">Skelbimai</span>
							<span class="font-medium text-gray-900">{fullSearchCounts.listings}</span>
						</div>
						<div class="flex items-center justify-between text-xs">
							<span class="text-gray-600">Leidimai</span>
							<span class="font-medium text-blue-700">{fullSearchCounts.permits}</span>
						</div>
						<div class="flex items-center justify-between text-xs">
							<span class="text-gray-600">Planai</span>
							<span class="font-medium text-green-700">{fullSearchCounts.planning}</span>
						</div>
						<div class="flex items-center justify-between text-xs">
							<span class="text-gray-600">Vystytojai</span>
							<span class="font-medium text-gray-900">{fullSearchCounts.developers}</span>
						</div>
						<div class="flex items-center justify-between text-xs">
							<span class="text-gray-600">Paveldas</span>
							<span class="font-medium text-red-700">{fullSearchCounts.heritage}</span>
						</div>
						<div class="flex items-center justify-between text-xs">
							<span class="text-gray-600">Apribojimai</span>
							<span class="font-medium text-orange-700">{fullSearchCounts.restrictions}</span>
						</div>
					</div>
				{/if}
			</div>
		</aside>

		<!-- Listings -->
		<div class="flex w-96 flex-shrink-0 flex-col border-r border-gray-200 bg-white">
			<!-- Sort bar -->
			<div class="flex items-center gap-1 border-b border-gray-100 px-2 py-1.5">
				{#each sortOptions as opt}
					<button
						type="button"
						onclick={() => (sortMode = opt.value)}
						class="rounded px-2 py-1 text-xs font-medium transition {sortMode === opt.value
							? 'bg-blue-600 text-white'
							: 'text-gray-500 hover:bg-gray-100'}"
					>
						{opt.label}
					</button>
				{/each}

				<!-- Legend -->
				<div class="ml-auto flex items-center gap-1.5">
					<span class="h-2 w-2 rounded-full bg-green-500" title="Tinka"></span>
					<span class="h-2 w-2 rounded-full bg-yellow-500" title="Peržiūrėti"></span>
					<span class="h-2 w-2 rounded-full bg-red-500" title="Praleisti"></span>
				</div>
			</div>

			<div class="flex-1 overflow-y-auto">
				{#if error}
					<div class="p-4 text-sm text-red-600">{error}</div>
				{:else if sortedListings.length === 0 && !loading}
					<div class="p-8 text-center text-sm text-gray-400">
						Skelbimų nerasta. Pabandykite pakeisti filtrus arba ieškoti kitoje vietovėje.
					</div>
				{:else}
					<div class="space-y-2 p-2">
						{#each sortedListings as listing (listing.id)}
							<ListingCard
								{listing}
								evaluation={evaluations[listing.id]}
								active={activeId === listing.id}
								onhover={handleListHover}
								onclick={handleListClick}
							/>
						{/each}
					</div>
				{/if}
			</div>
		</div>

		<!-- Map -->
		<div class="relative flex-1">
			<Map
				listings={sortedListings}
				{evaluations}
				{geoJsonLayers}
				{center}
				{activeId}
				onmarkerclick={handleMarkerClick}
				onbboxchange={handleBboxChange}
			/>
		</div>

		<!-- Detail panel -->
		{#if selectedListing}
			<div class="w-96 flex-shrink-0 border-l border-gray-200">
				<ListingDetail listing={selectedListing} onclose={closeDetail} />
			</div>
		{/if}
	</div>
</div>
