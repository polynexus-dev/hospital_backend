"""COP.1.e digital signatures (and the "non-editable once signed" rule in
AAC.3.g/AAC.4.h). Signing re-authenticates the signer, hashes the record's
content, and finalizes the record if it's a FinalizableModel."""
import hashlib
import json

import pyotp
from django.contrib.contenttypes.models import ContentType
from django.forms.models import model_to_dict

from .models import DigitalSignature

_SKIP = {"updated_at", "finalized_at", "finalized_by"}


def document_hash(instance):
    data = {k: v for k, v in model_to_dict(instance).items() if k not in _SKIP}
    for f in instance._meta.fields:  # model_to_dict skips non-editable fields
        if f.name not in data and f.name not in _SKIP:
            data[f.name] = getattr(instance, f.attname)
    payload = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SignatureError(Exception):
    pass


def sign_document(instance, user, *, method, password="", otp="", signature_image=None):
    if method == DigitalSignature.Method.PASSWORD:
        if not user.check_password(password):
            raise SignatureError("Password verification failed.")
    elif method == DigitalSignature.Method.TOTP:
        if not (user.is_2fa_enabled and user.totp_secret and pyotp.TOTP(user.totp_secret).verify(str(otp), valid_window=1)):
            raise SignatureError("OTP verification failed.")
    elif method == DigitalSignature.Method.STYLUS:
        if signature_image is None and not user.signature_image:
            raise SignatureError("A drawn signature is required.")
        if not user.check_password(password):
            raise SignatureError("Password verification failed.")
    else:
        raise SignatureError("Unknown signing method.")

    if hasattr(instance, "finalize") and not getattr(instance, "finalized_at", None):
        instance.finalize(user)

    sig = DigitalSignature(
        hospital_id=instance.hospital_id,
        content_type=ContentType.objects.get_for_model(instance),
        object_id=str(instance.pk),
        signer=user,
        signer_name=user.get_full_name(),
        signer_registration_number=getattr(user, "registration_number", ""),
        method=method,
        document_hash=document_hash(instance),
    )
    if signature_image is not None:
        sig.signature_image = signature_image
    elif user.signature_image:
        sig.signature_image = user.signature_image.name
    sig.save()
    return sig


def signatures_for(instance):
    return DigitalSignature.objects.filter(content_type=ContentType.objects.get_for_model(instance), object_id=str(instance.pk))
