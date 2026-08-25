from django.db import migrations


SAAS_OWNER_EMAIL = "saas_owner@hospital-crm.com"


def grant_is_saas_admin(apps, schema_editor):
    """One-time, narrowly-scoped fix for environments where
    apps.core.management.commands.seed_demo_data already ran before
    is_saas_admin existed (accounts/0011). That command's own
    idempotency guard (`if <demo data already seeded>: return`) means a
    plain redeploy never reaches the line that would otherwise set this
    flag on the already-created saas_owner account — so it has to be
    granted here instead. Deliberately scoped to this one known,
    non-customer platform-owner account, not a blanket backfill (see
    apps.saas_admin's own design notes on why every is_staff user
    shouldn't automatically become an is_saas_admin). No-ops wherever
    this account doesn't exist (a fresh environment where seed_demo_data
    hasn't run yet, or was never run at all — that path already creates
    the account correctly via the updated seed script)."""
    User = apps.get_model("accounts", "User")
    User.objects.filter(email=SAAS_OWNER_EMAIL).update(is_saas_admin=True, is_staff=True)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0011_user_is_saas_admin'),
    ]

    operations = [
        migrations.RunPython(grant_is_saas_admin, noop_reverse),
    ]
