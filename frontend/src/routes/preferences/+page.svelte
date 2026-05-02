<script lang="ts">
	import { onMount } from 'svelte';
	import { getPreferences, deletePreference, type PreferenceResult } from '$lib/api/client';

	let preferences = $state<PreferenceResult[]>([]);
	let loading = $state(true);
	let loadError = $state('');
	let removeError = $state('');

	async function load() {
		loading = true;
		loadError = '';
		try {
			preferences = await getPreferences();
		} catch (e) {
			loadError = e instanceof Error ? e.message : 'Nepavyko uzkrauti prioritetu';
			preferences = [];
		} finally {
			loading = false;
		}
	}

	async function remove(id: number) {
		removeError = '';
		try {
			await deletePreference(id);
			preferences = preferences.filter((p) => p.id !== id);
		} catch {
			removeError = 'Nepavyko pasalinti prioriteto. Bandykite dar karta.';
		}
	}

	onMount(load);
</script>

<svelte:head>
	<title>Išmokti prioritetai — NT Paieška</title>
</svelte:head>

<div class="min-h-screen bg-gray-50">
	<main class="mx-auto max-w-3xl px-4 py-8">
		<div class="rounded-lg border border-gray-200 bg-white shadow-sm">
			<div class="border-b border-gray-100 px-6 py-4">
				<h2 class="text-lg font-semibold text-gray-900">AI išmokti prioritetai</h2>
				<p class="mt-1 text-sm text-gray-500">
					Šie šablonai buvo išgauti iš jūsų atsiliepimų. Jie naudojami vertinant naujus
					skelbimus. Galite pašalinti nebeaktualius.
				</p>
			</div>

			{#if loadError}
				<div class="px-6 py-4">
					<div class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
						{loadError}
					</div>
				</div>
			{:else if removeError}
				<div class="px-6 py-4">
					<div class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
						{removeError}
					</div>
				</div>
			{/if}

			{#if loading}
				<div class="flex items-center justify-center py-12">
					<div
						class="h-6 w-6 animate-spin rounded-full border-2 border-blue-500 border-t-transparent"
					></div>
				</div>
			{:else if preferences.length === 0}
				<div class="px-6 py-12 text-center text-sm text-gray-400">
					Dar nėra išmoktų prioritetų. Vertinkite skelbimus paieškoje — sistema mokysis iš
					jūsų atsiliepimų.
				</div>
			{:else}
				<ul class="divide-y divide-gray-100">
					{#each preferences as pref (pref.id)}
						<li class="flex items-center gap-4 px-6 py-4">
							<span
								class="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full text-sm {pref.preference_type ===
								'like'
									? 'bg-green-100 text-green-600'
									: 'bg-red-100 text-red-600'}"
							>
								{pref.preference_type === 'like' ? '👍' : '👎'}
							</span>
							<div class="min-w-0 flex-1">
								<p class="text-sm text-gray-900">{pref.pattern}</p>
								<p class="text-xs text-gray-400">
									Svoris: {pref.weight} · {pref.preference_type === 'like' ? 'Prioritizuoti' : 'Vengti'}
								</p>
							</div>
							<button
								onclick={() => remove(pref.id)}
								class="flex-shrink-0 rounded px-3 py-1 text-xs text-gray-400 transition hover:bg-red-50 hover:text-red-600"
							>
								Pašalinti
							</button>
						</li>
					{/each}
				</ul>
			{/if}
		</div>

		<div class="mt-6 rounded-lg border border-blue-100 bg-blue-50 px-6 py-4">
			<h3 class="text-sm font-medium text-blue-800">Kaip tai veikia?</h3>
			<p class="mt-1 text-sm text-blue-700">
				Kai vertinate skelbimą (patinka / nepatinka) ir nurodote priežastį, AI ištraukia
				bendrus šablonus. Šie šablonai pridedami prie visų būsimų vertinimų — sistema
				mokosi ko ieškote ir ko vengiate.
			</p>
		</div>
	</main>
</div>
