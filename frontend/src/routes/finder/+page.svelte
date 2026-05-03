<script lang="ts">
	import { onMount } from 'svelte';
	import {
		getEvaluated,
		searchListings,
		type EvaluationResult,
		type ListingResult
	} from '$lib/api/client';

	let matches = $state<(EvaluationResult & { listing?: ListingResult })[]>([]);
	let loading = $state(true);

	onMount(async () => {
		try {
			const [evalResp, searchResp] = await Promise.all([
				getEvaluated(),
				searchListings({ lat: 55.8835, lng: 21.242, radius_m: 50000 })
			]);

			const listingMap = new Map<number, ListingResult>();
			for (const l of searchResp.results) {
				listingMap.set(l.id, l);
			}

			matches = evalResp
				.filter((e) => e.verdict === 'match' || e.verdict === 'review')
				.sort((a, b) => b.match_score - a.match_score)
				.map((e) => ({ ...e, listing: listingMap.get(e.listing_id) }));
		} catch {
			matches = [];
		} finally {
			loading = false;
		}
	});

	function formatPrice(price: number | null | undefined): string {
		if (!price) return '—';
		return new Intl.NumberFormat('lt-LT', {
			style: 'currency',
			currency: 'EUR',
			maximumFractionDigits: 0
		}).format(price);
	}

	const verdictStyles: Record<string, string> = {
		match: 'bg-green-100 text-green-700 border-green-200',
		review: 'bg-yellow-100 text-yellow-700 border-yellow-200'
	};
</script>

<svelte:head>
	<title>Kretinga Finder — NT Paieška</title>
</svelte:head>

<div class="min-h-screen bg-gray-50">
	<div class="mx-auto max-w-3xl px-4 py-8">
		<div class="mb-6 text-center">
			<h1 class="text-2xl font-bold text-gray-900">Kretinga Finder</h1>
			<p class="mt-1 text-sm text-gray-500">
				AI atrinkti skelbimai pagal jūsų kriterijus ir prioritetus
			</p>
		</div>

		{#if loading}
			<div class="flex justify-center py-16">
				<div class="h-8 w-8 animate-spin rounded-full border-2 border-blue-500 border-t-transparent"></div>
			</div>
		{:else if matches.length === 0}
			<div class="rounded-lg border border-gray-200 bg-white p-12 text-center shadow-sm">
				<p class="text-lg text-gray-400">Nėra tinkamų skelbimų</p>
				<p class="mt-2 text-sm text-gray-300">
					Palaukite kol spideriai nuskaitys daugiau skelbimų arba koreguokite AI prioritetus
				</p>
				<a href="/search?lat=55.8835&lng=21.242&radius=30000" class="mt-4 inline-block text-sm text-blue-600 hover:underline">
					Ieškoti visus skelbimus
				</a>
			</div>
		{:else}
			<div class="space-y-4">
				{#each matches as m (m.listing_id)}
					{@const vc = verdictStyles[m.verdict] || ''}
					<div class="rounded-lg border bg-white p-5 shadow-sm {vc}">
						<div class="flex items-start justify-between">
							<div class="flex-1">
								<div class="flex items-center gap-2">
									<span class="rounded-full px-2.5 py-0.5 text-xs font-bold {vc}">
										{Math.round(m.match_score * 100)}%
									</span>
									<h2 class="text-base font-semibold text-gray-900">{m.listing_title}</h2>
								</div>
								<p class="mt-2 text-sm leading-relaxed text-gray-700">{m.summary}</p>

								{#if m.listing}
									<div class="mt-3 flex flex-wrap gap-3 text-sm text-gray-600">
										<span class="font-bold text-blue-600">{formatPrice(m.listing.price)}</span>
										{#if m.listing.area_sqm}
											<span>{m.listing.area_sqm} m²</span>
										{/if}
										{#if m.listing.rooms}
											<span>{m.listing.rooms} kamb.</span>
										{/if}
										{#if m.listing.plot_area_ares}
											<span>{m.listing.plot_area_ares} a</span>
										{/if}
										<span class="text-gray-400">{m.listing.address_raw}</span>
									</div>
								{/if}

								<!-- Hard filters -->
								<div class="mt-2 flex flex-wrap gap-1">
									{#each Object.entries(m.hard_filter_results) as [key, ok]}
										<span class="rounded px-1.5 py-0.5 text-[10px] font-medium {ok ? 'bg-green-50 text-green-600' : 'bg-red-50 text-red-600'}">
											{key.replace('_ok', '')} {ok ? '✓' : '✗'}
										</span>
									{/each}
								</div>

								{#if m.red_flags.length > 0}
									<div class="mt-2 text-xs text-red-600">
										{m.red_flags.join(' · ')}
									</div>
								{/if}
							</div>

							{#if m.listing}
								<a
									href={m.listing.source_url}
									target="_blank"
									rel="noopener noreferrer"
									class="ml-4 flex-shrink-0 rounded-lg bg-blue-600 px-4 py-2 text-xs font-medium text-white hover:bg-blue-700"
								>
									Atidaryti
								</a>
							{/if}
						</div>
					</div>
				{/each}
			</div>
		{/if}
	</div>
</div>
