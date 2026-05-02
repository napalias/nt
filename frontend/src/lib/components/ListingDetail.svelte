<script lang="ts">
	import {
		classifyListing,
		submitFeedback,
		type EvaluationResult,
		type ListingResult
	} from '$lib/api/client';

	let {
		listing,
		onclose
	}: {
		listing: ListingResult;
		onclose: () => void;
	} = $props();

	let evaluation = $state<EvaluationResult | null>(null);
	let evalLoading = $state(false);
	let feedbackMode = $state<'like' | 'dislike' | null>(null);
	let feedbackReason = $state('');
	let feedbackSending = $state(false);
	let feedbackResult = $state<string[] | null>(null);

	async function loadEvaluation() {
		evalLoading = true;
		try {
			evaluation = await classifyListing(listing.id);
		} catch {
			evaluation = null;
		} finally {
			evalLoading = false;
		}
	}

	async function sendFeedback() {
		if (!feedbackMode || !feedbackReason.trim()) return;
		feedbackSending = true;
		try {
			const resp = await submitFeedback(listing.id, feedbackMode, feedbackReason);
			feedbackResult = resp.extracted_preferences;
			feedbackReason = '';
			feedbackMode = null;
			evaluation = null;
		} catch {
			/* ignore */
		} finally {
			feedbackSending = false;
		}
	}

	function formatPrice(price: number | null): string {
		if (!price) return '—';
		return new Intl.NumberFormat('lt-LT', {
			style: 'currency',
			currency: 'EUR',
			maximumFractionDigits: 0
		}).format(price);
	}

	const verdictConfig: Record<string, { label: string; color: string; bg: string }> = {
		match: { label: 'Tinka', color: 'text-green-700', bg: 'bg-green-100' },
		review: { label: 'Peržiūrėti', color: 'text-yellow-700', bg: 'bg-yellow-100' },
		skip: { label: 'Praleisti', color: 'text-red-700', bg: 'bg-red-100' }
	};

	$effect(() => {
		if (listing) {
			evaluation = null;
			feedbackResult = null;
			loadEvaluation();
		}
	});
</script>

<div class="flex h-full flex-col overflow-hidden bg-white">
	<!-- Header -->
	<div class="flex items-center justify-between border-b border-gray-200 px-4 py-3">
		<h2 class="truncate text-sm font-semibold text-gray-900">{listing.title}</h2>
		<button
			onclick={onclose}
			aria-label="Uždaryti"
			class="ml-2 rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
		>
			<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
			</svg>
		</button>
	</div>

	<div class="flex-1 overflow-y-auto">
		<!-- Photos -->
		{#if listing.photo_urls.length > 0}
			<div class="flex gap-1 overflow-x-auto p-2">
				{#each listing.photo_urls.slice(0, 6) as url}
					<img src={url} alt="" class="h-32 w-44 flex-shrink-0 rounded object-cover" loading="lazy" />
				{/each}
			</div>
		{/if}

		<!-- Price + key info -->
		<div class="border-b border-gray-100 px-4 py-3">
			<div class="text-2xl font-bold text-blue-600">{formatPrice(listing.price)}</div>
			{#if listing.price_per_sqm}
				<div class="text-sm text-gray-400">{Math.round(listing.price_per_sqm)} €/m²</div>
			{/if}

			<div class="mt-3 grid grid-cols-3 gap-3 text-center">
				{#if listing.area_sqm}
					<div>
						<div class="text-lg font-semibold text-gray-900">{listing.area_sqm}</div>
						<div class="text-xs text-gray-400">m²</div>
					</div>
				{/if}
				{#if listing.rooms}
					<div>
						<div class="text-lg font-semibold text-gray-900">{listing.rooms}</div>
						<div class="text-xs text-gray-400">kamb.</div>
					</div>
				{/if}
				{#if listing.plot_area_ares}
					<div>
						<div class="text-lg font-semibold text-gray-900">{listing.plot_area_ares}</div>
						<div class="text-xs text-gray-400">arai</div>
					</div>
				{/if}
			</div>
		</div>

		<!-- Details -->
		<div class="border-b border-gray-100 px-4 py-3">
			<div class="space-y-1.5 text-sm">
				<div class="flex justify-between">
					<span class="text-gray-500">Adresas</span>
					<span class="text-right text-gray-900">{listing.address_raw}</span>
				</div>
				{#if listing.year_built}
					<div class="flex justify-between">
						<span class="text-gray-500">Metai</span>
						<span class="text-gray-900">{listing.year_built}</span>
					</div>
				{/if}
				{#if listing.building_type}
					<div class="flex justify-between">
						<span class="text-gray-500">Tipas</span>
						<span class="text-gray-900">{listing.building_type}</span>
					</div>
				{/if}
				{#if listing.floor}
					<div class="flex justify-between">
						<span class="text-gray-500">Aukštas</span>
						<span class="text-gray-900">{listing.floor}{listing.total_floors ? ` / ${listing.total_floors}` : ''}</span>
					</div>
				{/if}
				{#if listing.cadastral_number}
					<div class="flex justify-between">
						<span class="text-gray-500">Kadastro Nr.</span>
						<a
							href="/property/{listing.cadastral_number}"
							class="text-purple-600 hover:underline"
						>
							{listing.cadastral_number}
						</a>
					</div>
				{/if}
				<div class="flex justify-between">
					<span class="text-gray-500">Šaltinis</span>
					<a href={listing.source_url} target="_blank" class="text-blue-600 hover:underline">{listing.source}</a>
				</div>
			</div>
		</div>

		<!-- Action buttons -->
		<div class="flex gap-2 border-b border-gray-100 px-4 py-3">
			{#if listing.source_url}
				<a
					href={listing.source_url}
					target="_blank"
					rel="noopener noreferrer"
					class="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-xs font-medium text-white transition hover:bg-blue-700"
				>
					<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
					</svg>
					Atidaryti originale
				</a>
			{/if}
			{#if listing.cadastral_number}
				<a
					href="/property/{listing.cadastral_number}"
					class="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-purple-300 px-3 py-2 text-xs font-medium text-purple-700 transition hover:bg-purple-50"
				>
					<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
					</svg>
					Sklypo ataskaita
				</a>
			{/if}
		</div>

		<!-- AI Evaluation -->
		<div class="border-b border-gray-100 px-4 py-3">
			<h3 class="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">AI vertinimas</h3>

			{#if evalLoading}
				<div class="flex items-center gap-2 text-sm text-gray-400">
					<div class="h-4 w-4 animate-spin rounded-full border-2 border-blue-500 border-t-transparent"></div>
					Vertinama...
				</div>
			{:else if evaluation}
				{@const vc = verdictConfig[evaluation.verdict] || verdictConfig.review}
				<div class="space-y-2">
					<div class="flex items-center gap-2">
						<span class="rounded-full px-2.5 py-0.5 text-xs font-semibold {vc.bg} {vc.color}">
							{vc.label}
						</span>
						<span class="text-sm font-medium text-gray-700">
							{Math.round(evaluation.match_score * 100)}%
						</span>
					</div>

					<p class="text-sm leading-relaxed text-gray-700">{evaluation.summary}</p>

					<!-- Hard filters -->
					<div class="flex flex-wrap gap-1">
						{#each Object.entries(evaluation.hard_filter_results) as [key, ok]}
							<span class="rounded px-1.5 py-0.5 text-[10px] {ok ? 'bg-green-50 text-green-600' : 'bg-red-50 text-red-600'}">
								{key.replace('_ok', '')} {ok ? '✓' : '✗'}
							</span>
						{/each}
					</div>

					{#if evaluation.red_flags.length > 0}
						<div class="rounded bg-red-50 p-2">
							<div class="text-xs font-medium text-red-700">Raudonos vėliavos:</div>
							<ul class="mt-1 space-y-0.5">
								{#each evaluation.red_flags as flag}
									<li class="text-xs text-red-600">• {flag}</li>
								{/each}
							</ul>
						</div>
					{/if}

					{#if evaluation.quality_notes.length > 0}
						<div class="rounded bg-gray-50 p-2">
							<div class="text-xs font-medium text-gray-600">Pastabos:</div>
							<ul class="mt-1 space-y-0.5">
								{#each evaluation.quality_notes as note}
									<li class="text-xs text-gray-500">• {note}</li>
								{/each}
							</ul>
						</div>
					{/if}
				</div>
			{:else}
				<p class="text-sm text-gray-400">Vertinimo nėra</p>
			{/if}
		</div>

		<!-- Feedback -->
		<div class="px-4 py-3">
			<h3 class="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">Jūsų nuomonė</h3>

			{#if feedbackResult}
				<div class="rounded bg-blue-50 p-3">
					<p class="text-xs font-medium text-blue-700">Išmokti prioritetai:</p>
					<ul class="mt-1 space-y-1">
						{#each feedbackResult as pref}
							<li class="text-xs text-blue-600">• {pref}</li>
						{/each}
					</ul>
				</div>
			{:else if feedbackMode}
				<div class="space-y-2">
					<textarea
						bind:value={feedbackReason}
						placeholder={feedbackMode === 'like' ? 'Kas patiko? (pvz. geras išplanavimas, didelis sklypas...)' : 'Kas nepatiko? (pvz. per arti kelio, per maža...)'}
						class="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
						rows="3"
					></textarea>
					<div class="flex gap-2">
						<button
							onclick={sendFeedback}
							disabled={feedbackSending || !feedbackReason.trim()}
							class="flex-1 rounded bg-blue-600 py-1.5 text-xs font-medium text-white transition hover:bg-blue-700 disabled:opacity-50"
						>
							{feedbackSending ? 'Siunčiama...' : 'Siųsti'}
						</button>
						<button
							onclick={() => (feedbackMode = null)}
							class="rounded border border-gray-300 px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-50"
						>
							Atšaukti
						</button>
					</div>
				</div>
			{:else}
				<div class="flex gap-2">
					<button
						onclick={() => (feedbackMode = 'like')}
						class="flex-1 rounded border border-green-300 py-2 text-sm font-medium text-green-700 transition hover:bg-green-50"
					>
						👍 Patinka
					</button>
					<button
						onclick={() => (feedbackMode = 'dislike')}
						class="flex-1 rounded border border-red-300 py-2 text-sm font-medium text-red-700 transition hover:bg-red-50"
					>
						👎 Nepatinka
					</button>
				</div>
			{/if}
		</div>
	</div>
</div>
