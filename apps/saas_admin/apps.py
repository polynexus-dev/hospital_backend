from django.apps import AppConfig


class SaasAdminConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.saas_admin"
    verbose_name = "SaaS Platform Administration"
