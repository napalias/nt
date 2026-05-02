<script lang="ts">
	import type { EvaluationResult, ListingResult } from '$lib/api/client';

	let {
		listing,
		evaluation = undefined,
		active = false,
		onhover,
		onclick
	}: {
		listing: ListingResult;
		evaluation?: EvaluationResult;
		active?: boolean;
		onhover?: (id: number | null) => void;
		onclick?: (listing: ListingResult) => void;
	} = $props();

	function formatPrice(price: number | null): string {
		if (!price) return '—';
		return new Intl.NumberFormat('lt-LT', {
			style: 'currency',
			currency: 'EUR',
			maximumFractionDigits: 0
		}).format(price);
	}

	function formatDistance(m: number): string {
		if (m < 1000) return `${Math.round(m)} m`;
		return `${(m / 1000).toFixed(1)} km`;
	}

	const verdictStyles: Record<string, string> = {
		match: 'bg-green-100 text-green-700',
		review: 'bg-yellow-100 text-yellow-700',
		skip: 'bg-red-100 text-red-700'
	};

	const verdictLabels: Record<string, string> = {
		match: 'Tinka',
		review: 'Peržiūrėti',
		skip: 'Praleisti'
	};

	const propertyLabels: Record<string, string> = {
		house: 'Namas',
		flat: 'Butas',
		plot: 'Sklypas',
		cottage: 'Sodyba',
		commercial: 'Komercinis'
	};
</script>

<button
	type="button"
	id="listing-{listing.id}"
	onmouseenter={() => onhover?.(listing.id)}
	onmouseleave={() => onhover?.(null)}
	onclick={() => onclick?.(listing)}
	class="w-full rounded-lg border bg-white p-3 text-left transition-all hover:shadow-md {active
		? 'border-blue-500 shadow-md ring-1 ring-blue-200'
		: 'border-gray-200'}"
>
	<div class="flex gap-3">
		{#if listing.photo_urls.length > 0}
			<div class="relative h-20 w-28 flex-shrink-0">
				<img
					src={listing.photo_urls[0]}
					alt=""
					class="h-full w-full rounded object-cover"
					loading="lazy"
				/>
				{#if evaluation}
					<span
						class="absolute left-1 top-1 rounded px-1.5 py-0.5 text-[9px] font-bold shadow-sm {verdictStyles[evaluation.verdict] || ''}"
					>
						{Math.round(evaluation.match_score * 100)}%
					</span>
				{/if}
			</div>
		{:else}
			<div
				class="relative flex h-20 w-28 flex-shrink-0 items-center justify-center rounded bg-gray-100 text-xs text-gray-400"
			>
				Nėra nuotr.
				{#if evaluation}
					<span
						class="absolute left-1 top-1 rounded px-1.5 py-0.5 text-[9px] font-bold {verdictStyles[evaluation.verdict] || ''}"
					>
						{Math.round(evaluation.match_score * 100)}%
					</span>
				{/if}
			</div>
		{/if}

		<div class="min-w-0 flex-1">
			<p class="truncate text-sm font-semibold text-gray-900">{listing.title}</p>

			<div class="mt-1 flex items-baseline gap-2">
				<span class="text-base font-bold text-blue-600">{formatPrice(listing.price)}</span>
				{#if listing.price_per_sqm}
					<span class="text-xs text-gray-400">{Math.round(listing.price_per_sqm)} €/m²</span>
				{/if}
			</div>

			<div class="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-gray-500">
				{#if listing.area_sqm}
					<span>{listing.area_sqm} m²</span>
				{/if}
				{#if listing.rooms}
					<span>{listing.rooms} kamb.</span>
				{/if}
				{#if listing.plot_area_ares}
					<span>{listing.plot_area_ares} a</span>
				{/if}
				{#if listing.year_built}
					<span>{listing.year_built} m.</span>
				{/if}
			</div>

			<div class="mt-1 flex items-center justify-between">
				<span class="truncate text-xs text-gray-400">{listing.address_raw}</span>
				<span class="flex-shrink-0 text-xs text-gray-400">{formatDistance(listing.distance_m)}</span>
			</div>
		</div>
	</div>

	<div class="mt-2 flex items-center gap-1.5">
		{#if evaluation}
			<span class="rounded-full px-2 py-0.5 text-[10px] font-medium {verdictStyles[evaluation.verdict] || ''}">
				{verdictLabels[evaluation.verdict] || evaluation.verdict}
			</span>
		{/if}
		<span
			class="rounded-full px-2 py-0.5 text-[10px] font-medium {listing.is_new_construction
				? 'bg-green-100 text-green-700'
				: 'bg-gray-100 text-gray-600'}"
		>
			{listing.is_new_construction
				? 'Nauja statyba'
				: (propertyLabels[listing.property_type] || listing.property_type)}
		</span>
		<span class="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-medium text-gray-500">
			{listing.source}
		</span>
	</div>
</button>
