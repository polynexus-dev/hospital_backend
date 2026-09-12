"""
Lead-quality scoring for `Enquiry.score` (0-100, "feeds hot/warm/cold
sorting on the pipeline board" — see the field's own comment in models.py).
Two tiers, always tried in this order:

1. A trained model — a scikit-learn logistic regression, fit fresh from
   this hospital's own closed enquiries every time `recompute_hospital_scores`
   runs. Trained strictly per-hospital, never across hospitals: lead
   quality genuinely differs by hospital, specialty mix, and local market,
   so pooling hospitals wouldn't just add noise, it would leak one
   hospital's lead patterns into another's scoring — the same tenant-
   isolation boundary every other model in this codebase respects (see
   apps.core.tenancy).
2. A heuristic fallback — fixed, documented point weights over the same
   signals the model uses. This is what a hospital gets on day one, before
   it has enough closed-enquiry history to learn from, and it's also what
   any hospital falls back to if training fails for any reason (a
   single-class training set, missing scikit-learn, anything else) — same
   fail-safe philosophy as apps.communications.llm_router's degrade-to-
   "unclear": a scoring problem must never become a save-the-enquiry
   problem.

Nothing here is persisted between runs — retraining from scratch on every
nightly pass is cheap at real hospital enquiry volumes (hundreds to low
thousands of rows, not millions) and avoids storing a pickled model, which
would be a real deserialization-security surface for no benefit at this
scale.
"""
from apps.enquiries.models import Enquiry

# Below this many closed (converted or lost) enquiries, there isn't enough
# signal to fit anything meaningful — a brand-new or low-volume hospital
# just gets the heuristic indefinitely, not a model overfit to a handful
# of rows.
MIN_TRAINING_SAMPLES = 50

# Enquiries in any OTHER stage are still open — they haven't resolved
# either way yet, so they carry no learnable outcome and are excluded from
# training entirely (they're the ones GETTING scored, not scored on).
_CONVERTED_STAGES = [Enquiry.Stage.COMPLETED, Enquiry.Stage.VISITED]
_LOST_STAGE = Enquiry.Stage.LOST

_URGENCY_POINTS = {
    Enquiry.Urgency.LOW: 0,
    Enquiry.Urgency.NORMAL: 1,
    Enquiry.Urgency.HIGH: 2,
    Enquiry.Urgency.URGENT: 3,
}
_SOURCES = [choice[0] for choice in Enquiry.Source.choices]
_NUMERIC_FEATURES = ["urgency", "has_department", "has_assigned_to", "has_estimated_value", "responded_within_24h"]


def _hours_to_first_response(enquiry: Enquiry) -> float | None:
    first_move = enquiry.stage_changes.order_by("created_at").first()
    if first_move is None or enquiry.created_at is None:
        return None
    return (first_move.created_at - enquiry.created_at).total_seconds() / 3600


def extract_features(enquiry: Enquiry) -> dict:
    """One shared signal set for both the heuristic and the trained
    model, so a score never depends on which path computed it."""
    response_hours = _hours_to_first_response(enquiry)
    return {
        "urgency": _URGENCY_POINTS.get(enquiry.urgency, 1),
        "has_department": 1 if enquiry.department_id else 0,
        "has_assigned_to": 1 if enquiry.assigned_to_id else 0,
        "has_estimated_value": 1 if enquiry.estimated_value else 0,
        "responded_within_24h": 1 if (response_hours is not None and response_hours <= 24) else 0,
        "source": enquiry.source,
    }


def _heuristic_score(enquiry: Enquiry) -> int:
    f = extract_features(enquiry)
    points = 0
    points += f["urgency"] * 12  # low=0, normal=12, high=24, urgent=36
    points += 15 if enquiry.source in (Enquiry.Source.REFERRAL, Enquiry.Source.WALK_IN) else 0
    points += 10 * f["has_department"]
    points += 10 * f["has_assigned_to"]
    points += 15 * f["responded_within_24h"]
    points += 5 * f["has_estimated_value"]
    return max(0, min(100, points))


def _vectorize(rows: list[dict]):
    """Numeric features pass through as-is; `source` is one-hot encoded
    over the model's fixed choice list. A small manual encoder rather than
    scikit-learn's own preprocessing pipeline — there are only five
    numeric fields and one category, a Pipeline/ColumnTransformer would be
    more machinery than the problem needs."""
    import numpy as np

    matrix = []
    for row in rows:
        vec = [row[key] for key in _NUMERIC_FEATURES]
        vec += [1 if row["source"] == source else 0 for source in _SOURCES]
        matrix.append(vec)
    return np.array(matrix, dtype=float)


def _closed_enquiries(hospital):
    return Enquiry.objects.filter(
        hospital=hospital,
        stage__in=[*_CONVERTED_STAGES, _LOST_STAGE],
    ).prefetch_related("stage_changes")


def _train_model(hospital):
    """Returns a fitted classifier, or None — for too little history, a
    single-class training set (every closed enquiry converted, or none
    did — logistic regression can't learn a boundary from that), or any
    other training failure. None always means "use the heuristic
    instead", never an exception a caller has to handle."""
    closed = list(_closed_enquiries(hospital))
    if len(closed) < MIN_TRAINING_SAMPLES:
        return None

    rows = [extract_features(e) for e in closed]
    labels = [1 if e.stage in _CONVERTED_STAGES else 0 for e in closed]
    if len(set(labels)) < 2:
        return None

    try:
        from sklearn.linear_model import LogisticRegression

        model = LogisticRegression(max_iter=1000, class_weight="balanced")
        model.fit(_vectorize(rows), labels)
        return model
    except Exception:
        # Missing scikit-learn, a degenerate fit, anything else — scoring
        # falls back to the heuristic, it never breaks enquiry creation.
        return None


def score_enquiry(enquiry: Enquiry, model=None) -> int:
    """The one entry point every caller uses. Pass a pre-trained `model`
    when scoring many enquiries for the same hospital in one pass
    (`recompute_hospital_scores`) to avoid retraining per row; omit it to
    score a single enquiry on its own — e.g. right after creation, before
    there's any point training anything for just one row."""
    if model is not None:
        try:
            proba_converted = model.predict_proba(_vectorize([extract_features(enquiry)]))[0][1]
            return max(0, min(100, round(proba_converted * 100)))
        except Exception:
            pass
    return _heuristic_score(enquiry)


def recompute_hospital_scores(hospital) -> int:
    """Retrains fresh from this hospital's current closed-enquiry history
    (see module docstring for why nothing is persisted between runs) and
    rescoring every still-open enquiry. Returns the number actually
    changed."""
    from apps.enquiries.services import OPEN_STAGES

    model = _train_model(hospital)
    updated = 0
    for enquiry in Enquiry.objects.filter(hospital=hospital, stage__in=OPEN_STAGES):
        new_score = score_enquiry(enquiry, model=model)
        if new_score != enquiry.score:
            enquiry.score = new_score
            enquiry.save(update_fields=["score"])
            updated += 1
    return updated
