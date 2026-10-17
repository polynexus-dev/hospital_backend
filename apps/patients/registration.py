"""
NABH AAC.1 registration capabilities: duplicate detection & merge (AAC.1.f),
mobile OTP verification (AAC.1.b), configurable UHID (AAC.1.d), and
idempotent offline-registration sync (AAC.1.g).
"""
import logging
import secrets
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.db.models import Q, UniqueConstraint
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.encryption import blind_index, name_query_tokens, normalize_phone

from .models import MobileOTP, Patient, UHIDConfig

logger = logging.getLogger(__name__)

OTP_TTL_MINUTES = 10
OTP_MAX_ATTEMPTS = 5


def _norm(s):
    return (s or "").strip().lower()


def find_duplicates(hospital_id, *, first_name="", last_name="", date_of_birth=None, mobile="", national_id="", exclude_pk=None):
    """Scores candidate matches: identical national id (100), same mobile
    (40), same full name (35), same DOB (25), same first name (10).
    exact = score ≥ 100 (name+DOB+mobile, or national id); probable ≥ 50."""
    qs = Patient.objects.filter(hospital_id=hospital_id)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    digest = blind_index(normalize_phone(mobile)) if mobile else ""
    cond = Q()
    if digest:
        cond |= Q(mobile_hash=digest) | Q(alternate_mobile_hash=digest)
    # Names and dates of birth are encrypted: narrow by their search hashes
    # and birth year, then compare the decrypted values below.
    if first_name and name_query_tokens(first_name):
        cond |= Q(pk__in=Patient.objects.filter(hospital_id=hospital_id).search_name(first_name).values("pk"))
    if date_of_birth:
        year = str(date_of_birth)[:4]
        if year.isdigit():
            cond |= Q(birth_year=int(year))
    if not cond:
        return []
    out = []
    for p in qs.filter(cond)[:200]:
        score, reasons = 0, []
        if national_id and p.national_id_number and _norm(p.national_id_number) == _norm(national_id):
            score += 100
            reasons.append("same national ID")
        if digest and digest in (p.mobile_hash, p.alternate_mobile_hash):
            score += 40
            reasons.append("same mobile")
        if first_name and _norm(p.first_name) == _norm(first_name):
            if _norm(p.last_name) == _norm(last_name):
                score += 35
                reasons.append("same name")
            else:
                score += 10
                reasons.append("same first name")
        if date_of_birth and p.date_of_birth and str(p.date_of_birth) == str(date_of_birth):
            score += 25
            reasons.append("same date of birth")
        if score >= 50:
            out.append({"id": p.pk, "uhid": p.uhid, "name": p.full_name, "date_of_birth": p.date_of_birth, "score": score,
                        "match": "exact" if score >= 100 else "probable", "reasons": reasons})
    return sorted(out, key=lambda r: -r["score"])


def _unique_sets_with(model, field):
    """Unique field groups on `model` that include `field` (a patient FK)."""
    sets = [tuple(f) for f in model._meta.unique_together]
    sets += [tuple(c.fields) for c in model._meta.constraints if isinstance(c, UniqueConstraint) and c.fields]
    return [s for s in sets if field.name in s or field.attname in s]


@transaction.atomic
def merge_patients(primary, duplicate, *, actor=None):
    """Re-points every relation from `duplicate` to `primary` and soft-deletes
    the duplicate. One-to-one relations that would collide are left on the
    duplicate and reported, never silently overwritten."""
    moved, skipped = {}, []
    for rel in Patient._meta.related_objects:
        model = rel.related_model
        fk = rel.field.name
        if rel.one_to_one:
            if model._base_manager.filter(**{fk: primary}).exists():
                if model._base_manager.filter(**{fk: duplicate}).exists():
                    skipped.append(model.__name__)
                continue
        if rel.many_to_many:
            continue
        rows = model._base_manager.filter(**{fk: duplicate})
        unique_sets = _unique_sets_with(model, rel.field)
        if unique_sets:
            # Move row by row: one the primary already has (same consent
            # opt-out, say) stays with the duplicate rather than failing.
            n = 0
            for row in rows:
                if any(model._base_manager.filter(**{fk: primary}, **{f: getattr(row, f) for f in fields if f not in (fk, rel.field.attname)}).exists()
                       for fields in unique_sets):
                    skipped.append(model.__name__)
                    continue
                model._base_manager.filter(pk=row.pk).update(**{fk: primary})
                n += 1
        else:
            n = rows.update(**{fk: primary})
        if n:
            moved[model.__name__] = n
    for field in ("date_of_birth", "gender", "email", "blood_group", "national_id_type", "national_id_number"):
        if not getattr(primary, field) and getattr(duplicate, field):
            setattr(primary, field, getattr(duplicate, field))
    if not primary.mrn and duplicate.uhid:
        primary.mrn = f"merged:{duplicate.uhid}"
    primary.save()
    duplicate.delete(actor=actor, reason=f"Merged into {primary.uhid}")
    return {"moved": moved, "skipped_one_to_one": sorted(set(skipped))}


def issue_otp(hospital, mobile, purpose="registration"):
    from apps.communications.adapters import get_sms_provider

    code = f"{secrets.randbelow(10**6):06d}"
    MobileOTP.objects.create(
        hospital=hospital, mobile_hash=blind_index(normalize_phone(mobile)), purpose=purpose,
        code_hash=make_password(code), expires_at=timezone.now() + timedelta(minutes=OTP_TTL_MINUTES),
    )
    # SMS goes out on the hospital's DLT-registered "registration_otp"
    # template (MSG91 needs its template id); plain text is the fallback for
    # the stub / test provider.
    from apps.clinical.notify import ensure_default_templates
    from apps.communications.services import get_active_template

    ensure_default_templates(hospital)
    context = {"otp": code, "hospital_name": hospital.name, "minutes": OTP_TTL_MINUTES}
    template = get_active_template(hospital, purpose="registration_otp", channel="sms", language="en")
    text = f"{code} is your OTP to verify your mobile number with {hospital.name}. Valid for {OTP_TTL_MINUTES} minutes."
    if template is not None:
        try:
            text = template.render(context)
        except (KeyError, IndexError, ValueError):
            logger.warning("Hospital %s's registration_otp template uses a placeholder we don't fill", hospital.pk)
    try:
        get_sms_provider().send(to=mobile, body=text, subject="", template=template, context=context)
    except Exception:
        logger.warning("Registration OTP SMS to hospital %s failed", hospital.pk, exc_info=True)
    return code


def verify_otp(hospital, mobile, code, purpose="registration"):
    otp = MobileOTP.objects.filter(hospital=hospital, mobile_hash=blind_index(normalize_phone(mobile)), purpose=purpose, verified_at__isnull=True).first()
    if otp is None or otp.expires_at < timezone.now():
        return False, "OTP expired or not requested."
    if otp.attempts >= OTP_MAX_ATTEMPTS:
        return False, "Too many attempts — request a new OTP."
    otp.attempts += 1
    if not check_password(str(code), otp.code_hash):
        otp.save(update_fields=["attempts"])
        return False, "Invalid OTP."
    otp.verified_at = timezone.now()
    otp.save(update_fields=["attempts", "verified_at"])
    return True, ""


class PatientRegistrationMixin:
    """Actions added to PatientViewSet."""

    def create(self, request, *args, **kwargs):
        """AAC.1.f — an exact duplicate is refused (showing the existing
        record); probable duplicates come back as a warning the front desk
        must confirm with confirm_not_duplicate=true."""
        if not request.data.get("confirm_not_duplicate"):
            dupes = find_duplicates(
                request.user.hospital_id, first_name=request.data.get("first_name", ""), last_name=request.data.get("last_name", ""),
                date_of_birth=request.data.get("date_of_birth"), mobile=request.data.get("mobile", ""), national_id=request.data.get("national_id_number", ""),
            )
            exact = [d for d in dupes if d["match"] == "exact"]
            if exact:
                return Response({"detail": "Duplicate patient — this person is already registered.", "code": "duplicate_patient", "matches": exact}, status=status.HTTP_409_CONFLICT)
        return super().create(request, *args, **kwargs)

    @action(detail=False, methods=["post"], url_path="check-duplicates")
    def check_duplicates(self, request):
        d = request.data
        return Response(find_duplicates(request.user.hospital_id, first_name=d.get("first_name", ""), last_name=d.get("last_name", ""),
                                        date_of_birth=d.get("date_of_birth"), mobile=d.get("mobile", ""), national_id=d.get("national_id_number", "")))

    @action(detail=True, methods=["post"])
    def merge(self, request, pk=None):
        primary = self.get_object()
        dup = Patient.objects.filter(pk=request.data.get("duplicate_id"), hospital_id=request.user.hospital_id).first()
        if dup is None or dup.pk == primary.pk:
            return Response({"duplicate_id": "A different patient from your hospital is required."}, status=400)
        return Response(merge_patients(primary, dup, actor=request.user))

    @action(detail=True, methods=["post"], url_path="verify-mobile")
    def verify_mobile(self, request, pk=None):
        """POST {} → sends OTP; POST {otp} → verifies."""
        patient = self.get_object()
        if not request.data.get("otp"):
            issue_otp(patient.hospital, patient.mobile)
            return Response({"sent": True, "expires_in_minutes": OTP_TTL_MINUTES})
        ok, err = verify_otp(patient.hospital, patient.mobile, request.data["otp"])
        if not ok:
            return Response({"otp": err}, status=400)
        Patient.objects.filter(pk=patient.pk).update(mobile_verified_at=timezone.now())
        return Response({"verified": True})

    @action(detail=False, methods=["post"], url_path="offline-sync")
    def offline_sync(self, request):
        """AAC.1.g — registrations captured offline, replayed once back
        online. Idempotent on offline_client_id; each row gets the same
        duplicate screening as an online registration."""
        rows = request.data.get("patients") or []
        if not isinstance(rows, list):
            return Response({"patients": "A list of patient registrations is required."}, status=400)
        results = []
        for row in rows[:200]:
            if not isinstance(row, dict):
                results.append({"status": "error", "error": "each registration must be an object"})
                continue
            cid = str(row.get("offline_client_id", "")).strip()
            if not cid:
                results.append({"status": "error", "error": "offline_client_id required"})
                continue
            existing = Patient.objects.filter(hospital_id=request.user.hospital_id, offline_client_id=cid).first()
            if existing:
                results.append({"offline_client_id": cid, "status": "already_synced", "id": existing.pk, "uhid": existing.uhid})
                continue
            exact = [d for d in find_duplicates(request.user.hospital_id, first_name=row.get("first_name", ""), last_name=row.get("last_name", ""),
                                                date_of_birth=row.get("date_of_birth"), mobile=row.get("mobile", "")) if d["match"] == "exact"]
            if exact:
                results.append({"offline_client_id": cid, "status": "duplicate", "matches": exact})
                continue
            ser = self.get_serializer(data={**row, "registration_channel": "offline"})
            if not ser.is_valid():
                results.append({"offline_client_id": cid, "status": "error", "error": ser.errors})
                continue
            with transaction.atomic():
                p = ser.save(hospital=request.user.hospital, offline_client_id=cid)
            results.append({"offline_client_id": cid, "status": "created", "id": p.pk, "uhid": p.uhid})
        return Response({"results": results})


class UHIDConfigSerializer(serializers.ModelSerializer):
    preview = serializers.SerializerMethodField()

    class Meta:
        model = UHIDConfig
        exclude = ["hospital"]
        read_only_fields = ["id"]

    def get_preview(self, obj):
        return obj.format(obj.hospital.next_uhid_sequence)

    def validate_pattern(self, value):
        if "{SEQ}" not in value:
            raise serializers.ValidationError("Pattern must contain {SEQ} so every UHID is unique.")
        return value


class UHIDConfigView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        cfg, _ = UHIDConfig.objects.get_or_create(hospital=request.user.hospital, defaults={"prefix": request.user.hospital.slug.upper()[:6]})
        return Response(UHIDConfigSerializer(cfg).data)

    def patch(self, request):
        from apps.core.permissions import CanReviewEmergencyAccess

        if not CanReviewEmergencyAccess().has_permission(request, self):
            return Response({"detail": "Administrators only."}, status=403)
        cfg, _ = UHIDConfig.objects.get_or_create(hospital=request.user.hospital)
        ser = UHIDConfigSerializer(cfg, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data)
