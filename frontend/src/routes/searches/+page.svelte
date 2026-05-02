<script lang="ts">
	import { onMount } from 'svelte';
	import { getSavedSearches, deleteSavedSearch, type SavedSearch } from '$lib/api/client';

	let searches = $state<SavedSearch[]>([]);
	let loading = $state(true);

	async function load() {
		loading = true;
		try {
			searches = await getSavedSearches();
		} catch {
			searches = [];
		} finally {
			loading = false;
		}
	}

	async function remove(id: number) {
		await deleteSavedSearch(id);
		searches = searches.filter((s) => s.id !== id);
	}

	function searchUrl(s: SavedSearch): string {
		const params = new URLSearchParams();
		params.set('lat', s.lat.toString());
		params.set('lng', s.lng.toString());
		params.set('radius', s.radius_m.toString());
		if (s.min_price) params.set('min_price', s.min_price.toString());
		if (s.max_price) params.set('max_price', s.max_price.toString());
		if (s.rooms) params.set('rooms', s.rooms.toString());
		if (s.property_type) params.set('property_type', s.property_type);
		if (s.is_new_construction) params.set('new', '1');
		return `/search?${params.toString()}`;
	}

	function formatDate(iso: string): string {
		return new Date(iso).toLocaleString('lt-LT', {
			year: 'numeric',
			month: '2-digit',
			day: '2-digit',
			hour: '2-digit',
			minute: '2-digit'
		});
	}

	onMount(load);
</script>

<svelte:head>
	<title>Išsaugotos paieškos — NT Paieška</title>
</svelte:head>

<div class="min-h-screen bg-gray-50">
	<header class="border-b border-gray-200 bg-white px-4 py-3 shadow-sm">
		<div class="mx-auto flex max-w-3xl items-center gap-4">
			<a href="/" class="text-lg font-bold text-gray-900">NT Paieška</a>
			<span class="text-sm text-gray-400">/</span>
			<h1 class="text-sm font-medium text-gray-700">Išsaugotos paieškos</h1>
			<div class="flex-1"></div>
			<a href="/search" class="text-sm text-blue-600 hover:underline">Paieška</a>
		</div>
	</header>

	<main class="mx-auto max-w-3xl px-4 py-8">
		<div class="rounded-lg border border-gray-200 bg-white shadow-sm">
			<div class="border-b border-gray-100 px-6 py-4">
				<h2 class="text-lg font-semibold text-gray-900">Išsaugotos paieškos</h2>
				<p class="mt-1 text-sm text-gray-500">
					Sistema kas 30 min. tikrina ar atsirado naujų skelbimų atitinkančių jūsų paieškas
					ir siunčia pranešimą el. paštu.
				</p>
			</div>

			{#if loading}
				<div class="flex items-center justify-center py-12">
					<div class="h-6 w-6 animate-spin rounded-full border-2 border-blue-500 border-t-transparent"></div>
				</div>
			{:else if searches.length === 0}
				<div class="px-6 py-12 text-center text-sm text-gray-400">
					Nėra išsaugotų paieškų. Paieškos puslapyje paspauskite „Išsaugoti paiešką".
				</div>
			{:else}
				<ul class="divide-y divide-gray-100">
					{#each searches as search (search.id)}
						<li class="flex items-center gap-4 px-6 py-4">
							<div class="min-w-0 flex-1">
								<a
									href={searchUrl(search)}
									class="text-sm font-medium text-blue-600 hover:underline"
								>
									{search.name}
								</a>
								<div class="mt-1 flex flex-wrap gap-2 text-xs text-gray-400">
									<span>Spindulys: {(search.radius_m / 1000).toFixed(1)} km</span>
									{#if search.min_price || search.max_price}
										<span>
											Kaina: {search.min_price ? `€${search.min_price.toLocaleString()}` : '—'}
											– {search.max_price ? `€${search.max_price.toLocaleString()}` : '—'}
										</span>
									{/if}
									{#if search.rooms}
										<span>{search.rooms} kamb.</span>
									{/if}
									{#if search.property_type}
										<span>{search.property_type}</span>
									{/if}
								</div>
								<div class="mt-1 text-xs text-gray-300">
									Paskutinis pranešimas: {formatDate(search.last_notified_at)}
								</div>
							</div>
							<button
								onclick={() => remove(search.id)}
								class="flex-shrink-0 rounded px-3 py-1 text-xs text-gray-400 transition hover:bg-red-50 hover:text-red-600"
							>
								Pašalinti
							</button>
						</li>
					{/each}
				</ul>
			{/if}
		</div>
	</main>
</div>
