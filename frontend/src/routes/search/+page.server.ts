import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ url, fetch }) => {
	const lat = url.searchParams.get('lat') || '55.8835';
	const lng = url.searchParams.get('lng') || '21.242';
	const radius = url.searchParams.get('radius') || '5000';

	const params = new URLSearchParams({ lat, lng, radius_m: radius });

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

	try {
		const [searchRes, evalRes] = await Promise.all([
			fetch(`/api/search?${params.toString()}`).then((r) => r.json()),
			fetch('/api/classifier/evaluated').then((r) => r.json()).catch(() => [])
		]);

		return {
			searchData: searchRes,
			evaluations: evalRes,
			center: { lat: parseFloat(lat), lng: parseFloat(lng) },
			radiusM: parseInt(radius)
		};
	} catch {
		return {
			searchData: { center: { lat: parseFloat(lat), lng: parseFloat(lng) }, radius_m: parseInt(radius), count: 0, results: [] },
			evaluations: [],
			center: { lat: parseFloat(lat), lng: parseFloat(lng) },
			radiusM: parseInt(radius)
		};
	}
};
