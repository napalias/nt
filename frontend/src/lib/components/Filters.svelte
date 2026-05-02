<script lang="ts">
	let {
		minPrice = $bindable<number | undefined>(undefined),
		maxPrice = $bindable<number | undefined>(undefined),
		rooms = $bindable<number | undefined>(undefined),
		propertyType = $bindable<string | undefined>(undefined),
		isNewConstruction = $bindable<boolean | undefined>(undefined),
		onapply
	}: {
		minPrice?: number;
		maxPrice?: number;
		rooms?: number;
		propertyType?: string;
		isNewConstruction?: boolean;
		onapply: () => void;
	} = $props();

	const propertyTypes = [
		{ value: '', label: 'Visi tipai' },
		{ value: 'house', label: 'Namai' },
		{ value: 'flat', label: 'Butai' },
		{ value: 'plot', label: 'Sklypai' },
		{ value: 'cottage', label: 'Sodybos' },
		{ value: 'commercial', label: 'Komerciniai' }
	];

	function reset() {
		minPrice = undefined;
		maxPrice = undefined;
		rooms = undefined;
		propertyType = undefined;
		isNewConstruction = undefined;
		onapply();
	}
</script>

<div class="space-y-4">
	<h3 class="text-sm font-semibold uppercase tracking-wide text-gray-500">Filtrai</h3>

	<div>
		<label for="filter-type" class="mb-1 block text-xs font-medium text-gray-600">Turto tipas</label>
		<select
			id="filter-type"
			bind:value={propertyType}
			onchange={onapply}
			class="w-full rounded border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
		>
			{#each propertyTypes as pt}
				<option value={pt.value}>{pt.label}</option>
			{/each}
		</select>
	</div>

	<div class="grid grid-cols-2 gap-2">
		<div>
			<label for="filter-min-price" class="mb-1 block text-xs font-medium text-gray-600">Kaina nuo</label>
			<input
				id="filter-min-price"
				type="number"
				bind:value={minPrice}
				onchange={onapply}
				placeholder="€"
				class="w-full rounded border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
			/>
		</div>
		<div>
			<label for="filter-max-price" class="mb-1 block text-xs font-medium text-gray-600">Kaina iki</label>
			<input
				id="filter-max-price"
				type="number"
				bind:value={maxPrice}
				onchange={onapply}
				placeholder="€"
				class="w-full rounded border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
			/>
		</div>
	</div>

	<div>
		<span class="mb-1 block text-xs font-medium text-gray-600">Kambariai</span>
		<div class="flex gap-1" role="group" aria-label="Kambarių filtras">
			{#each [undefined, 1, 2, 3, 4, 5] as r}
				<button
					type="button"
					onclick={() => {
						rooms = r;
						onapply();
					}}
					class="rounded px-3 py-1 text-xs font-medium transition {rooms === r
						? 'bg-blue-600 text-white'
						: 'bg-gray-100 text-gray-700 hover:bg-gray-200'}"
				>
					{r === undefined ? 'Visi' : r === 5 ? '5+' : r}
				</button>
			{/each}
		</div>
	</div>

	<div>
		<label class="flex items-center gap-2 text-sm text-gray-700">
			<input
				type="checkbox"
				bind:checked={isNewConstruction}
				onchange={onapply}
				class="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
			/>
			Tik naujos statybos
		</label>
	</div>

	<button
		type="button"
		onclick={reset}
		class="w-full rounded border border-gray-300 py-1.5 text-xs text-gray-600 hover:bg-gray-50"
	>
		Išvalyti filtrus
	</button>
</div>
