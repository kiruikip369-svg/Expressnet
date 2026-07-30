from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path, re_path
import re

from billing_api.views import captive_probe, react_app, react_asset


urlpatterns = [
    path("generate_204", captive_probe),
    path("hotspot-detect.html", captive_probe),
    path("connecttest.txt", captive_probe),
    path("ncsi.txt", captive_probe),
    path(f"{settings.API_BASE_PATH}/", include("billing_api.urls")),
    path("assets/<path:asset_path>", react_asset, name="react_asset"),
    re_path(rf"^(?!{re.escape(settings.API_BASE_PATH)}/).*", react_app, name="react_app"),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.BASE_DIR / "frontend" / "dist" / "assets")
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
