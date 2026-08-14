import django.dispatch

# Fired by apps.ipd.services.discharge_patient. No receiver lives in this
# app yet — Phase 6/7 (feedback's post-discharge NPS survey, billing's
# final-bill trigger) will subscribe to this the same way apps.feedback
# already subscribes to apps.appointments.signals.appointment_completed
# (see docs/erp/05-integration-architecture.md). A bare Signal() needs no
# AppConfig.ready() wiring by itself — that's only needed by the *consuming*
# app, to register its own @receiver.
patient_discharged = django.dispatch.Signal()
