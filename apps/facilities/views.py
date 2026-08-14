from rest_framework import viewsets

from apps.core.viewsets import TenantScopedViewSetMixin

from .models import Bed, Room, Ward
from .serializers import BedSerializer, RoomSerializer, WardSerializer


class WardViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = WardSerializer
    queryset = Ward.objects.all()
    filterset_fields = ["ward_type", "department", "is_active"]
    search_fields = ["name", "floor"]


class RoomViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = RoomSerializer
    queryset = Room.objects.all()
    filterset_fields = ["ward", "room_type", "is_active"]
    search_fields = ["room_number"]


class BedViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = BedSerializer
    queryset = Bed.objects.all()
    filterset_fields = ["room", "bed_type", "status"]
    search_fields = ["bed_number"]
