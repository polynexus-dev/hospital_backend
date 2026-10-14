"""Each user's own signature and stamp for signed clinical documents (COP.1.e)."""
from rest_framework import serializers
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

MAX_BYTES = 2 * 1024 * 1024


def _payload(user):
    return {
        "signature_image": user.signature_image.url if user.signature_image else "",
        "stamp_image": user.stamp_image.url if user.stamp_image else "",
        "signature_width_mm": user.signature_width_mm, "stamp_width_mm": user.stamp_width_mm,
        "registration_number": user.registration_number,
    }


class MySignatureView(APIView):
    """GET / PUT (multipart) /users/me/signature/ — signature_image, stamp_image,
    signature_width_mm, stamp_width_mm, registration_number; remove_signature /
    remove_stamp = true to clear."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        return Response(_payload(request.user))

    def put(self, request):
        user = request.user
        fields = []
        for key in ("signature_image", "stamp_image"):
            f = request.FILES.get(key)
            if f is not None:
                if f.size > MAX_BYTES or (f.content_type or "").split("/")[0] != "image":
                    raise serializers.ValidationError({key: "A PNG or JPG image under 2 MB (PNG with a transparent background prints best)."})
                setattr(user, key, f)
                fields.append(key)
            elif str(request.data.get(f"remove_{key.split('_')[0]}", "")).lower() in ("1", "true"):
                getattr(user, key).delete(save=False)
                setattr(user, key, "")
                fields.append(key)
        for key in ("signature_width_mm", "stamp_width_mm"):
            if request.data.get(key) not in (None, ""):
                try:
                    value = int(request.data[key])
                except (TypeError, ValueError):
                    raise serializers.ValidationError({key: "Millimetres, 10 to 80."})
                if not 10 <= value <= 80:
                    raise serializers.ValidationError({key: "Millimetres, 10 to 80."})
                setattr(user, key, value)
                fields.append(key)
        if "registration_number" in request.data:
            user.registration_number = str(request.data["registration_number"])[:64]
            fields.append("registration_number")
        if fields:
            user.save(update_fields=fields)
        return Response(_payload(user))
