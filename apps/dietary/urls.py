from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("diet-types", views.DietTypeViewSet, basename="diettype")
router.register("orders", views.DietOrderViewSet, basename="dietorder")
router.register("consultations", views.DietConsultationViewSet, basename="dietconsultation")
router.register("menu", views.MenuItemViewSet, basename="menuitem")
router.register("meals", views.MealServiceViewSet, basename="mealservice")

urlpatterns = router.urls
