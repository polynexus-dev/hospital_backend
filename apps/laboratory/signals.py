import django.dispatch

# Fired by apps.laboratory.views.LabResultViewSet whenever a saved result's
# flag is "critical" — apps.automation subscribes to create an urgent task
# for the attending clinician, same pattern as apps.appointments.signals'
# appointment_checked_in / appointment_no_show (see
# docs/erp/05-integration-architecture.md). Fires on every save that leaves
# the result critical, not just the first time — for a safety alert,
# over-notifying on a re-save is a far smaller problem than under-notifying.
result_critical = django.dispatch.Signal()
