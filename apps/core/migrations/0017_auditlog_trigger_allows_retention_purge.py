from django.db import migrations

# Same immutability trigger as 0003, with one sanctioned exception: the
# DAC.2.c retention job (apps.governance.services.purge_expired_audit_logs)
# deletes archived rows after `SET LOCAL hms.audit_purge = 'on'`, which only
# lasts until the end of its own transaction. UPDATE stays forbidden for
# everyone, and any other DELETE (raw SQL, dbshell) is still refused.

FORWARD_SQL = """
CREATE OR REPLACE FUNCTION core_auditlog_block_mutation() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' AND current_setting('hms.audit_purge', true) = 'on' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'core_auditlog rows are immutable: %% is not permitted (id=%%)', TG_OP, OLD.id
        USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;
"""

REVERSE_SQL = """
CREATE OR REPLACE FUNCTION core_auditlog_block_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'core_auditlog rows are immutable: %% is not permitted (id=%%)', TG_OP, OLD.id
        USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;
"""


def forward(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(FORWARD_SQL)


def reverse(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(REVERSE_SQL)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0016_auditlog_refs_survive_deletes"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
