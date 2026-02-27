from trip_pilot.logging import clear_request_context, new_request_id, set_request_context


class RequestContextMiddleware:
    def __init__(self, get_response):  # noqa: ANN001
        self.get_response = get_response

    def __call__(self, request):  # noqa: ANN001
        request_id = request.headers.get("X-Request-ID", new_request_id())
        set_request_context(request_id=request_id)
        try:
            response = self.get_response(request)
            response["X-Request-ID"] = request_id
            return response
        finally:
            clear_request_context()

