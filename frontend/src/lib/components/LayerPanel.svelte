<script lang="ts">
	interface Layer {
		id: string;
		label: string;
		color: string;
		count: number;
		enabled: boolean;
	}

	let {
		layers = $bindable<Layer[]>([]),
		ontoggle
	}: {
		layers: Layer[];
		ontoggle: (layerId: string, enabled: boolean) => void;
	} = $props();
</script>

<div class="space-y-2">
	<h3 class="text-xs font-semibold uppercase tracking-wide text-gray-500">Sluoksniai</h3>

	{#each layers as layer}
		<label class="flex cursor-pointer items-center gap-2 rounded px-1 py-1 hover:bg-gray-50">
			<input
				type="checkbox"
				checked={layer.enabled}
				onchange={() => ontoggle(layer.id, !layer.enabled)}
				class="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
			/>
			<span class="h-3 w-3 rounded-sm" style="background-color: {layer.color}"></span>
			<span class="flex-1 text-xs text-gray-700">{layer.label}</span>
			{#if layer.count > 0}
				<span class="text-[10px] text-gray-400">{layer.count}</span>
			{/if}
		</label>
	{/each}
</div>
