from django.contrib import admin
from django.apps import apps

for _m in apps.get_app_config("dietary").get_models():
    admin.site.register(_m)
