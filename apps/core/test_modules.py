"""The licensable-module list lives in two places — the backend (what a
hospital may be given) and the SaaS console (what an operator can tick).
These tests keep them, and the menu's module gates, in step."""
import re
from pathlib import Path

import pytest

from apps.core.models import ALL_MODULES, CRM_MODULES, Hospital, default_enabled_modules

FRONTEND = Path(__file__).resolve().parents[3] / "Frontend" / "src"


def _frontend_modules():
    text = (FRONTEND / "features" / "saas" / "TenantModulesModal.tsx").read_text(encoding="utf-8")
    block = text[text.index("const CRM_MODULES"):text.index("export const SYSTEM_MODULES")]
    return re.findall(r'\{ key: "([a-z_]+)", name:', block)


def test_saas_console_offers_exactly_the_backend_modules():
    offered = _frontend_modules()
    assert len(offered) == len(set(offered)), "duplicate module in the SaaS console list"
    assert set(offered) == set(ALL_MODULES)
    text = (FRONTEND / "features" / "saas" / "TenantModulesModal.tsx").read_text(encoding="utf-8")
    crm_block = text[text.index("const CRM_MODULES"):text.index("const HMS_MODULES")]
    assert set(re.findall(r'\{ key: "([a-z_]+)", name:', crm_block)) == set(CRM_MODULES)  # right suite


def test_every_menu_module_gate_is_a_real_module():
    nav = (FRONTEND / "app" / "navConfig.ts").read_text(encoding="utf-8")
    gates = set()
    for m in re.finditer(r"moduleKey: (\[[^\]]*\]|\"[a-z_]+\")", nav):
        gates.update(re.findall(r'"([a-z_]+)"', m.group(1)))
    assert gates and gates <= set(ALL_MODULES), gates - set(ALL_MODULES)


@pytest.mark.django_db
def test_new_hospitals_get_every_module_and_the_saas_api_accepts_new_keys(saas_admin_client):
    assert default_enabled_modules() == list(ALL_MODULES)
    h = Hospital.objects.create(name="Module Test Hospital", slug="module-test")
    res = saas_admin_client.patch(f"/api/v1/saas-admin/hospitals/{h.pk}/modules/", {"enabled_modules": ["opd", "cathlab", "schemes", "enquiries", "crm", "not-a-module"]}, format="json")
    assert res.status_code == 200, res.data
    h.refresh_from_db()
    assert h.enabled_modules == ["opd", "cathlab", "schemes", "enquiries"]  # unknown keys dropped
