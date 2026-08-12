"""
Pluggable HIS connector. The doc lists several possible transports (HL7 v2,
FHIR R4, read-only DB views, SFTP drop, an on-prem sync agent) because the
hospital's HIS vendor isn't fixed — this interface is deliberately
transport-agnostic: whichever one is chosen, it only has to produce the
same two lists of dicts, and apps.integrations.tasks.sync_his_data upserts
them into HISVisit / HISBillingRecord regardless of where they came from.
"""
from abc import ABC, abstractmethod
from datetime import datetime

from django.conf import settings


class HISConnector(ABC):
    @abstractmethod
    def fetch_visits(self, *, since: datetime) -> list[dict]:
        """Each dict: patient_mobile, external_visit_id, visit_type,
        visit_date, department_name, doctor_name."""

    @abstractmethod
    def fetch_billing(self, *, since: datetime) -> list[dict]:
        """Each dict: patient_mobile, external_bill_id, bill_date,
        total_amount, paid_amount, status."""


class StubHISConnector(HISConnector):
    """No HIS integration wired up for this hospital yet — returns nothing
    so the rest of the system (patient 360, dues display) runs cleanly
    against an empty cache until a real connector is configured."""

    def fetch_visits(self, *, since: datetime) -> list[dict]:
        return []

    def fetch_billing(self, *, since: datetime) -> list[dict]:
        return []


def get_his_connector() -> HISConnector:
    connectors = {"stub": StubHISConnector}
    connector_cls = connectors.get(settings.HIS_CONNECTOR, StubHISConnector)
    return connector_cls()
