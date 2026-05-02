<script lang="ts">
	import { geocode, type GeocodeResult } from '$lib/api/client';

	let {
		onselect
	}: {
		onselect: (result: GeocodeResult) => void;
	} = $props();

	let query = $state('');
	let results = $state<GeocodeResult[]>([]);
	let loading = $state(false);
	let showDropdown = $state(false);
	let debounceTimer: ReturnType<typeof setTimeout>;

	function handleInput() {
		clearTimeout(debounceTimer);
		if (query.length < 2) {
			results = [];
			showDropdown = false;
			return;
		}
		debounceTimer = setTimeout(async () => {
			loading = true;
			try {
				results = await geocode(query);
				showDropdown = results.length > 0;
			} catch {
				results = [];
			} finally {
				loading = false;
			}
		}, 300);
	}

	function select(result: GeocodeResult) {
		query = result.display_name.split(',')[0];
		showDropdown = false;
		onselect(result);
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') {
			showDropdown = false;
		}
	}
</script>

<div class="relative">
	<div class="flex items-center gap-2">
		<div class="relative flex-1">
			<input
				type="text"
				bind:value={query}
				oninput={handleInput}
				onkeydown={handleKeydown}
				onfocus={() => results.length > 0 && (showDropdown = true)}
				placeholder="Ieškoti vietovę... (pvz. Kretinga)"
				class="w-full rounded-lg border border-gray-300 bg-white px-4 py-2.5 pl-10 text-sm shadow-sm transition focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
			/>
			<svg
				class="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400"
				fill="none"
				stroke="currentColor"
				viewBox="0 0 24 24"
			>
				<path
					stroke-linecap="round"
					stroke-linejoin="round"
					stroke-width="2"
					d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
				/>
			</svg>
			{#if loading}
				<div class="absolute right-3 top-1/2 -translate-y-1/2">
					<div
						class="h-4 w-4 animate-spin rounded-full border-2 border-blue-500 border-t-transparent"
					></div>
				</div>
			{/if}
		</div>
	</div>

	{#if showDropdown}
		<ul
			class="absolute z-50 mt-1 w-full rounded-lg border border-gray-200 bg-white py-1 shadow-lg"
		>
			{#each results as result}
				<li>
					<button
						type="button"
						onclick={() => select(result)}
						class="w-full px-4 py-2 text-left text-sm hover:bg-blue-50 focus:bg-blue-50 focus:outline-none"
					>
						{result.display_name}
					</button>
				</li>
			{/each}
		</ul>
	{/if}
</div>
