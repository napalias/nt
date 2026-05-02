<script lang="ts">
	import { page } from '$app/stores';
</script>

<svelte:head>
	<title>Klaida — NT Paieška</title>
</svelte:head>

<div class="flex min-h-[60vh] flex-col items-center justify-center px-4">
	<div class="mx-auto max-w-md text-center">
		<div class="text-6xl font-bold text-gray-200">{$page.status}</div>

		<h1 class="mt-4 text-xl font-semibold text-gray-900">
			{#if $page.status === 404}
				Puslapis nerastas
			{:else if $page.status === 500}
				Serverio klaida
			{:else if $page.status === 403}
				Prieiga uždrausta
			{:else}
				Ivyko klaida
			{/if}
		</h1>

		<p class="mt-2 text-sm text-gray-500">
			{#if $page.status === 404}
				Puslapis, kurio ieškote, neegzistuoja arba buvo perkeltas.
			{:else if $page.error?.message}
				{$page.error.message}
			{:else}
				Pabandykite atnaujinti puslapi arba grižti i pradzia.
			{/if}
		</p>

		<div class="mt-6 flex justify-center gap-3">
			<a
				href="/"
				class="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-blue-700"
			>
				Pradzia
			</a>
			<button
				onclick={() => window.location.reload()}
				class="rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-50"
			>
				Bandyti dar karta
			</button>
		</div>
	</div>
</div>
