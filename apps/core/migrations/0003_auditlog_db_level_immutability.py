from django.db import migrations


FORWARD_SQL = """
CREATE OR REPLACE FUNCTION core_auditlog_block_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'core_auditlog rows are immutable: % is not permitted (id=%)', TG_OP, OLD.id
        USING ERRCODE = '23514';  -- check_violation, so Django/psycopg surfaces this as
                                   -- IntegrityError (a business-rule violation) rather than
                                   -- the default 'raise_exception' code, which Django maps to
                                   -- ProgrammingError — the wrong signal for callers, since
                                   -- that class normally means a malformed query, not a
                                   -- deliberately rejected one.
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER core_auditlog_no_update
    BEFORE UPDATE ON core_auditlog
    FOR EACH ROW EXECUTE FUNCTION core_auditlog_block_mutation();

CREATE TRIGGER core_auditlog_no_delete
    BEFORE DELETE ON core_auditlog
    FOR EACH ROW EXECUTE FUNCTION core_auditlog_block_mutation();
"""

REVERSE_SQL = """
DROP TRIGGER IF EXISTS core_auditlog_no_update ON core_auditlog;
DROP TRIGGER IF EXISTS core_auditlog_no_delete ON core_auditlog;
DROP FUNCTION IF EXISTS core_auditlog_block_mutation();
"""


def apply_postgres_triggers(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute(FORWARD_SQL)


def reverse_postgres_triggers(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute(REVERSE_SQL)


class Migration(migrations.Migration):
    """AuditLog.save()/delete() already refuse updates/deletes at the
    Django ORM layer — but that only holds for code that goes through the
    model. A raw SQL UPDATE/DELETE (a compromised DB credential, a
    careless `python manage.py dbshell`, a future developer bypassing the
    ORM for a "quick fix") would sail straight past that guard. This
    migration enforces the same rule at the database engine itself via a
    trigger, so it holds regardless of what's issuing the query.

    Postgres-specific (this project's stated production database — see
    Backend/README.md and DATABASE_URL in .env.example); conditionally
    skipped on SQLite for local development and unit testing."""

    dependencies = [
        ("core", "0002_hospital_google_review_url_and_more"),
    ]

    operations = [
        migrations.RunPython(apply_postgres_triggers, reverse_postgres_triggers),
    ]

