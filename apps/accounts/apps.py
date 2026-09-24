from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"

    def ready(self):
        from django.db.models.signals import post_migrate

        post_migrate.connect(_sync_roles_after_migrate, dispatch_uid="accounts_sync_role_permissions")


def _sync_roles_after_migrate(sender, app_config=None, **kwargs):
    """Once per `migrate`, after the last app's permissions exist: give
    existing roles their template's permissions for newly added models."""
    from django.apps import apps

    if sender is not list(apps.get_app_configs())[-1]:
        return
    from .role_sync import sync_all_roles

    sync_all_roles(stdout=kwargs.get("stdout") if kwargs.get("verbosity", 1) else None)
