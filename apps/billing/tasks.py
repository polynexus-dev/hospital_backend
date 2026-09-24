from celery import shared_task


@shared_task
def post_bed_charges_nightly():
    """Brings every current inpatient's running bill up to date with bed-days."""
    from .bed_charges import post_all_open_admissions

    return post_all_open_admissions()
