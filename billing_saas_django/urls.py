from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path, re_path
from django.views.static import serve as static_serve
import re

from billing_api.views import captive_probe, react_app, react_asset


urlpatterns = [
    path("generate_204", captive_probe),
    path("hotspot-detect.html", captive_probe),
    path("connecttest.txt", captive_probe),
    path("ncsi.txt", captive_probe),
    path(f"{settings.API_BASE_PATH}/v1/core/", include("core.api.v1.urls")),
    path(f"{settings.API_BASE_PATH}/v1/network/", include("network.api.v1.urls")),
    path(f"{settings.API_BASE_PATH}/v1/management/", include("management.api.v1.urls")),
    path(f"{settings.API_BASE_PATH}/", include("billing_api.urls")),
    path("assets/<path:asset_path>", react_asset, name="react_asset"),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.BASE_DIR / "frontend" / "dist" / "assets")
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
elif settings.STATIC_URL:
    urlpatterns += [
        re_path(
            rf"^{re.escape(settings.STATIC_URL.lstrip('/'))}(?P<path>.*)$",
            static_serve,
            {"document_root": settings.STATIC_ROOT},
        )
    ]

urlpatterns += [re_path(rf"^(?!{re.escape(settings.API_BASE_PATH)}/).*", react_app, name="react_app")]
