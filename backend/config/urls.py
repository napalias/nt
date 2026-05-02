from django.contrib import admin
from django.http import JsonResponse
from django.urls import path
from ninja import NinjaAPI

from apps.cadastre.api import router as cadastre_router
from apps.classifier.api import router as classifier_router
from apps.developers.api import router as developers_router
from apps.permits.api import router as permits_router
from apps.planning.api import router as planning_router
from apps.search.api import router as search_router

api = NinjaAPI(title="Real Estate API", urls_namespace="api")
api.add_router("/", search_router)
api.add_router("/classifier/", classifier_router)
api.add_router("/developers/", developers_router)
api.add_router("/layers/", cadastre_router)
api.add_router("/permits/", permits_router)
api.add_router("/planning/", planning_router)


def healthcheck(request) -> JsonResponse:
    return JsonResponse({"ok": True})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", api.urls),
    path("health/", healthcheck),
]
