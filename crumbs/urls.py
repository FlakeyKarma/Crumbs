from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("pantry/", include("pantry.urls")),
    path("health/", include("health.urls")),
    # Last: the recipes app owns the root, including a /<slug>/ catch-all
    # shape, so anything with its own prefix has to be registered above it.
    path("", include("recipes.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
