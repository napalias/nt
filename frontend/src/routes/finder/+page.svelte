<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import type { EvaluationResult, ListingResult } from '$lib/api/client';

	let { data } = $props();

	type MatchEntry = EvaluationResult & { listing?: ListingResult };

	let reclassifying = $state(false);
	let reclassifyMessage = $state('');
	let feedbackPending = $state<Record<number, boolean>>({});
	let feedbackDone = $state<Record<number, 'like' | 'dislike'>>({});

	let matches = $derived.by(() => {
		const listingMap = new Map<number, ListingResult>();
		for (const l of (data.searchData?.results ?? [])) {
			listingMap.set(l.id, l);
		}
		return (data.evaluations ?? [])
			.filter((e: EvaluationResult) => e.verdict === 'match' || e.verdict === 'review')
			.sort((a: EvaluationResult, b: EvaluationResult) => b.match_score - a.match_score)
			.map((e: EvaluationResult): MatchEntry => ({ ...e, listing: listingMap.get(e.listing_id) }));
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

	async function reclassify() {
		reclassifying = true;
		reclassifyMessage = '';
		try {
			const resp = await fetch('/api/classifier/classify/batch', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ limit: 50 })
			});
			if (!resp.ok) throw new Error(`API error: ${resp.status}`);
			const result = await resp.json();
			reclassifyMessage = `Klasifikuota: ${result.classified_count} skelbimai`;
			await invalidateAll();
		} catch {
			reclassifyMessage = 'Klaida klasifikuojant';
		} finally {
			reclassifying = false;
		}
	}

	async function sendFeedback(listingId: number, feedbackType: 'like' | 'dislike') {
		feedbackPending = { ...feedbackPending, [listingId]: true };
		try {
			const resp = await fetch(`/api/classifier/feedback/${listingId}`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ feedback_type: feedbackType, reason: '' })
			});
			if (!resp.ok) throw new Error(`API error: ${resp.status}`);
			feedbackDone = { ...feedbackDone, [listingId]: feedbackType };
		} catch {
			// silently fail
		} finally {
			feedbackPending = { ...feedbackPending, [listingId]: false };
		}
	}
</script>

<svelte:head>
	<title>Kretinga Finder — NT Paieška</title>
</svelte:head>

<div class="min-h-screen bg-gray-50">
	<div class="mx-auto max-w-3xl px-4 py-8">
		<!-- Header -->
		<div class="mb-6 text-center">
			<h1 class="text-2xl font-bold text-gray-900">Kretinga Finder</h1>
			<p class="mt-1 text-sm text-gray-500">
				AI atrinkti skelbimai pagal jūsų kriterijus ir prioritetus
			</p>
		</div>

		<!-- Actions bar -->
		<div class="mb-6 flex items-center justify-between">
			<span class="text-sm text-gray-500">
				{matches.length} {matches.length === 1 ? 'skelbimas' : 'skelbimai'}
			</span>
			<div class="flex items-center gap-3">
				{#if reclassifyMessage}
					<span class="text-sm text-gray-600">{reclassifyMessage}</span>
				{/if}
				<button
					onclick={reclassify}
					disabled={reclassifying}
					class="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
				>
					{#if reclassifying}
						<span class="inline-flex items-center gap-1.5">
							<span class="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white border-t-transparent"></span>
							Klasifikuojama...
						</span>
					{:else}
						Perklasifikuoti
					{/if}
				</button>
			</div>
		</div>

		<!-- Listing cards -->
		{#if matches.length === 0}
			<div class="rounded-lg border border-gray-200 bg-white p-12 text-center shadow-sm">
				<p class="text-lg text-gray-400">Nėra tinkamų skelbimų</p>
				<p class="mt-2 text-sm text-gray-300">
					Palaukite kol spideriai nuskaitys daugiau skelbimų arba koreguokite AI prioritetus
				</p>
				<a
					href="/search?lat=55.8835&lng=21.242&radius=30000"
					class="mt-4 inline-block text-sm text-blue-600 hover:underline"
				>
					Ieškoti visus skelbimus
				</a>
			</div>
		{:else}
			<div class="space-y-4">
				{#each matches as m (m.listing_id)}
					{@const vc = verdictStyles[m.verdict] || ''}
					{@const fb = feedbackDone[m.listing_id]}
					<div class="rounded-lg border bg-white shadow-sm overflow-hidden">
						<!-- Score + title header -->
						<div class="flex items-center gap-3 px-5 pt-4 pb-2">
							<span class="rounded-full px-2.5 py-0.5 text-xs font-bold {vc}">
								{Math.round(m.match_score * 100)}%
							</span>
							<h2 class="text-base font-semibold text-gray-900 flex-1 min-w-0 truncate">
								{m.listing_title}
							</h2>
						</div>

						<!-- Price + key stats -->
						{#if m.listing}
							<div class="px-5 pb-2">
								<div class="flex flex-wrap items-baseline gap-x-4 gap-y-1">
									<span class="text-xl font-bold text-blue-600">
										{formatPrice(m.listing.price)}
									</span>
									{#if m.listing.price_per_sqm}
										<span class="text-sm text-gray-400">
											{formatPrice(m.listing.price_per_sqm)}/m²
										</span>
									{/if}
								</div>
								<div class="mt-1.5 flex flex-wrap gap-3 text-sm text-gray-600">
									{#if m.listing.area_sqm}
										<span>{m.listing.area_sqm} m²</span>
									{/if}
									{#if m.listing.rooms}
										<span>{m.listing.rooms} kamb.</span>
									{/if}
									{#if m.listing.plot_area_ares}
										<span>{m.listing.plot_area_ares} a sklypas</span>
									{/if}
									{#if m.listing.year_built}
										<span>{m.listing.year_built} m.</span>
									{/if}
									{#if m.listing.address_raw}
										<span class="text-gray-400">{m.listing.address_raw}</span>
									{/if}
								</div>
							</div>
						{/if}

						<!-- AI summary -->
						<div class="px-5 pb-3">
							<p class="text-sm leading-relaxed text-gray-700">{m.summary}</p>
						</div>

						<!-- Hard filters -->
						<div class="px-5 pb-2 flex flex-wrap gap-1">
							{#each Object.entries(m.hard_filter_results) as [key, ok]}
								<span
									class="rounded px-1.5 py-0.5 text-[10px] font-medium {ok
										? 'bg-green-50 text-green-600'
										: 'bg-red-50 text-red-600'}"
								>
									{key.replace('_ok', '')} {ok ? '✓' : '✗'}
								</span>
							{/each}
						</div>

						{#if m.red_flags.length > 0}
							<div class="px-5 pb-2 text-xs text-red-600">
								{m.red_flags.join(' · ')}
							</div>
						{/if}

						<!-- Action buttons -->
						<div class="flex items-center justify-between border-t border-gray-100 px-5 py-3">
							<div class="flex items-center gap-2">
								<button
									onclick={() => sendFeedback(m.listing_id, 'like')}
									disabled={!!feedbackPending[m.listing_id] || !!fb}
									class="inline-flex items-center gap-1 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors
										{fb === 'like'
										? 'bg-green-100 text-green-700'
										: 'bg-gray-100 text-gray-600 hover:bg-green-50 hover:text-green-700'}
										disabled:opacity-50"
								>
									<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="h-4 w-4">
										<path d="M1 8.25a1.25 1.25 0 1 1 2.5 0v7.5a1.25 1.25 0 1 1-2.5 0v-7.5ZM7.188 3.273a1.25 1.25 0 0 0-2.438.587L5.5 7H4.25A2.25 2.25 0 0 0 2 9.25v.636a2.25 2.25 0 0 0 1.494 2.121l4.172 1.457A3.75 3.75 0 0 0 8.9 13.75h3.35a2.25 2.25 0 0 0 2.15-1.588l1.526-5.037A1.75 1.75 0 0 0 14.25 5H10.5l.79-2.137a1.25 1.25 0 0 0-.652-1.59L7.188 3.273Z" />
									</svg>
									Patinka
								</button>
								<button
									onclick={() => sendFeedback(m.listing_id, 'dislike')}
									disabled={!!feedbackPending[m.listing_id] || !!fb}
									class="inline-flex items-center gap-1 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors
										{fb === 'dislike'
										? 'bg-red-100 text-red-700'
										: 'bg-gray-100 text-gray-600 hover:bg-red-50 hover:text-red-700'}
										disabled:opacity-50"
								>
									<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="h-4 w-4">
										<path d="M19 11.75a1.25 1.25 0 1 1-2.5 0v-7.5a1.25 1.25 0 1 1 2.5 0v7.5ZM12.812 16.727a1.25 1.25 0 0 0 2.438-.587L14.5 13h1.25A2.25 2.25 0 0 0 18 10.75v-.636a2.25 2.25 0 0 0-1.494-2.121l-4.172-1.457A3.75 3.75 0 0 0 11.1 6.25H7.75a2.25 2.25 0 0 0-2.15 1.588L4.074 12.875A1.75 1.75 0 0 0 5.75 15H9.5l-.79 2.137a1.25 1.25 0 0 0 .652 1.59l3.45-2Z" />
									</svg>
									Nepatinka
								</button>
							</div>

							{#if m.listing?.source_url}
								<a
									href={m.listing.source_url}
									target="_blank"
									rel="noopener noreferrer"
									class="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-blue-700 transition-colors"
								>
									Atidaryti skelbimą
									<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="h-3.5 w-3.5">
										<path fill-rule="evenodd" d="M4.25 5.5a.75.75 0 0 0-.75.75v8.5c0 .414.336.75.75.75h8.5a.75.75 0 0 0 .75-.75v-4a.75.75 0 0 1 1.5 0v4A2.25 2.25 0 0 1 12.75 17h-8.5A2.25 2.25 0 0 1 2 14.75v-8.5A2.25 2.25 0 0 1 4.25 4h5a.75.75 0 0 1 0 1.5h-5Zm7.25-.75a.75.75 0 0 1 .75-.75h3.5a.75.75 0 0 1 .75.75v3.5a.75.75 0 0 1-1.5 0V6.31l-5.47 5.47a.75.75 0 1 1-1.06-1.06l5.47-5.47H12.25a.75.75 0 0 1-.75-.75Z" clip-rule="evenodd" />
									</svg>
								</a>
							{/if}
						</div>
					</div>
				{/each}
			</div>
		{/if}
	</div>
</div>
