<script lang="ts">
	import { page } from '$app/stores';
	import { onMount } from 'svelte';
	import { getPropertyReport, type PropertyReport } from '$lib/api/client';

	let report = $state<PropertyReport | null>(null);
	let loading = $state(true);
	let error = $state('');

	const cadastralNumber = $page.params.id ?? '';

	onMount(async () => {
		try {
			report = await getPropertyReport(cadastralNumber);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Nepavyko gauti duomenų';
		} finally {
			loading = false;
		}
	});

	function formatPrice(price: number | null): string {
		if (!price) return '—';
		return new Intl.NumberFormat('lt-LT', {
			style: 'currency',
			currency: 'EUR',
			maximumFractionDigits: 0
		}).format(price);
	}
</script>

<svelte:head>
	<title>Sklypas {cadastralNumber} — NT Paieška</title>
</svelte:head>

<div class="min-h-screen bg-gray-50">
	<div class="mx-auto max-w-4xl px-4 py-4">
		<nav class="flex items-center gap-2 text-sm text-gray-400">
			<a href="/" class="hover:text-gray-600">Pradžia</a>
			<span>/</span>
			<span class="text-gray-700">Sklypas {cadastralNumber}</span>
		</nav>
	</div>

	<main class="mx-auto max-w-4xl px-4 pb-8">
		{#if loading}
			<div class="flex justify-center py-16">
				<div class="h-8 w-8 animate-spin rounded-full border-2 border-blue-500 border-t-transparent"></div>
			</div>
		{:else if error}
			<div class="rounded-lg border border-red-200 bg-red-50 p-6 text-center text-red-700">
				{error}
			</div>
		{:else if report}
			<!-- Plot info -->
			{#if report.plot}
				<div class="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
					<h2 class="text-lg font-semibold text-gray-900">Sklypo informacija</h2>
					<div class="mt-4 grid grid-cols-2 gap-4 text-sm">
						<div>
							<span class="text-gray-500">Kadastro numeris</span>
							<p class="font-medium text-gray-900">{cadastralNumber}</p>
						</div>
						<div>
							<span class="text-gray-500">Plotas</span>
							<p class="font-medium text-gray-900">{report.plot.area_sqm} m²</p>
						</div>
						<div>
							<span class="text-gray-500">Paskirtis</span>
							<p class="font-medium text-gray-900">{report.plot.purpose}</p>
						</div>
						<div>
							<span class="text-gray-500">Savivaldybė</span>
							<p class="font-medium text-gray-900">{report.plot.municipality}</p>
						</div>
					</div>
				</div>
			{:else}
				<div class="rounded-lg border border-yellow-200 bg-yellow-50 p-4 text-sm text-yellow-700">
					Kadastro duomenys neprieinami šiam sklypui.
				</div>
			{/if}

			<!-- Listings -->
			{#if report.listings.length > 0}
				<div class="mt-6 rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
					<h2 class="text-lg font-semibold text-gray-900">
						Skelbimai
						<span class="text-sm font-normal text-gray-400">({report.listings.length})</span>
					</h2>
					<div class="mt-3 divide-y divide-gray-100">
						{#each report.listings as listing}
							<div class="flex items-center justify-between py-3">
								<div>
									<p class="text-sm font-medium text-gray-900">{listing.title}</p>
									<p class="text-xs text-gray-500">{listing.source} · {listing.address_raw}</p>
								</div>
								<div class="text-right">
									<p class="text-sm font-bold text-blue-600">{formatPrice(listing.price)}</p>
									{#if listing.area_sqm}
										<p class="text-xs text-gray-400">{listing.area_sqm} m²</p>
									{/if}
								</div>
							</div>
						{/each}
					</div>
				</div>
			{/if}

			<!-- Permits -->
			{#if report.permits.length > 0}
				<div class="mt-6 rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
					<h2 class="text-lg font-semibold text-gray-900">
						Statybos leidimai
						<span class="text-sm font-normal text-gray-400">({report.permits.length})</span>
					</h2>
					<div class="mt-3 divide-y divide-gray-100">
						{#each report.permits as permit}
							<div class="py-3">
								<div class="flex items-center gap-2">
									<span class="rounded-full px-2 py-0.5 text-xs font-medium
										{permit.status === 'issued' ? 'bg-green-100 text-green-700' : 'bg-yellow-100 text-yellow-700'}">
										{permit.status}
									</span>
									<span class="text-sm font-medium text-gray-900">{permit.permit_number}</span>
								</div>
								<p class="mt-1 text-xs text-gray-500">{permit.applicant_name} · {permit.building_purpose}</p>
								{#if permit.issued_at}
									<p class="text-xs text-gray-400">Išduotas: {permit.issued_at}</p>
								{/if}
							</div>
						{/each}
					</div>
				</div>
			{/if}

			<!-- Planning -->
			{#if report.planning.length > 0}
				<div class="mt-6 rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
					<h2 class="text-lg font-semibold text-gray-900">
						Teritorijų planavimas
						<span class="text-sm font-normal text-gray-400">({report.planning.length})</span>
					</h2>
					<div class="mt-3 divide-y divide-gray-100">
						{#each report.planning as plan}
							<div class="py-3">
								<p class="text-sm font-medium text-gray-900">{plan.title}</p>
								<div class="mt-1 flex flex-wrap gap-2">
									<span class="rounded bg-blue-50 px-2 py-0.5 text-xs text-blue-700">{plan.doc_type}</span>
									<span class="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-600">{plan.status}</span>
									{#if plan.max_floors}
										<span class="rounded bg-purple-50 px-2 py-0.5 text-xs text-purple-700">Max {plan.max_floors} aukštai</span>
									{/if}
									{#if plan.max_height_m}
										<span class="rounded bg-purple-50 px-2 py-0.5 text-xs text-purple-700">Max {plan.max_height_m}m</span>
									{/if}
								</div>
								{#if plan.allowed_uses.length > 0}
									<p class="mt-1 text-xs text-gray-500">Paskirtis: {plan.allowed_uses.join(', ')}</p>
								{/if}
								{#if plan.extraction_confidence !== null && plan.extraction_confidence < 0.6}
									<p class="mt-1 text-xs text-yellow-600">
										Tikrinkite originalų dokumentą (AI pasitikėjimas: {Math.round(plan.extraction_confidence * 100)}%)
									</p>
								{/if}
							</div>
						{/each}
					</div>
				</div>
			{/if}

			<!-- Heritage & Restrictions -->
			{#if report.heritage.length > 0 || report.restrictions.length > 0}
				<div class="mt-6 rounded-lg border border-red-100 bg-white p-6 shadow-sm">
					<h2 class="text-lg font-semibold text-gray-900">Apribojimai</h2>
					{#if report.heritage.length > 0}
						<div class="mt-3">
							<h3 class="text-xs font-semibold uppercase text-red-600">Kultūros paveldas</h3>
							{#each report.heritage as h}
								<div class="mt-2 rounded bg-red-50 p-2 text-sm text-red-800">
									{h.name} — {h.category} ({h.protection_level})
								</div>
							{/each}
						</div>
					{/if}
					{#if report.restrictions.length > 0}
						<div class="mt-3">
							<h3 class="text-xs font-semibold uppercase text-orange-600">Specialiosios sąlygos</h3>
							{#each report.restrictions as r}
								<div class="mt-2 rounded bg-orange-50 p-2 text-sm text-orange-800">
									{r.category}{r.description ? ` — ${r.description}` : ''}
								</div>
							{/each}
						</div>
					{/if}
				</div>
			{/if}

			<!-- Developers -->
			{#if report.developers.length > 0}
				<div class="mt-6 rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
					<h2 class="text-lg font-semibold text-gray-900">
						Vystytojai aplinkoje
						<span class="text-sm font-normal text-gray-400">({report.developers.length})</span>
					</h2>
					<div class="mt-3 divide-y divide-gray-100">
						{#each report.developers as dev}
							<div class="flex items-center justify-between py-3">
								<div>
									<p class="text-sm font-medium text-gray-900">{dev.name}</p>
									<p class="text-xs text-gray-500">{dev.company_code} · {dev.status}</p>
								</div>
								{#if dev.employee_count}
									<span class="text-xs text-gray-400">{dev.employee_count} darbuotojų</span>
								{/if}
							</div>
						{/each}
					</div>
				</div>
			{/if}

			<!-- Empty state -->
			{#if !report.plot && report.listings.length === 0 && report.permits.length === 0 && report.planning.length === 0}
				<div class="mt-6 rounded-lg border border-gray-200 bg-white p-12 text-center shadow-sm">
					<p class="text-sm text-gray-400">Duomenų apie šį sklypą nerasta.</p>
				</div>
			{/if}

			<!-- Data source citations -->
			<footer class="mt-8 rounded-lg border border-gray-200 bg-gray-50 px-6 py-4">
				<h3 class="text-xs font-semibold uppercase tracking-wide text-gray-400">Šaltiniai</h3>
				<div class="mt-2 space-y-1 text-xs text-gray-500">
					{#if report.plot}
						<p>
							<span class="font-medium text-gray-600">GeoPortal NTKR</span> — kadastro duomenys
						</p>
					{/if}
					{#if report.permits.length > 0}
						<p>
							<span class="font-medium text-gray-600">Infostatyba</span> — statybos leidimai
						</p>
					{/if}
					{#if report.planning.length > 0}
						<p>
							<span class="font-medium text-gray-600">TPDRIS</span> — teritorijų planavimo dokumentai
						</p>
					{/if}
					{#if report.heritage.length > 0}
						<p>
							<span class="font-medium text-gray-600">Kultūros vertybių registras</span> — paveldo zonos
						</p>
					{/if}
					{#if report.restrictions.length > 0}
						<p>
							<span class="font-medium text-gray-600">GeoPortal SŽNS</span> — specialiosios sąlygos
						</p>
					{/if}
					{#if report.developers.length > 0}
						<p>
							<span class="font-medium text-gray-600">JAR</span> — juridinių asmenų registras
						</p>
					{/if}
					{#if report.listings.length > 0}
						<p>
							<span class="font-medium text-gray-600">Aruodas, Domoplius</span> — NT skelbimu portalai
						</p>
					{/if}
				</div>
				<p class="mt-3 text-[10px] text-gray-400">
					Duomenys renkami automatiškai ir gali būti netikslūs. Visada patikrinkite pirminius šaltinius.
				</p>
			</footer>
		{/if}
	</main>
</div>
