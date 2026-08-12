from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("apps.core.urls")),
    path("api/v1/", include("apps.accounts.urls")),
    path("api/v1/", include("apps.patients.urls")),
    path("api/v1/", include("apps.telephony.urls")),
    path("api/v1/", include("apps.enquiries.urls")),
    path("api/v1/", include("apps.appointments.urls")),
    path("api/v1/", include("apps.communications.urls")),
    path("api/v1/", include("apps.automation.urls")),
    path("api/v1/", include("apps.feedback.urls")),
    path("api/v1/", include("apps.analytics.urls")),
    path("api/v1/", include("apps.integrations.urls")),
    path("api/v1/referrals/", include("apps.referrals.urls")),
    path("api/v1/packages/", include("apps.packages.urls")),
    path("api/v1/tpa/", include("apps.tpa.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/schema/swagger-ui/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
]

