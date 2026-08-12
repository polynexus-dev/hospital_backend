from rest_framework.pagination import PageNumberPagination


class StandardResultsPagination(PageNumberPagination):
    """Default pagination for the whole API. The stock PageNumberPagination
    ignores any client-supplied page_size unless page_size_query_param is
    set — several frontend screens (e.g. the Enquiries pipeline board, the
    Appointments calendar grid) need a full day/board's worth of rows in
    one request, not the default page of 25."""

    page_size_query_param = "page_size"
    max_page_size = 500
