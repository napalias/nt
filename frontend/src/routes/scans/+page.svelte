<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import {
		getScrapeAttention,
		getScrapeSummary,
		getScrapeRuns,
		getRunNewListings,
		type NewListing,
		type ScrapeAttention,
		type ScrapeRun,
		type SpiderSummary
	} from '$lib/api/client';

	type Badge = { label: string; cls: string };

	const NEUTRAL = 'bg-gray-100 text-gray-600 ring-gray-200';

	// Unknown values (a status the UI has not heard of yet) fall back to a neutral badge.
	const STATUS: Record<string, Badge> = {
		running: { label: 'Vykdoma', cls: 'bg-blue-50 text-blue-700 ring-blue-200' },
		ok: { label: 'Sėkminga', cls: 'bg-green-50 text-green-700 ring-green-200' },
		partial: { label: 'Iš dalies', cls: 'bg-amber-50 text-amber-800 ring-amber-200' },
		empty: { label: 'Nieko nerasta', cls: 'bg-amber-50 text-amber-800 ring-amber-200' },
		blocked: { label: 'Užblokuota', cls: 'bg-red-50 text-red-700 ring-red-200' },
		failed: { label: 'Nepavyko', cls: 'bg-red-50 text-red-700 ring-red-200' }
	};

	const STATE: Record<string, Badge> = {
		scheduled: { label: 'Pagal grafiką', cls: 'bg-green-50 text-green-700 ring-green-200' },
		paused: { label: 'Sustabdyta', cls: 'bg-amber-50 text-amber-800 ring-amber-200' },
		not_built: { label: 'Dar nesukurta', cls: NEUTRAL },
		needs_login: { label: 'Reikia prisijungimo', cls: 'bg-amber-50 text-amber-800 ring-amber-200' }
	};

	const PROBLEM: Record<string, string> = {
		needs_human: 'Reikia žmogaus',
		blocked: 'Užblokuota',
		failed: 'Nepavyko',
		empty_twice: 'Du tušti nuskaitymai iš eilės',
		partial: 'Iš dalies'
	};

	const BLOCK_KIND: Record<string, string> = {
		captcha: 'captcha',
		challenge: 'patikros puslapis',
		login: 'reikia prisijungti',
		robots: 'robots.txt draudimas',
		forbidden: '403 draudžia',
		rate_limited: 'per daug užklausų (429)'
	};

	const FETCH_MODE: Record<string, string> = {
		plain: 'Paprastas HTTP',
		curl_cffi: 'Naršyklės TLS (curl_cffi)',
		playwright: 'Naršyklė (Playwright)'
	};

	function badge(map: Record<string, Badge>, key: string): Badge {
		return map[key] ?? { label: key || '—', cls: NEUTRAL };
	}

	let summary = $state<SpiderSummary[]>([]);
	let attention = $state<ScrapeAttention[]>([]);
	let attentionError = $state(false);
	let runs = $state<ScrapeRun[]>([]);
	let loadingOverview = $state(true);
	let loadingRuns = $state(true);
	let loadError = $state('');
	let newListings = $state<Record<number, NewListing[] | 'loading' | 'error'>>({});

	// Filter state lives in the URL: /scans?spider=alio&run=12
	let filter = $derived($page.url.searchParams.get('spider') ?? '');
	let openRun = $derived.by(() => {
		const raw = Number($page.url.searchParams.get('run'));
		return Number.isInteger(raw) && raw > 0 ? raw : null;
	});

	let anyRunning = $derived(
		summary.some((s) => s.last_run?.status === 'running') ||
			runs.some((r) => r.status === 'running')
	);

	let labels = $derived(Object.fromEntries(summary.map((s) => [s.spider, s.label])));
	function labelOf(spider: string): string {
		return labels[spider] ?? spider;
	}

	function setQuery(changes: Record<string, string | null>) {
		const params = new URLSearchParams($page.url.searchParams);
		for (const [key, value] of Object.entries(changes)) {
			if (value) params.set(key, value);
			else params.delete(key);
		}
		const qs = params.toString();
		goto(qs ? `?${qs}` : $page.url.pathname, { replaceState: true, noScroll: true, keepFocus: true });
	}

	function setFilter(spider: string) {
		setQuery({ spider: filter === spider ? null : spider, run: null });
	}

	function toggle(run: ScrapeRun) {
		setQuery({ run: openRun === run.id ? null : String(run.id) });
	}

	async function loadOverview() {
		const [sum, att] = await Promise.allSettled([getScrapeSummary(), getScrapeAttention()]);
		if (sum.status === 'fulfilled') {
			summary = sum.value;
			loadError = '';
		} else {
			loadError =
				sum.reason instanceof Error ? sum.reason.message : 'Nepavyko užkrauti nuskaitymų';
		}
		if (att.status === 'fulfilled') {
			attention = att.value;
			attentionError = false;
		} else {
			attentionError = true;
		}
		loadingOverview = false;
	}

	// A slow answer for an older filter must not overwrite a newer one.
	let runsSeq = 0;
	async function loadRuns(spider: string) {
		const seq = ++runsSeq;
		try {
			const result = await getScrapeRuns(spider || undefined);
			if (seq !== runsSeq) return;
			runs = result;
			loadError = '';
		} catch (e) {
			if (seq !== runsSeq) return;
			loadError = e instanceof Error ? e.message : 'Nepavyko užkrauti nuskaitymų';
		} finally {
			if (seq === runsSeq) loadingRuns = false;
		}
	}

	async function ensureNewListings(runId: number) {
		if (newListings[runId]) return;
		newListings[runId] = 'loading';
		try {
			newListings[runId] = await getRunNewListings(runId);
		} catch {
			newListings[runId] = 'error';
		}
	}

	$effect(() => {
		const spider = filter;
		untrack(() => loadRuns(spider));
	});

	$effect(() => {
		const id = openRun;
		if (id === null) return;
		const run = runs.find((r) => r.id === id); // tracked: re-check once the runs have loaded
		if (run && run.items_new > 0) untrack(() => ensureNewListings(id));
	});

	onMount(() => {
		loadOverview();
		// Refresh while a scan is running; every minute otherwise.
		let ticks = 0;
		const timer = setInterval(() => {
			ticks += 1;
			if (anyRunning || ticks % 6 === 0) {
				loadOverview();
				loadRuns(filter);
			}
		}, 10_000);
		return () => clearInterval(timer);
	});

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
		const s = Math.max(0, Math.round((end - new Date(run.started_at).getTime()) / 1000));
		return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`;
	}

	// Registry schedule is a cron string "minute hour day month weekday".
	function scheduleText(cron: string): string {
		const [minute, hour, , , dow] = cron.split(' ');
		if (minute === undefined || hour === undefined) return cron;
		const time = `${hour.padStart(2, '0')}:${minute.padStart(2, '0')}`;
		return !dow || dow === '*' ? `kasdien ${time}` : `${time} (savaitės d.: ${dow})`;
	}

	function listingsOf(runId: number): NewListing[] {
		const v = newListings[runId];
		return Array.isArray(v) ? v : [];
	}

	function price(p: number | null): string {
		return p == null ? '' : `${Math.round(p).toLocaleString('lt-LT')} €`;
	}

	function reasons(run: ScrapeRun): [string, number][] {
		return Object.entries(run.drop_reasons).sort((a, b) => b[1] - a[1]);
	}

	function argList(run: ScrapeRun): [string, string][] {
		return Object.entries(run.args);
	}

	function blockText(run: ScrapeRun): string {
		return BLOCK_KIND[run.block_kind] ?? run.block_kind;
	}
</script>

<svelte:head>
	<title>Nuskaitymai — NT Paieška</title>
</svelte:head>

{#snippet stat(label: string, value: number, valueCls: string, hint?: string)}
	<div class="min-w-0">
		<div class="text-lg font-semibold tabular-nums {valueCls}">{value}</div>
		<div class="text-xs text-gray-500">{label}</div>
		{#if hint}<div class="text-[11px] leading-tight text-gray-400">{hint}</div>{/if}
	</div>
{/snippet}

<div class="min-h-screen bg-gray-50">
	<main class="mx-auto max-w-5xl px-3 py-6 sm:px-4 sm:py-8">
		<div class="mb-6 flex flex-wrap items-end justify-between gap-3">
			<div class="min-w-0">
				<h1 class="text-xl font-semibold text-gray-900">Nuskaitymai</h1>
				<p class="mt-1 text-sm text-gray-500">
					Kiekvienas portalas: kada nuskaitytas, kiek rasta, kiek naujų pridėta ir kiek atmesta.
					Automatiškai pagal grafiką; rankiniu būdu:
					<code class="rounded bg-gray-100 px-1">pnpm scrape</code>.
				</p>
			</div>
			{#if anyRunning}
				<span class="flex items-center gap-2 text-sm text-blue-700">
					<span class="h-2 w-2 animate-pulse rounded-full bg-blue-500"></span> Vyksta nuskaitymas
				</span>
			{/if}
		</div>

		{#if loadError}
			<div
				class="mb-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"
				role="alert"
			>
				{loadError}
			</div>
		{/if}

		<!-- Attention banner: sources that are blocked or need a human -->
		{#if attention.length > 0}
			<section
				class="mb-6 rounded-lg border border-red-200 bg-red-50 p-4"
				aria-label="Reikia dėmesio"
			>
				<h2 class="flex items-center gap-2 font-semibold text-red-800">
					<span aria-hidden="true">⚠</span>
					Reikia dėmesio ({attention.length})
				</h2>
				<ul class="mt-3 space-y-3">
					{#each attention as a (a.spider)}
						{@const isError = a.severity === 'error'}
						<li
							class="rounded-md border bg-white p-3 {isError
								? 'border-red-200'
								: 'border-amber-200'}"
						>
							<div class="flex flex-wrap items-center gap-x-3 gap-y-1">
								<span class="font-medium text-gray-900">{a.label}</span>
								<span
									class="rounded-full px-2 py-0.5 text-xs font-medium ring-1 {isError
										? 'bg-red-50 text-red-700 ring-red-200'
										: 'bg-amber-50 text-amber-800 ring-amber-200'}"
								>
									{PROBLEM[a.problem] ?? a.problem}
								</span>
								{#if a.run.block_kind}
									<span class="text-xs text-gray-500">({blockText(a.run)})</span>
								{/if}
							</div>
							<p class="mt-2 text-sm text-gray-800">
								<span class="font-medium">Ką daryti:</span>
								{a.what_to_do}
							</p>
							<div class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-500">
								<span>Paskutinis nuskaitymas: {ago(a.run.started_at)}</span>
								{#if a.notified_at}
									<span>Laiškas išsiųstas: {ago(a.notified_at)}</span>
								{/if}
								<button
									type="button"
									class="text-blue-700 hover:underline"
									onclick={() => setQuery({ spider: a.spider, run: String(a.run.id) })}
								>
									Rodyti nuskaitymą
								</button>
							</div>
						</li>
					{/each}
				</ul>
			</section>
		{:else if attentionError}
			<p class="mb-6 text-sm text-gray-500">Nepavyko užkrauti dėmesio sąrašo.</p>
		{:else if !loadingOverview}
			<p class="mb-6 flex items-center gap-2 text-sm text-green-700">
				<span class="h-2 w-2 rounded-full bg-green-500"></span> Nėra portalų, kuriems reikėtų
				dėmesio.
			</p>
		{/if}

		<!-- One card per registry source -->
		{#if loadingOverview}
			<p class="text-sm text-gray-500">Kraunama…</p>
		{:else}
			<div class="grid grid-cols-1 gap-3 md:grid-cols-2">
				{#each summary as s (s.spider)}
					{@const run = s.last_run}
					{@const st = run ? badge(STATUS, run.status) : null}
					{@const state = badge(STATE, s.state)}
					{@const live = s.state === 'scheduled' || s.state === 'paused' || run !== null}
					<article
						class="flex min-w-0 flex-col rounded-lg border bg-white p-4 shadow-sm
							{filter === s.spider ? 'border-blue-400 ring-1 ring-blue-200' : 'border-gray-200'}"
					>
						<header class="flex flex-wrap items-start justify-between gap-2">
							<div class="min-w-0">
								<h3 class="font-medium text-gray-900">{s.label}</h3>
								<div class="mt-1 flex flex-wrap items-center gap-1.5 text-xs">
									<span class="rounded-full px-2 py-0.5 ring-1 {state.cls}">{state.label}</span>
									<span class="rounded-full bg-gray-100 px-2 py-0.5 text-gray-600">
										{FETCH_MODE[s.fetch_mode] ?? s.fetch_mode}
									</span>
								</div>
							</div>
							{#if st}
								<span class="rounded-full px-2 py-0.5 text-xs font-medium ring-1 {st.cls}">
									{st.label}
								</span>
							{:else}
								<span class="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-500">
									Dar nevykdyta
								</span>
							{/if}
						</header>

						{#if s.note && (!run || s.state === 'needs_login' || s.state === 'not_built' || s.state === 'paused')}
							<p class="mt-3 rounded-md bg-gray-50 px-3 py-2 text-xs text-gray-600">{s.note}</p>
						{/if}

						{#if run}
							<div class="mt-3 text-xs text-gray-500">
								<span class="font-medium text-gray-700" title={new Date(run.started_at).toLocaleString('lt-LT')}>
									{ago(run.started_at)}
								</span>
								· {when(run.started_at)} · {took(run)}
								{#if run.trigger === 'manual'}· rankinis{/if}
								{#if run.status !== 'ok'}
									<div>Paskutinis sėkmingas: {ago(s.last_ok_at)}</div>
								{/if}
							</div>

							{#if run.status === 'blocked' || run.block_kind}
								<p class="mt-2 text-xs text-red-700">
									Portalas blokuoja{run.block_kind ? `: ${blockText(run)}` : ''}.
								</p>
							{/if}

							<div class="mt-3 grid grid-cols-3 gap-x-3 gap-y-3 sm:grid-cols-6">
								{@render stat('nuorodų', run.links_seen, 'text-gray-900', 'matyta sąrašuose')}
								{@render stat('rasta', run.items_found, 'text-gray-900', 'skelbimų')}
								{@render stat('nauji', run.items_new, run.items_new ? 'text-green-700' : 'text-gray-400', 'pridėta į bazę')}
								{@render stat('atnaujinti', run.items_updated, run.items_updated ? 'text-gray-900' : 'text-gray-400')}
								{@render stat('nepakitę', run.items_unchanged, 'text-gray-500')}
								{@render stat('atmesti', run.items_dropped, run.items_dropped ? 'text-amber-700' : 'text-gray-400')}
							</div>

							{#if run.items_dropped > 0}
								<div class="mt-3">
									<div class="text-xs font-medium uppercase text-gray-500">Kodėl atmesti</div>
									{#if reasons(run).length}
										<ul class="mt-1 space-y-0.5 text-sm text-gray-700">
											{#each reasons(run).slice(0, 3) as [reason, count] (reason)}
												<li class="break-words">
													<span class="font-medium tabular-nums">{count}×</span>
													{reason}
												</li>
											{/each}
										</ul>
										{#if reasons(run).length > 3}
											<details class="mt-1 text-sm text-gray-700">
												<summary class="cursor-pointer text-xs text-blue-700">
													dar {reasons(run).length - 3}
												</summary>
												<ul class="mt-1 space-y-0.5">
													{#each reasons(run).slice(3) as [reason, count] (reason)}
														<li class="break-words">
															<span class="font-medium tabular-nums">{count}×</span>
															{reason}
														</li>
													{/each}
												</ul>
											</details>
										{/if}
									{:else}
										<p class="mt-1 text-sm text-gray-500">Priežastys nenurodytos.</p>
									{/if}
								</div>
							{/if}

							{#if run.errors}
								<p class="mt-2 text-xs text-red-700">Klaidų žurnale: {run.errors}</p>
							{/if}
						{/if}

						<!-- What was scanned -->
						{#if live}
							<div class="mt-3 border-t border-gray-100 pt-3 text-xs text-gray-500">
								<div class="font-medium uppercase">Ką nuskaito</div>
								{#if run?.start_url}
									<a
										href={run.start_url}
										target="_blank"
										rel="noopener"
										class="mt-1 block break-all text-blue-700 hover:underline"
									>
										{run.start_url}
									</a>
								{:else if s.start_urls.length}
									<a
										href={s.start_urls[0]}
										target="_blank"
										rel="noopener"
										class="mt-1 block break-all text-blue-700 hover:underline"
									>
										{s.start_urls[0]}
									</a>
								{/if}
								{#if run && argList(run).length}
									<div class="mt-1.5 flex flex-wrap gap-1">
										{#each argList(run) as [key, value] (key)}
											<span class="rounded bg-gray-100 px-1.5 py-0.5 font-mono text-[11px] text-gray-700 break-all">
												{key}={value}
											</span>
										{/each}
									</div>
								{/if}
								{#if s.start_urls.length > 1}
									<details class="mt-1.5">
										<summary class="cursor-pointer text-blue-700">
											visi startiniai adresai ({s.start_urls.length})
										</summary>
										<ul class="mt-1 space-y-1">
											{#each s.start_urls as url (url)}
												<li class="break-all">{url}</li>
											{/each}
										</ul>
									</details>
								{/if}
								{#if s.state === 'scheduled'}
									<div class="mt-1.5">Grafikas: {scheduleText(s.schedule)}</div>
								{/if}
							</div>
						{/if}

						<footer class="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-gray-500">
							<span>
								{#if s.listings_total !== null}
									Bazėje {s.listings_total} skelb. · per 7 d. nauji: {s.new_7d}
								{:else}
									Per 7 d. nuskaitymų: {s.runs_7d}
								{/if}
							</span>
							<button
								type="button"
								onclick={() => setFilter(s.spider)}
								class="rounded-md px-2 py-1 text-sm text-blue-700 hover:bg-blue-50"
								aria-pressed={filter === s.spider}
							>
								{filter === s.spider ? 'Rodyti visus' : 'Istorija'}
							</button>
						</footer>
					</article>
				{/each}
			</div>
		{/if}

		<!-- Run history -->
		<div class="mt-8 rounded-lg border border-gray-200 bg-white shadow-sm">
			<div class="flex items-center justify-between gap-2 border-b border-gray-100 px-4 py-3">
				<h2 class="font-semibold text-gray-900">
					Istorija {filter ? `— ${labelOf(filter)}` : ''}
				</h2>
				{#if filter}
					<button class="text-sm text-blue-700 hover:underline" onclick={() => setFilter(filter)}>
						Rodyti visus
					</button>
				{/if}
			</div>

			{#if loadingRuns}
				<p class="px-4 py-6 text-sm text-gray-500">Kraunama…</p>
			{:else if runs.length === 0}
				<p class="px-4 py-6 text-sm text-gray-500">
					Dar nėra įrašų. Paleiskite <code class="rounded bg-gray-100 px-1">pnpm scrape</code>.
				</p>
			{:else}
				<div class="overflow-x-auto">
					<table class="w-full min-w-[34rem] text-sm">
						<thead class="text-left text-xs uppercase tracking-wide text-gray-500">
							<tr class="border-b border-gray-100">
								<th class="px-3 py-2 font-medium sm:px-4">Pradėta</th>
								<th class="px-3 py-2 font-medium sm:px-4">Portalas</th>
								<th class="px-3 py-2 font-medium sm:px-4">Būsena</th>
								<th class="hidden px-4 py-2 text-right font-medium md:table-cell">Puslapiai</th>
								<th class="px-3 py-2 text-right font-medium sm:px-4">Rasta</th>
								<th class="px-3 py-2 text-right font-medium sm:px-4">Nauji</th>
								<th class="hidden px-4 py-2 text-right font-medium sm:table-cell">Atnaujinti</th>
								<th class="px-3 py-2 text-right font-medium sm:px-4">Atmesti</th>
								<th class="hidden px-4 py-2 text-right font-medium md:table-cell">Trukmė</th>
							</tr>
						</thead>
						<tbody>
							{#each runs as run (run.id)}
								{@const rs = badge(STATUS, run.status)}
								<tr
									class="cursor-pointer border-b border-gray-50 hover:bg-gray-50 {openRun === run.id
										? 'bg-gray-50'
										: ''}"
									onclick={() => toggle(run)}
									onkeydown={(e) => {
										if (e.key === 'Enter' || e.key === ' ') {
											e.preventDefault();
											toggle(run);
										}
									}}
									tabindex="0"
									aria-expanded={openRun === run.id}
								>
									<td class="px-3 py-2 whitespace-nowrap text-gray-600 sm:px-4">{when(run.started_at)}</td>
									<td class="px-3 py-2 font-medium text-gray-900 sm:px-4">{labelOf(run.spider)}</td>
									<td class="px-3 py-2 sm:px-4">
										<span class="rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap ring-1 {rs.cls}">
											{rs.label}
										</span>
									</td>
									<td class="hidden px-4 py-2 text-right tabular-nums md:table-cell">{run.pages_crawled}</td>
									<td class="px-3 py-2 text-right tabular-nums sm:px-4">{run.items_found}</td>
									<td class="px-3 py-2 text-right font-medium tabular-nums text-green-700 sm:px-4">{run.items_new}</td>
									<td class="hidden px-4 py-2 text-right tabular-nums sm:table-cell">{run.items_updated}</td>
									<td
										class="px-3 py-2 text-right tabular-nums sm:px-4 {run.items_dropped
											? 'text-amber-700'
											: 'text-gray-400'}"
									>
										{run.items_dropped}
									</td>
									<td class="hidden px-4 py-2 text-right whitespace-nowrap text-gray-500 md:table-cell">{took(run)}</td>
								</tr>
								{#if openRun === run.id}
									<tr class="border-b border-gray-100 bg-gray-50">
										<td colspan="9" class="px-3 pt-1 pb-4 sm:px-4">
											<div class="mb-3 text-xs text-gray-500">
												Nuorodų: {run.links_seen} · išsaugota: {run.items_saved} · nepakitę: {run.items_unchanged}
												· puslapių: {run.pages_crawled} · trukmė: {took(run)}
												· {run.trigger === 'manual' ? 'rankinis' : 'pagal grafiką'}
												{#if run.finish_reason}· pabaiga: {run.finish_reason}{/if}
											</div>
											{#if run.start_url}
												<div class="mb-3 text-xs text-gray-500">
													<span class="font-medium uppercase">Nuskaityta:</span>
													<a href={run.start_url} target="_blank" rel="noopener" class="break-all text-blue-700 hover:underline">
														{run.start_url}
													</a>
													{#each argList(run) as [key, value] (key)}
														<span class="ml-1 rounded bg-white px-1.5 py-0.5 font-mono text-[11px] break-all ring-1 ring-gray-200">
															{key}={value}
														</span>
													{/each}
												</div>
											{/if}
											{#if Object.keys(run.drop_reasons).length}
												<div class="mb-3">
													<div class="mb-1 text-xs font-medium uppercase text-gray-500">Kodėl atmesti</div>
													<ul class="text-sm text-gray-700">
														{#each reasons(run) as [reason, count] (reason)}
															<li class="break-words">
																<span class="font-medium tabular-nums">{count}×</span>
																{reason}
															</li>
														{/each}
													</ul>
												</div>
											{/if}
											{#if run.errors}
												<p class="mb-3 text-sm text-red-700">
													Klaidų žurnale: {run.errors} (žr. <code>pnpm logs scraper_worker</code>)
												</p>
											{/if}
											{#if run.status === 'blocked'}
												<p class="mb-3 text-sm text-red-700">
													Portalas atmetė užklausas{run.block_kind ? ` (${blockText(run)})` : ''}.
													{#if run.needs_human}Reikia žmogaus.{/if}
												</p>
											{/if}
											<div class="text-xs font-medium uppercase text-gray-500">Nauji skelbimai</div>
											{#if run.items_new === 0}
												<p class="text-sm text-gray-500">Šis nuskaitymas naujų skelbimų neatnešė.</p>
											{:else if newListings[run.id] === 'loading' || newListings[run.id] === undefined}
												<p class="text-sm text-gray-500">Kraunama…</p>
											{:else if newListings[run.id] === 'error'}
												<p class="text-sm text-red-700">Nepavyko užkrauti.</p>
											{:else}
												<ul class="mt-1 divide-y divide-gray-200 text-sm">
													{#each listingsOf(run.id) as l (l.id)}
														<li class="flex items-baseline justify-between gap-4 py-1.5">
															<a href={l.source_url} target="_blank" rel="noopener" class="min-w-0 break-words text-blue-700 hover:underline">
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
				</div>
			{/if}
		</div>
	</main>
</div>
