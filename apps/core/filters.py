"""Search that keeps working over encrypted patient names and emails.

DRF's SearchFilter runs icontains against each search field, which silently
matches nothing on an encrypted column. This drop-in replacement routes any
search field that points at a patient's first/last name (directly, or through
relations such as "patient__first_name") to the patient's name_search prefix
hashes, and an email field to its exact-match hash; every other field is
searched as usual.
"""
from django.core.exceptions import FieldDoesNotExist
from django.db.models import Q
from rest_framework.filters import SearchFilter

from .encryption import email_index, name_query_tokens

NAME_FIELDS = {"first_name", "last_name"}


def _model_at(model, path):
    for part in [p for p in path.split("__") if p]:
        try:
            field = model._meta.get_field(part)
        except FieldDoesNotExist:
            return None
        model = getattr(field, "related_model", None)
        if model is None:
            return None
    return model


class EncryptedAwareSearchFilter(SearchFilter):
    def filter_queryset(self, request, queryset, view):
        from apps.patients.models import Patient

        search_fields = self.get_search_fields(view, request)
        terms = self.get_search_terms(request)
        if not search_fields or not terms:
            return queryset

        plain, name_paths, email_paths = [], set(), set()
        for field in search_fields:
            bare = field.lstrip("^=@$")
            prefix, _, last = bare.rpartition("__")
            if (last in NAME_FIELDS or last == "email") and _model_at(queryset.model, prefix) is Patient:
                (name_paths if last in NAME_FIELDS else email_paths).add(prefix + "__" if prefix else "")
            else:
                plain.append(field)

        lookups = [self.construct_search(str(f), queryset) for f in plain]
        for term in terms:
            condition = Q()
            for lookup in lookups:
                condition |= Q(**{lookup: term})
            tokens = name_query_tokens(term)
            for path in name_paths:
                if tokens:
                    name_q = Q()
                    for token in tokens:
                        name_q &= Q(**{f"{path}name_search__contains": f" {token} "})
                    condition |= name_q
            digest = email_index(term)
            for path in email_paths:
                if digest:
                    condition |= Q(**{f"{path}email_hash": digest})
            queryset = queryset.filter(condition)

        if any("__" in f for f in plain) or any(name_paths | email_paths):
            queryset = queryset.distinct()
        return queryset
