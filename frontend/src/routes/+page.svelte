<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import {
		geocode,
		getEvaluated,
		getSavedSearches,
		getPreferences,
		type GeocodeResult,
		type EvaluationResult,
		type SavedSearch,
		type PreferenceResult
	} from '$lib/api/client';
	import SearchBar from '$lib/components/SearchBar.svelte';

	function handleSelect(result: GeocodeResult) {
		goto(`/search?lat=${result.lat}&lng=${result.lng}&radius=5000`);
	}

	let topMatches = $state<EvaluationResult[]>([]);
	let savedSearches = $state<SavedSearch[]>([]);
	let preferences = $state<PreferenceResult[]>([]);
	let stats = $state({ total: 0, matches: 0, reviews: 0, skips: 0 });
	let loading = $state(true);

	onMount(async () => {
		try {
			const [allEvals, searches, prefs] = await Promise.all([
				getEvaluated().catch(() => []),
				getSavedSearches().catch(() => []),
				getPreferences().catch(() => [])
			]);

			topMatches = allEvals
				.filter((e) => e.verdict === 'match')
				.sort((a, b) => b.match_score - a.match_score)
				.slice(0, 5);

			savedSearches = searches;
			preferences = prefs;

			stats = {
				total: allEvals.length,
				matches: allEvals.filter((e) => e.verdict === 'match').length,
				reviews: allEvals.filter((e) => e.verdict === 'review').length,
				skips: allEvals.filter((e) => e.verdict === 'skip').length
			};
		} finally {
			loading = false;
		}
	});

	function formatPrice(title: string): string {
		const match = title.match(/€([\d,]+)/);
		return match ? `€${match[1]}` : '';
	}
</script>

<svelte:head>
	<title>NT Paieška — Pradžia</title>
</svelte:head>

<div class="min-h-screen bg-gradient-to-br from-blue-50 to-white">
	<!-- Hero -->
	<div class="flex flex-col items-center px-4 pb-8 pt-16">
		<h1 class="text-4xl font-bold tracking-tight text-gray-900">NT Paieška</h1>
		<p class="mt-2 text-lg text-gray-500">
			Nekilnojamojo turto tyrimai — Kretinga ir aplinka
		</p>

		<div class="mt-8 w-full max-w-xl">
			<SearchBar onselect={handleSelect} />
		</div>

		<div class="mt-4 flex flex-wrap justify-center gap-2">
			<a
				href="/search?lat=55.8835&lng=21.242&radius=5000"
				class="rounded-full bg-blue-100 px-4 py-1.5 text-sm font-medium text-blue-700 transition hover:bg-blue-200"
			>
				Kretinga
			</a>
			<a
				href="/search?lat=55.7068&lng=21.1346&radius=5000"
				class="rounded-full bg-blue-100 px-4 py-1.5 text-sm font-medium text-blue-700 transition hover:bg-blue-200"
			>
				Palanga
			</a>
			<a
				href="/search?lat=55.7033&lng=21.1443&radius=5000"
				class="rounded-full bg-blue-100 px-4 py-1.5 text-sm font-medium text-blue-700 transition hover:bg-blue-200"
			>
				Klaipėda
			</a>
			<a
				href="/search?lat=54.6872&lng=25.2797&radius=5000"
				class="rounded-full bg-gray-100 px-4 py-1.5 text-sm font-medium text-gray-600 transition hover:bg-gray-200"
			>
				Vilnius
			</a>
		</div>
	</div>

	{#if !loading}
		<!-- Stats -->
		<div class="mx-auto max-w-4xl px-4 pb-8">
			<div class="grid grid-cols-4 gap-4">
				<div class="rounded-lg border border-gray-200 bg-white p-4 text-center shadow-sm">
					<div class="text-2xl font-bold text-gray-900">{stats.total}</div>
					<div class="text-xs text-gray-500">Įvertinta</div>
				</div>
				<div class="rounded-lg border border-green-200 bg-green-50 p-4 text-center shadow-sm">
					<div class="text-2xl font-bold text-green-700">{stats.matches}</div>
					<div class="text-xs text-green-600">Tinka</div>
				</div>
				<div class="rounded-lg border border-yellow-200 bg-yellow-50 p-4 text-center shadow-sm">
					<div class="text-2xl font-bold text-yellow-700">{stats.reviews}</div>
					<div class="text-xs text-yellow-600">Peržiūrėti</div>
				</div>
				<div class="rounded-lg border border-red-200 bg-red-50 p-4 text-center shadow-sm">
					<div class="text-2xl font-bold text-red-700">{stats.skips}</div>
					<div class="text-xs text-red-600">Praleisti</div>
				</div>
			</div>
		</div>

		<!-- Dashboard grid -->
		<div class="mx-auto grid max-w-4xl grid-cols-2 gap-6 px-4 pb-12">
			<!-- Top matches -->
			<div class="rounded-lg border border-gray-200 bg-white shadow-sm">
				<div class="border-b border-gray-100 px-4 py-3">
					<h2 class="text-sm font-semibold text-gray-900">Geriausi skelbimai</h2>
				</div>
				{#if topMatches.length === 0}
					<div class="px-4 py-6 text-center text-sm text-gray-400">
						Dar nėra vertinimų
					</div>
				{:else}
					<ul class="divide-y divide-gray-50">
						{#each topMatches as ev}
							<li class="flex items-center gap-3 px-4 py-2.5">
								<span
									class="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-green-100 text-xs font-bold text-green-700"
								>
									{Math.round(ev.match_score * 100)}
								</span>
								<div class="min-w-0 flex-1">
									<p class="truncate text-sm text-gray-900">{ev.listing_title}</p>
									<p class="truncate text-xs text-gray-400">{ev.summary.slice(0, 80)}</p>
								</div>
							</li>
						{/each}
					</ul>
				{/if}
			</div>

			<!-- Saved searches + preferences -->
			<div class="space-y-6">
				<!-- Saved searches -->
				<div class="rounded-lg border border-gray-200 bg-white shadow-sm">
					<div class="flex items-center justify-between border-b border-gray-100 px-4 py-3">
						<h2 class="text-sm font-semibold text-gray-900">Išsaugotos paieškos</h2>
						<a href="/searches" class="text-xs text-blue-600 hover:underline">Visos</a>
					</div>
					{#if savedSearches.length === 0}
						<div class="px-4 py-4 text-center text-sm text-gray-400">Nėra</div>
					{:else}
						<ul class="divide-y divide-gray-50">
							{#each savedSearches.slice(0, 3) as s}
								<li class="px-4 py-2">
									<a
										href="/search?lat={s.lat}&lng={s.lng}&radius={s.radius_m}"
										class="text-sm text-blue-600 hover:underline"
									>
										{s.name}
									</a>
								</li>
							{/each}
						</ul>
					{/if}
				</div>

				<!-- Preferences -->
				<div class="rounded-lg border border-gray-200 bg-white shadow-sm">
					<div class="flex items-center justify-between border-b border-gray-100 px-4 py-3">
						<h2 class="text-sm font-semibold text-gray-900">AI prioritetai</h2>
						<a href="/preferences" class="text-xs text-blue-600 hover:underline">Redaguoti</a>
					</div>
					{#if preferences.length === 0}
						<div class="px-4 py-4 text-center text-sm text-gray-400">Dar nėra</div>
					{:else}
						<ul class="divide-y divide-gray-50">
							{#each preferences.slice(0, 4) as p}
								<li class="px-4 py-2 text-xs text-gray-600">
									<span class={p.preference_type === 'like' ? 'text-green-600' : 'text-red-600'}>
										{p.preference_type === 'like' ? '👍' : '👎'}
									</span>
									{p.pattern}
								</li>
							{/each}
						</ul>
					{/if}
				</div>
			</div>
		</div>
	{:else}
		<div class="flex justify-center py-12">
			<div class="h-6 w-6 animate-spin rounded-full border-2 border-blue-500 border-t-transparent"></div>
		</div>
	{/if}
</div>
