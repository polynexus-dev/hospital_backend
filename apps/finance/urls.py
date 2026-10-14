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
from . import ar_views  # noqa: E402

router.register(r"journal", ar_views.JournalEntryViewSet, basename="journalentry")
router.register(r"customers", ar_views.CustomerViewSet, basename="customer")
router.register(r"customer-invoices", ar_views.CustomerInvoiceViewSet, basename="customerinvoice")
router.register(r"customer-receipts", ar_views.CustomerReceiptViewSet, basename="customerreceipt")
router.register(r"recurring-invoices", ar_views.RecurringInvoiceViewSet, basename="recurringinvoice")
router.register(r"tariff", fpm_views.ServiceTariffViewSet, basename="servicetariff")
router.register(r"insurance-policies", fpm_views.InsurancePolicyViewSet, basename="insurancepolicy")
router.register(r"claim-settlements", fpm_views.ClaimSettlementViewSet, basename="claimsettlement")

from . import payout_views  # noqa: E402

router.register(r"doctor-payout-rules", payout_views.DoctorPayoutRuleViewSet, basename="doctorpayoutrule")
router.register(r"doctor-payouts", payout_views.DoctorPayoutViewSet, basename="doctorpayout")
router.register(r"payout-deduction-heads", payout_views.PayoutDeductionHeadViewSet, basename="payoutdeductionhead")

urlpatterns = router.urls + [
    path("day-book/", ar_views.DayBookView.as_view(), name="day-book"),
    path("accounts-settings/", ar_views.AccountsSettingsView.as_view(), name="accounts-settings"),
    path("trial-balance/", fpm_views.TrialBalanceView.as_view(), name="trial-balance"),
    path("tally-export/", fpm_views.TallyExportView.as_view(), name="tally-export"),
    path("gst/", fpm_views.GSTReportView.as_view(), name="gst-report"),
    path("patients/<int:patient_id>/statement/", fpm_views.PatientStatementView.as_view(), name="patient-statement"),
    path("tpa-dashboard/", fpm_views.TPADashboardView.as_view(), name="tpa-dashboard"),
    path("payout-statement/<str:token>/", payout_views.PayoutStatementLinkView.as_view(), name="payout-statement-link"),
]
