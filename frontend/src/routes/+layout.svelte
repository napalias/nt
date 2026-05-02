<script lang="ts">
    import '../app.css';
    import { page } from '$app/stores';
    let { children } = $props();

    let isSearchPage = $derived($page.url.pathname === '/search');

    const navLinks = [
        { href: '/', label: 'Pradzia' },
        { href: '/search', label: 'Paieska' },
        { href: '/searches', label: 'Issaugotos paieškos' },
        { href: '/preferences', label: 'Prioritetai' }
    ];
</script>

{#if !isSearchPage}
    <nav class="border-b border-gray-200 bg-white shadow-sm">
        <div class="mx-auto flex max-w-5xl items-center gap-6 px-4 py-3">
            <a href="/" class="flex-shrink-0 text-lg font-bold text-gray-900">NT Paieška</a>
            <div class="flex items-center gap-1">
                {#each navLinks as link}
                    <a
                        href={link.href}
                        class="rounded-md px-3 py-1.5 text-sm font-medium transition
                            {$page.url.pathname === link.href
                                ? 'bg-blue-50 text-blue-700'
                                : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'}"
                    >
                        {link.label}
                    </a>
                {/each}
            </div>
        </div>
    </nav>
{/if}

{@render children()}
