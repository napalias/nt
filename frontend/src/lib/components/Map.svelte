<script lang="ts">
	import { onMount } from 'svelte';
	import maplibregl from 'maplibre-gl';
	import 'maplibre-gl/dist/maplibre-gl.css';
	import type { EvaluationResult, GeoJsonCollection, ListingResult } from '$lib/api/client';

	let {
		listings = [],
		evaluations = {},
		geoJsonLayers = {},
		center = { lat: 55.8835, lng: 21.242 },
		activeId = null,
		onmarkerclick,
		onbboxchange
	}: {
		listings: ListingResult[];
		evaluations: Record<number, EvaluationResult>;
		geoJsonLayers: Record<string, { data: GeoJsonCollection; color: string; visible: boolean }>;
		center: { lat: number; lng: number };
		activeId: number | null;
		onmarkerclick?: (listing: ListingResult) => void;
		onbboxchange?: (bbox: string) => void;
	} = $props();

	let mapContainer: HTMLDivElement;
	let map: maplibregl.Map;
	let markers = new globalThis.Map<number, maplibregl.Marker>();
	let mapReady = $state(false);

	const verdictColors: Record<string, string> = {
		match: '#16a34a',
		review: '#ca8a04',
		skip: '#dc2626'
	};
	const defaultColor = '#3b82f6';

	function getColor(listingId: number): string {
		const ev = evaluations[listingId];
		if (!ev) return defaultColor;
		return verdictColors[ev.verdict] || defaultColor;
	}

	function getBboxString(): string {
		const bounds = map.getBounds();
		return `${bounds.getWest()},${bounds.getSouth()},${bounds.getEast()},${bounds.getNorth()}`;
	}

	onMount(() => {
		map = new maplibregl.Map({
			container: mapContainer,
			style: {
				version: 8,
				sources: {
					osm: {
						type: 'raster',
						tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
						tileSize: 256,
						attribution:
							'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
					}
				},
				layers: [{ id: 'osm', type: 'raster', source: 'osm' }]
			},
			center: [center.lng, center.lat],
			zoom: 12
		});

		map.addControl(new maplibregl.NavigationControl(), 'top-right');

		map.on('load', () => {
			mapReady = true;
		});

		map.on('moveend', () => {
			onbboxchange?.(getBboxString());
		});

		return () => map.remove();
	});

	// Listing markers
	$effect(() => {
		if (!map) return;

		for (const [, marker] of markers) {
			marker.remove();
		}
		markers.clear();

		for (const listing of listings) {
			const color = getColor(listing.id);
			const el = document.createElement('div');
			el.className = 'map-marker';
			el.dataset.color = color;
			el.style.cssText = `width:12px;height:12px;background:${color};border:2px solid #fff;border-radius:50%;cursor:pointer;box-shadow:0 1px 3px rgba(0,0,0,0.3);transition:all 0.15s;`;

			el.addEventListener('click', () => onmarkerclick?.(listing));
			el.addEventListener('mouseenter', () => {
				el.style.width = '16px';
				el.style.height = '16px';
			});
			el.addEventListener('mouseleave', () => {
				if (activeId !== listing.id) {
					el.style.width = '12px';
					el.style.height = '12px';
				}
			});

			const ev = evaluations[listing.id];
			const scoreHtml = ev
				? `<br/><span style="color:${color};font-weight:600">${Math.round(ev.match_score * 100)}% — ${ev.verdict}</span>`
				: '';

			const popup = new maplibregl.Popup({ offset: 12, closeButton: false }).setHTML(
				`<div style="font-size:12px;max-width:220px">
					<strong>${listing.title}</strong><br/>
					<span style="color:#3b82f6;font-weight:600">${listing.price ? `€${listing.price.toLocaleString()}` : '—'}</span>
					${listing.area_sqm ? `<span style="color:#888"> · ${listing.area_sqm} m²</span>` : ''}
					${scoreHtml}
				</div>`
			);

			const marker = new maplibregl.Marker({ element: el })
				.setLngLat([listing.lng, listing.lat])
				.setPopup(popup)
				.addTo(map);

			markers.set(listing.id, marker);
		}
	});

	// Active marker highlight
	$effect(() => {
		if (!map) return;
		for (const [id, marker] of markers) {
			const el = marker.getElement();
			const baseColor = el.dataset.color || defaultColor;
			if (id === activeId) {
				el.style.width = '16px';
				el.style.height = '16px';
				el.style.zIndex = '10';
			} else {
				el.style.width = '12px';
				el.style.height = '12px';
				el.style.background = baseColor;
				el.style.zIndex = '';
			}
		}
	});

	// GeoJSON polygon layers
	$effect(() => {
		if (!map || !mapReady) return;

		for (const [layerId, config] of Object.entries(geoJsonLayers)) {
			const sourceId = `layer-${layerId}`;
			const fillId = `${sourceId}-fill`;
			const lineId = `${sourceId}-line`;

			if (map.getSource(sourceId)) {
				(map.getSource(sourceId) as maplibregl.GeoJSONSource).setData(
					config.data as unknown as GeoJSON.GeoJSON
				);
				map.setLayoutProperty(fillId, 'visibility', config.visible ? 'visible' : 'none');
				map.setLayoutProperty(lineId, 'visibility', config.visible ? 'visible' : 'none');
			} else {
				map.addSource(sourceId, {
					type: 'geojson',
					data: config.data as unknown as GeoJSON.GeoJSON
				});
				map.addLayer({
					id: fillId,
					type: 'fill',
					source: sourceId,
					paint: {
						'fill-color': config.color,
						'fill-opacity': 0.15
					},
					layout: {
						visibility: config.visible ? 'visible' : 'none'
					}
				});
				map.addLayer({
					id: lineId,
					type: 'line',
					source: sourceId,
					paint: {
						'line-color': config.color,
						'line-width': 1.5,
						'line-opacity': 0.6
					},
					layout: {
						visibility: config.visible ? 'visible' : 'none'
					}
				});
			}
		}
	});

	// Fly to center
	$effect(() => {
		if (!map) return;
		map.flyTo({ center: [center.lng, center.lat], zoom: 12, duration: 1000 });
	});
</script>

<div bind:this={mapContainer} class="h-full w-full"></div>
