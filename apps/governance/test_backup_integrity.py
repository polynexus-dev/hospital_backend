"""M3: a backup is only restored after its integrity is proven. A record
with no stored checksum, or whose file no longer matches it, is refused."""
import gzip
import hashlib

import pytest

from apps.governance.models import BackupRecord
from apps.governance.services import restore_backup


def _backup(hospital, tmp_path, *, checksum, payload=b"[]"):
    path = tmp_path / "backup.json.gz"
    with gzip.open(path, "wb") as fh:
        fh.write(payload)
    return BackupRecord.objects.create(
        hospital=hospital, status=BackupRecord.Status.SUCCESS,
        file_path=str(path), checksum_sha256=checksum,
    )


@pytest.mark.django_db
def test_restore_refuses_a_backup_with_no_checksum(hospital, tmp_path):
    rec = _backup(hospital, tmp_path, checksum="")
    with pytest.raises(ValueError, match="no integrity checksum"):
        restore_backup(rec)


@pytest.mark.django_db
def test_restore_refuses_a_tampered_backup(hospital, tmp_path):
    rec = _backup(hospital, tmp_path, checksum="0" * 64)  # wrong digest
    with pytest.raises(ValueError, match="checksum mismatch"):
        restore_backup(rec)


@pytest.mark.django_db
def test_restore_accepts_a_matching_checksum(hospital, tmp_path):
    payload = b"[]"
    rec = _backup(hospital, tmp_path, checksum=hashlib.sha256(payload).hexdigest(), payload=payload)
    assert restore_backup(rec) == 0
    rec.refresh_from_db()
    assert rec.status == BackupRecord.Status.RESTORED
