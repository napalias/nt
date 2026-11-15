import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ url, fetch }) => {
	const lat = url.searchParams.get('lat') || '55.8835';
	const lng = url.searchParams.get('lng') || '21.242';
	const center = { lat: parseFloat(lat), lng: parseFloat(lng) };
	const radiusParam = url.searchParams.get('radius');

	try {
		// Without a radius in the URL, the default comes from the API, not from this file.
		const radiusM: number = radiusParam
			? parseInt(radiusParam)
			: (await fetch('/api/search/defaults').then((r) => r.json())).radius_m;

		const params = new URLSearchParams({ lat, lng, radius_m: String(radiusM) });

		const minPrice = url.searchParams.get('min_price');
		const maxPrice = url.searchParams.get('max_price');
		const rooms = url.searchParams.get('rooms');
		const propertyType = url.searchParams.get('property_type');
		const isNew = url.searchParams.get('new');

		if (minPrice) params.set('min_price', minPrice);
		if (maxPrice) params.set('max_price', maxPrice);
		if (rooms) params.set('rooms', rooms);
		if (propertyType) params.set('property_type', propertyType);
		if (isNew === '1') params.set('is_new_construction', 'true');

		const [searchRes, evalRes] = await Promise.all([
			fetch(`/api/search?${params.toString()}`).then((r) => r.json()),
			fetch('/api/classifier/evaluated').then((r) => r.json()).catch(() => [])
		]);

		return {
			searchData: searchRes,
			evaluations: evalRes,
			center,
			radiusM
		};
	} catch {
		// API unreachable: show an empty result. The radius is unknown unless the URL gave one.
		const radiusM = radiusParam ? parseInt(radiusParam) : 0;
		return {
			searchData: { center, radius_m: radiusM, count: 0, results: [] },
			evaluations: [],
			center,
			radiusM
		};
	}
};
