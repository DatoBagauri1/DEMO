import contextvars
import uuid


_request_context: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "request_context",
    default={},
)


def set_request_context(**kwargs: str) -> None:
    current = _request_context.get({}).copy()
    current.update({k: v for k, v in kwargs.items() if v})
    _request_context.set(current)


def clear_request_context() -> None:
    _request_context.set({})


def new_request_id() -> str:
    return uuid.uuid4().hex


class RequestContextFilter:
    def filter(self, record) -> bool:  # noqa: ANN001
        context = _request_context.get({})
        record.request_id = context.get("request_id", "-")
        record.plan_id = context.get("plan_id", "-")
        return True

