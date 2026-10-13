"""Search that keeps working over encrypted personal data.

DRF's SearchFilter runs icontains against each search field, which silently
matches nothing on an encrypted column. This drop-in replacement looks at the
model each search field points to (directly, or through relations such as
"patient__first_name"): if that model declares the field in SEARCH_INDEXES
(apps.core.encryption.fill_search_indexes), names are matched on word-prefix
hashes and phone numbers / emails on exact-match hashes. Every other field is
searched as usual.
"""
from django.core.exceptions import FieldDoesNotExist
from django.db.models import Q
from rest_framework.filters import SearchFilter

from .encryption import blind_index, email_index, name_query_tokens, normalize_phone, search_columns


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


def encrypted_term_q(prefix: str, kind: str, column: str, term: str) -> Q | None:
    if kind == "name":
        tokens = name_query_tokens(term)
        if not tokens:
            return None
        q = Q()
        for token in tokens:
            q &= Q(**{f"{prefix}{column}__contains": f" {token} "})
        return q
    digest = email_index(term) if kind == "email" else blind_index(normalize_phone(term)) if sum(c.isdigit() for c in term) >= 6 else ""
    return Q(**{f"{prefix}{column}": digest}) if digest else None


class EncryptedAwareSearchFilter(SearchFilter):
    def filter_queryset(self, request, queryset, view):
        search_fields = self.get_search_fields(view, request)
        terms = self.get_search_terms(request)
        if not search_fields or not terms:
            return queryset

        plain, encrypted = [], []
        for field in search_fields:
            bare = field.lstrip("^=@$")
            prefix, _, last = bare.rpartition("__")
            model = _model_at(queryset.model, prefix)
            column = search_columns(model).get(last) if model is not None else None
            if column:
                encrypted.append((prefix + "__" if prefix else "", *column))
            else:
                plain.append(field)

        lookups = [self.construct_search(str(f), queryset) for f in plain]
        for term in terms:
            condition = Q()
            for lookup in lookups:
                condition |= Q(**{lookup: term})
            for prefix, kind, column in set(encrypted):
                q = encrypted_term_q(prefix, kind, column, term)
                if q is not None:
                    condition |= q
            queryset = queryset.filter(condition)

        if any("__" in f for f in plain) or any(p for p, _, _ in encrypted):
            queryset = queryset.distinct()
        return queryset
