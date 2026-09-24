from rest_framework.routers import DefaultRouter
from .views import ExpenseViewSet, LedgerViewSet, ReceivableViewSet

router = DefaultRouter()
router.register(r"ledger", LedgerViewSet, basename="ledger")
router.register(r"expenses", ExpenseViewSet, basename="expense")
router.register(r"receivables", ReceivableViewSet, basename="receivable")

from django.urls import path  # noqa: E402

from . import fpm_views  # noqa: E402

router.register(r"vendors", fpm_views.VendorViewSet, basename="vendor")
router.register(r"vendor-invoices", fpm_views.VendorInvoiceViewSet, basename="vendorinvoice")
router.register(r"supplier-notes", fpm_views.SupplierNoteViewSet, basename="suppliernote")
router.register(r"accounts", fpm_views.LedgerAccountViewSet, basename="ledgeraccount")
router.register(r"journal", fpm_views.JournalEntryViewSet, basename="journalentry")
router.register(r"tariff", fpm_views.ServiceTariffViewSet, basename="servicetariff")
router.register(r"insurance-policies", fpm_views.InsurancePolicyViewSet, basename="insurancepolicy")
router.register(r"claim-settlements", fpm_views.ClaimSettlementViewSet, basename="claimsettlement")

urlpatterns = router.urls + [
    path("trial-balance/", fpm_views.TrialBalanceView.as_view(), name="trial-balance"),
    path("tally-export/", fpm_views.TallyExportView.as_view(), name="tally-export"),
    path("gst/", fpm_views.GSTReportView.as_view(), name="gst-report"),
    path("patients/<int:patient_id>/statement/", fpm_views.PatientStatementView.as_view(), name="patient-statement"),
    path("tpa-dashboard/", fpm_views.TPADashboardView.as_view(), name="tpa-dashboard"),
]
