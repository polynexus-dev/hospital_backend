from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0018_role_template_help")]

    operations = [
        migrations.AddField(
            model_name="user",
            name="saas_role",
            field=models.CharField(blank=True, choices=[
                ("saas_owner", "SaaS Owner"), ("platform_admin", "Platform Admin"),
                ("support_l1", "Support L1"), ("support_l2", "Support L2"),
                ("support_lead", "Support Lead"), ("billing", "Billing / Finance"),
                ("customer_success", "Customer Success"),
                ("security_auditor", "Security / Compliance Auditor"),
                ("devops", "DevOps / Engineering"),
            ], help_text="Platform-company role. Assigned and changed only by the SaaS Owner.", max_length=32),
        ),
    ]
