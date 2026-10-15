"""Insurer emails imported into the insurance inbox: matched to the case,
typed, queries applied at once, money-moving mail held for review."""
from decimal import Decimal
from email.message import EmailMessage

import pytest

from apps.patients.models import Patient
from apps.tpa.mailbox import classify, ingest, parse_amount, poll
from apps.tpa.models import Claim, ClaimMessage, PreAuthRequest, TPACompany, TPAMailbox

TPA = "/api/v1/tpa"


def mail(subject, body, msg_id, attach=None):
    m = EmailMessage()
    m["From"] = "Star Health Claims <claims@starhealth.example>"
    m["To"] = "tpa@hospital.example"
    m["Subject"] = subject
    m["Message-ID"] = msg_id
    m["Date"] = "Wed, 14 Oct 2026 10:15:00 +0530"
    m.set_content(body)
    if attach:
        m.add_attachment(attach, maintype="application", subtype="pdf", filename="letter.pdf")
    return m.as_bytes()


class FakeIMAP:
    def __init__(self, messages):
        self.messages = messages  # {uid: raw}
        self.selected = None

    def login(self, user, password):
        assert (user, password) == ("tpa@hospital.example", "app-pass")

    def select(self, folder, readonly=False):
        assert readonly  # never marks mail as read
        self.selected = folder

    def uid(self, command, *args):
        if command == "search":
            low = int(args[1].split()[1].split(":")[0])
            return "OK", [" ".join(str(u) for u in sorted(self.messages) if u >= low).encode()]
        uid = int(args[0])
        return "OK", [(b"%d (BODY[] {n})" % uid, self.messages[uid]), b")"]

    def logout(self):
        pass


@pytest.fixture
def s(hospital):
    star = TPACompany.objects.create(hospital=hospital, name="Star Health", code="STAR", avg_tat_days=2)
    patient = Patient.objects.create(hospital=hospital, first_name="Ravi", last_name="Kale", mobile="9811100701")
    pa = PreAuthRequest.objects.create(hospital=hospital, patient=patient, tpa_company=star, policy_number="POL/2026/88812", claim_amount=80000)
    claim = Claim.objects.create(hospital=hospital, patient=patient, tpa_company=star, claim_number="CLM778899", billed_amount=50000)
    mb = TPAMailbox.objects.create(hospital=hospital, host="imap.example", username="tpa@hospital.example", password="app-pass")
    return {"star": star, "pa": pa, "claim": claim, "mb": mb}


def test_classify_and_amount():
    assert classify("Query raised on pre-auth 12", "") == "query"
    assert classify("Cashless approval letter", "please submit documents at discharge") == "approval"  # subject wins
    assert classify("Re: your mail", "Kindly submit the following documents") == "documents"
    assert classify("Claim repudiated", "") == "rejection"
    assert classify("Payment advice - UTR 1234", "") == "settlement"
    assert classify("Enhancement approved", "") == "enhancement_ok"
    assert classify("Hello", "Regards") == "note"
    assert parse_amount("Approved amount: Rs. 60,000.00 for room") == Decimal("60000.00")


def test_query_on_a_preauth_takes_effect_and_approval_waits_for_review(hospital, s):
    q = ingest(s["mb"], mail("Query: Pre-auth #%d" % s["pa"].pk, "Please send the previous discharge summary.", "<q1@star>"))
    assert q.preauth_request_id == s["pa"].pk and q.kind == "query" and not q.needs_review and q.respond_by
    s["pa"].refresh_from_db()
    assert s["pa"].status == "query_raised"
    assert ingest(s["mb"], mail("Query: Pre-auth #%d" % s["pa"].pk, "dup", "<q1@star>")) is None  # same Message-ID

    a = ingest(s["mb"], mail("Cashless approval - policy POL/2026/88812", "Approved amount INR 60,000", "<a1@star>", attach=b"%PDF-1.4"))
    assert a.preauth_request_id == s["pa"].pk and a.needs_review and a.amount == Decimal("60000") and a.attachment
    s["pa"].refresh_from_db()
    assert s["pa"].approved_amount == 0  # nothing moves until confirmed


def test_claim_number_match_unmatched_mail_and_confirm_dismiss(auth_client, hospital, s):
    settle = ingest(s["mb"], mail("Settlement of claim CLM778899", "NEFT UTR 99887 for Rs 42,000", "<s1@star>"))
    assert settle.claim_id == s["claim"].pk and settle.needs_review
    stray = ingest(s["mb"], mail("Approval for your patient", "Approved Rs 10,000", "<x1@star>"))
    assert stray.preauth_request_id is None and stray.claim_id is None and stray.needs_review
    junk = ingest(s["mb"], mail("Newsletter", "Our new products", "<n1@star>"))

    box = auth_client.get(f"{TPA}/messages/inbox/").data
    assert box["counts"]["to_review"] == 3

    ok = auth_client.post(f"{TPA}/messages/{settle.pk}/confirm/", {"amount": "42000"}, format="json")
    assert ok.status_code == 200 and not ok.data["needs_review"]
    s["claim"].refresh_from_db()
    assert s["claim"].status == "partially_settled" and s["claim"].settled_amount == Decimal("42000")
    assert auth_client.post(f"{TPA}/messages/{stray.pk}/confirm/", {}, format="json").status_code == 400  # no case yet
    assert auth_client.post(f"{TPA}/messages/{stray.pk}/confirm/", {"preauth_request": s["pa"].pk}, format="json").status_code == 200
    s["pa"].refresh_from_db()
    assert s["pa"].status == "approved" and s["pa"].approved_amount == Decimal("10000")
    assert auth_client.post(f"{TPA}/messages/{junk.pk}/dismiss/").status_code == 200
    assert auth_client.get(f"{TPA}/messages/inbox/").data["counts"]["to_review"] == 0


def test_poll_imports_new_uids_only_and_records_errors(hospital, s):
    server = FakeIMAP({5: mail("Query on claim CLM778899", "Need original bills", "<p1@star>"),
                       6: mail("Hello", "Regards", "<p2@star>")})
    assert poll(s["mb"], connect=lambda: server) == 2
    s["mb"].refresh_from_db()
    assert s["mb"].last_uid == 6 and s["mb"].last_error == "" and s["mb"].last_checked_at
    server.messages[7] = mail("Query again CLM778899", "x", "<p3@star>")
    assert poll(s["mb"], connect=lambda: server) == 1
    assert ClaimMessage.objects.filter(source="email").count() == 3

    def broken():
        raise OSError("connection refused")

    assert poll(s["mb"], connect=broken) == 0
    s["mb"].refresh_from_db()
    assert "connection refused" in s["mb"].last_error and s["mb"].last_uid == 7


def test_mailbox_settings_keep_the_password_write_only(auth_client, hospital):
    assert auth_client.get(f"{TPA}/mailbox/").data == {"configured": False}
    assert auth_client.put(f"{TPA}/mailbox/", {"host": "imap.example", "username": "u@x.example"}, format="json").status_code == 400
    r = auth_client.put(f"{TPA}/mailbox/", {"host": "imap.example", "username": "u@x.example", "password": "secret"}, format="json").data
    assert r["configured"] and r["has_password"] and "password" not in r
    assert auth_client.put(f"{TPA}/mailbox/", {"folder": "Claims"}, format="json").data["folder"] == "Claims"  # password kept
