from rest_framework import serializers

from .models import AbhaLink, ConsentRequest, HealthRecordFetch, NHCXTransaction


class AbhaLinkSerializer(serializers.ModelSerializer):
    # Write-only, create-time-only input for AbhaLinkViewSet.perform_create
    # to hand to the gateway (apps.abdm.gateway.ABDMGateway.
    # initiate_abha_verification) — a mobile number or Aadhaar number is
    # never persisted on this row at all, encrypted or otherwise: once the
    # OTP transaction is handed to ABDM, this app has no further need for
    # it, and Aadhaar numbers in particular shouldn't be stored anywhere
    # they don't have to be.
    identifier = serializers.CharField(write_only=True, help_text="Mobile number or Aadhaar number, per `verification_method`.")

    class Meta:
        model = AbhaLink
        fields = [
            "id", "patient", "verification_method", "identifier", "status",
            "abha_number", "abha_address", "failure_reason",
            "initiated_by", "initiated_at", "linked_at",
        ]
        # Every field besides patient/verification_method/identifier (the
        # only inputs `create` accepts) is stamped by the gateway response
        # in perform_create/verify, never a bare PATCH.
        read_only_fields = [
            "id", "status", "abha_number", "abha_address",
            "failure_reason", "initiated_by", "initiated_at", "linked_at",
        ]

    def validate_patient(self, patient):
        # DRF's auto-generated PK-related field for `patient` only checks
        # the row exists, not that it's the caller's own hospital's — the
        # same gap as most FK fields elsewhere in this codebase (e.g.
        # apps.privacy.serializers), but worth closing here since a
        # cross-tenant reference would let hospital A create a linkage
        # row against hospital B's patient (get_queryset's hospital
        # scoping only protects list/retrieve of *existing* rows, not
        # which patient a new row is allowed to point at).
        request = self.context.get("request")
        if request and patient.hospital_id != getattr(request.user, "hospital_id", None):
            raise serializers.ValidationError("Patient does not belong to your hospital.")
        return patient


class VerifyAbhaOtpSerializer(serializers.Serializer):
    otp = serializers.CharField(max_length=10)


class ConsentRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsentRequest
        fields = [
            "id", "patient", "abha_link", "purpose", "hi_types",
            "date_range_from", "date_range_to", "status",
            "gateway_request_id", "consent_artifact_id",
            "requested_by", "requested_at", "responded_at", "expires_at",
        ]
        # status/gateway_request_id/consent_artifact_id/responded_at/
        # expires_at all come from the gateway via check_status — a bare
        # PATCH can't claim a consent was granted that never was.
        read_only_fields = [
            "id", "status", "gateway_request_id", "consent_artifact_id",
            "requested_by", "requested_at", "responded_at", "expires_at",
        ]

    def validate(self, attrs):
        request = self.context.get("request")
        hospital_id = getattr(request.user, "hospital_id", None) if request else None
        patient = attrs.get("patient")
        abha_link = attrs.get("abha_link")
        if patient and patient.hospital_id != hospital_id:
            raise serializers.ValidationError({"patient": "Patient does not belong to your hospital."})
        if abha_link and abha_link.hospital_id != hospital_id:
            raise serializers.ValidationError({"abha_link": "ABHA link does not belong to your hospital."})
        if patient and abha_link and abha_link.patient_id != patient.id:
            raise serializers.ValidationError({"abha_link": "ABHA link does not belong to the specified patient."})
        return attrs


class HealthRecordFetchSerializer(serializers.ModelSerializer):
    class Meta:
        model = HealthRecordFetch
        fields = ["id", "consent_request", "fetched_by", "fetched_at", "hi_types_fetched", "record_count"]
        read_only_fields = fields


class NHCXTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = NHCXTransaction
        fields = [
            "id", "preauth_request", "transaction_type", "status",
            "nhcx_transaction_id", "gateway_response_summary",
            "initiated_by", "initiated_at", "updated_at",
        ]
        read_only_fields = [
            "id", "status", "nhcx_transaction_id", "gateway_response_summary",
            "initiated_by", "initiated_at", "updated_at",
        ]

    def validate_preauth_request(self, preauth_request):
        request = self.context.get("request")
        if preauth_request and request and preauth_request.hospital_id != getattr(request.user, "hospital_id", None):
            raise serializers.ValidationError("Pre-auth request does not belong to your hospital.")
        return preauth_request
