import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
	try {
		const [evalRes, searchRes] = await Promise.all([
			fetch('/api/classifier/evaluated').then((r) => r.json()).catch(() => []),
			fetch('/api/search?lat=55.8835&lng=21.242&radius_m=50000').then((r) => r.json()).catch(() => ({ results: [] }))
		]);

		return {
			evaluations: evalRes as import('$lib/api/client').EvaluationResult[],
			searchData: searchRes as import('$lib/api/client').SearchResponse
		};
	} catch {
		return {
			evaluations: [],
			searchData: { center: { lat: 55.8835, lng: 21.242 }, radius_m: 50000, count: 0, results: [] }
		};
	}
};
