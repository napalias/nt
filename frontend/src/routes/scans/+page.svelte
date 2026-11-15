<script lang="ts">
	import { onMount } from 'svelte';
	import {
		getScrapeSummary,
		getScrapeRuns,
		getRunNewListings,
		type ScrapeRun,
		type ScrapeStatus,
		type SpiderSummary,
		type NewListing
	} from '$lib/api/client';

	const SITE_LABELS: Record<string, string> = {
		domoplius: 'Domoplius',
		skelbiu: 'Skelbiu',
		aruodas: 'Aruodas',
		alio: 'Alio',
		infostatyba: 'Infostatyba (leidimai)',
		tpdris: 'TPDRIS (planai)'
	};

	const STATUS: Record<ScrapeStatus, { label: string; cls: string }> = {
		running: { label: 'Vykdoma', cls: 'bg-blue-50 text-blue-700 ring-blue-200' },
		ok: { label: 'Sėkminga', cls: 'bg-green-50 text-green-700 ring-green-200' },
		empty: { label: 'Nieko neišsaugota', cls: 'bg-amber-50 text-amber-800 ring-amber-200' },
		blocked: { label: 'Užblokuota', cls: 'bg-red-50 text-red-700 ring-red-200' },
		failed: { label: 'Nepavyko', cls: 'bg-red-50 text-red-700 ring-red-200' }
	};

	let summary = $state<SpiderSummary[]>([]);
	let runs = $state<ScrapeRun[]>([]);
	let filter = $state('');
	let loading = $state(true);
	let loadError = $state('');
	let openRun = $state<number | null>(null);
	let newListings = $state<Record<number, NewListing[] | 'loading' | 'error'>>({});

	let anyRunning = $derived(runs.some((r) => r.status === 'running'));

	async function load() {
		loadError = '';
		try {
			[summary, runs] = await Promise.all([getScrapeSummary(), getScrapeRuns(filter || undefined)]);
		} catch (e) {
			loadError = e instanceof Error ? e.message : 'Nepavyko užkrauti nuskaitymų';
		} finally {
			loading = false;
		}
	}

	async function toggle(run: ScrapeRun) {
		openRun = openRun === run.id ? null : run.id;
		if (openRun === null || newListings[run.id] || run.items_new === 0) return;
		newListings[run.id] = 'loading';
		try {
			newListings[run.id] = await getRunNewListings(run.id);
		} catch {
			newListings[run.id] = 'error';
		}
	}

	function setFilter(spider: string) {
		filter = filter === spider ? '' : spider;
		openRun = null;
		load();
	}

	function ago(iso: string | null): string {
		if (!iso) return '—';
		const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
		if (min < 1) return 'ką tik';
		if (min < 60) return `prieš ${min} min.`;
		const h = Math.round(min / 60);
		if (h < 48) return `prieš ${h} val.`;
		return `prieš ${Math.round(h / 24)} d.`;
	}

	function when(iso: string): string {
		return new Date(iso).toLocaleString('lt-LT', {
			month: '2-digit',
			day: '2-digit',
			hour: '2-digit',
			minute: '2-digit'
		});
	}

	function took(run: ScrapeRun): string {
		const end = run.finished_at ? new Date(run.finished_at).getTime() : Date.now();
		const s = Math.round((end - new Date(run.started_at).getTime()) / 1000);
		return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`;
	}

	function listingsOf(runId: number): NewListing[] {
		const v = newListings[runId];
		return Array.isArray(v) ? v : [];
	}

	function price(p: number | null): string {
		return p == null ? '' : `${Math.round(p).toLocaleString('lt-LT')} €`;
	}

	onMount(() => {
		load();
		// Refresh while a scan is running; every minute otherwise.
		let ticks = 0;
		const timer = setInterval(() => {
			ticks += 1;
			if (anyRunning || ticks % 6 === 0) load();
		}, 10_000);
		return () => clearInterval(timer);
	});
</script>

<svelte:head>
	<title>Nuskaitymai — NT Paieška</title>
</svelte:head>

<div class="min-h-screen bg-gray-50">
	<main class="mx-auto max-w-5xl px-4 py-8">
		<div class="mb-6 flex items-end justify-between gap-4">
			<div>
				<h1 class="text-xl font-semibold text-gray-900">Nuskaitymai</h1>
				<p class="mt-1 text-sm text-gray-500">
					Kurie portalai nuskaityti, kiek rasta ir kiek naujų skelbimų atsirado. Automatiškai kasdien;
					rankiniu būdu: <code class="rounded bg-gray-100 px-1">pnpm scrape</code>.
				</p>
			</div>
			{#if anyRunning}
				<span class="flex items-center gap-2 text-sm text-blue-700">
					<span class="h-2 w-2 animate-pulse rounded-full bg-blue-500"></span> Vyksta nuskaitymas
				</span>
			{/if}
		</div>

		{#if loadError}
			<div class="mb-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
				{loadError}
			</div>
		{/if}

		<!-- Per-site summary -->
		<div class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
			{#each summary as s (s.spider)}
				{@const st = s.last_run ? STATUS[s.last_run.status] : null}
				<button
					type="button"
					onclick={() => setFilter(s.spider)}
					class="flex flex-col justify-start rounded-lg border bg-white p-4 text-left shadow-sm transition hover:border-blue-300
						{filter === s.spider ? 'border-blue-400 ring-1 ring-blue-200' : 'border-gray-200'}"
				>
					<div class="flex items-start justify-between gap-2">
						<span class="font-medium text-gray-900">{SITE_LABELS[s.spider] ?? s.spider}</span>
						{#if st}
							<span class="rounded-full px-2 py-0.5 text-xs font-medium ring-1 {st.cls}">{st.label}</span>
						{:else}
							<span class="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-500">Dar nevykdyta</span>
						{/if}
					</div>
					{#if s.last_run}
						<div class="mt-3 flex gap-5 text-sm">
							<div>
								<div class="text-lg font-semibold text-gray-900">{s.last_run.items_found}</div>
								<div class="text-xs text-gray-500">rasta</div>
							</div>
							<div>
								<div class="text-lg font-semibold text-green-700">{s.last_run.items_new}</div>
								<div class="text-xs text-gray-500">nauji</div>
							</div>
							<div>
								<div class="text-lg font-semibold {s.last_run.items_dropped ? 'text-amber-700' : 'text-gray-400'}">
									{s.last_run.items_dropped}
								</div>
								<div class="text-xs text-gray-500">atmesti</div>
							</div>
						</div>
						<div class="mt-3 text-xs text-gray-500">
							Paskutinis: {ago(s.last_run.started_at)}
							{#if s.last_run.status !== 'ok'}
								· sėkmingas: {ago(s.last_ok_at)}
							{/if}
						</div>
					{/if}
					{#if s.listings_total !== null}
						<div class="mt-1 text-xs text-gray-500">
							Bazėje {s.listings_total} skelb. · per 7 d. nauji: {s.new_7d}
						</div>
					{/if}
				</button>
			{/each}
		</div>

		<!-- Run history -->
		<div class="mt-8 rounded-lg border border-gray-200 bg-white shadow-sm">
			<div class="flex items-center justify-between border-b border-gray-100 px-4 py-3">
				<h2 class="font-semibold text-gray-900">
					Istorija {filter ? `— ${SITE_LABELS[filter] ?? filter}` : ''}
				</h2>
				{#if filter}
					<button class="text-sm text-blue-700 hover:underline" onclick={() => setFilter(filter)}>
						Rodyti visus
					</button>
				{/if}
			</div>

			{#if loading}
				<p class="px-4 py-6 text-sm text-gray-500">Kraunama…</p>
			{:else if runs.length === 0}
				<p class="px-4 py-6 text-sm text-gray-500">
					Dar nėra įrašų. Paleiskite <code class="rounded bg-gray-100 px-1">pnpm scrape</code>.
				</p>
			{:else}
				<table class="w-full text-sm">
					<thead class="text-left text-xs uppercase tracking-wide text-gray-500">
						<tr class="border-b border-gray-100">
							<th class="px-4 py-2 font-medium">Pradėta</th>
							<th class="px-4 py-2 font-medium">Portalas</th>
							<th class="px-4 py-2 font-medium">Būsena</th>
							<th class="px-4 py-2 text-right font-medium">Puslapiai</th>
							<th class="px-4 py-2 text-right font-medium">Rasta</th>
							<th class="px-4 py-2 text-right font-medium">Nauji</th>
							<th class="px-4 py-2 text-right font-medium">Atnaujinti</th>
							<th class="px-4 py-2 text-right font-medium">Atmesti</th>
							<th class="px-4 py-2 text-right font-medium">Trukmė</th>
						</tr>
					</thead>
					<tbody>
						{#each runs as run (run.id)}
							<tr
								class="cursor-pointer border-b border-gray-50 hover:bg-gray-50 {openRun === run.id ? 'bg-gray-50' : ''}"
								onclick={() => toggle(run)}
							>
								<td class="px-4 py-2 whitespace-nowrap text-gray-600">{when(run.started_at)}</td>
								<td class="px-4 py-2 font-medium text-gray-900">{SITE_LABELS[run.spider] ?? run.spider}</td>
								<td class="px-4 py-2">
									<span class="rounded-full px-2 py-0.5 text-xs font-medium ring-1 {STATUS[run.status].cls}">
										{STATUS[run.status].label}
									</span>
								</td>
								<td class="px-4 py-2 text-right tabular-nums">{run.pages_crawled}</td>
								<td class="px-4 py-2 text-right tabular-nums">{run.items_found}</td>
								<td class="px-4 py-2 text-right font-medium tabular-nums text-green-700">{run.items_new}</td>
								<td class="px-4 py-2 text-right tabular-nums">{run.items_updated}</td>
								<td class="px-4 py-2 text-right tabular-nums {run.items_dropped ? 'text-amber-700' : 'text-gray-400'}">
									{run.items_dropped}
								</td>
								<td class="px-4 py-2 text-right whitespace-nowrap text-gray-500">{took(run)}</td>
							</tr>
							{#if openRun === run.id}
								<tr class="border-b border-gray-100 bg-gray-50">
									<td colspan="9" class="px-4 pb-4 pt-1">
										{#if Object.keys(run.drop_reasons).length}
											<div class="mb-3">
												<div class="mb-1 text-xs font-medium uppercase text-gray-500">Kodėl atmesti</div>
												<ul class="text-sm text-gray-700">
													{#each Object.entries(run.drop_reasons) as [reason, count]}
														<li><span class="tabular-nums font-medium">{count}×</span> {reason}</li>
													{/each}
												</ul>
											</div>
										{/if}
										{#if run.errors}
											<p class="mb-3 text-sm text-red-700">Klaidų žurnale: {run.errors} (žr. <code>pnpm logs scraper_worker</code>)</p>
										{/if}
										{#if run.status === 'blocked'}
											<p class="mb-3 text-sm text-red-700">Portalas atmetė užklausas (403/429). Reikia apeiti botų apsaugą.</p>
										{/if}
										<div class="text-xs font-medium uppercase text-gray-500">Nauji skelbimai</div>
										{#if run.items_new === 0}
											<p class="text-sm text-gray-500">Šis nuskaitymas naujų skelbimų neatnešė.</p>
										{:else if newListings[run.id] === 'loading'}
											<p class="text-sm text-gray-500">Kraunama…</p>
										{:else if newListings[run.id] === 'error'}
											<p class="text-sm text-red-700">Nepavyko užkrauti.</p>
										{:else if Array.isArray(newListings[run.id])}
											<ul class="mt-1 divide-y divide-gray-200 text-sm">
												{#each listingsOf(run.id) as l (l.id)}
													<li class="flex items-baseline justify-between gap-4 py-1.5">
														<a href={l.source_url} target="_blank" rel="noopener" class="text-blue-700 hover:underline">
															{l.title}
														</a>
														<span class="whitespace-nowrap text-gray-600">{price(l.price)}</span>
													</li>
												{/each}
											</ul>
										{/if}
									</td>
								</tr>
							{/if}
						{/each}
					</tbody>
				</table>
			{/if}
		</div>
	</main>
</div>
