from rest_framework.permissions import BasePermission

from .features import FEATURE_KEYS, label
from .service import feature_enabled


def HasFeature(feature: str):
    """DRF permission class: the licence must include `feature`.

        permission_classes = [IsAuthenticated, HasFeature("ai_assist")]

    Views that belong to a module are already gated by
    apps.core.modules.ModuleAccessMiddleware, which reads the licensed
    features' modules; HasFeature covers features with no module of their
    own (AI assist, IVR, multi-branch, ...). Always allowed in SaaS mode,
    where the subscription governs."""
    if feature not in FEATURE_KEYS:
        raise ValueError(f"Unknown licence feature: {feature}")

    class _HasFeature(BasePermission):
        message = f"{label(feature)} is not included in this installation's licence."

        def has_permission(self, request, view):
            return feature_enabled(feature)

    _HasFeature.__name__ = f"HasFeature_{feature}"
    return _HasFeature
