"""Errors safe to show to a caller, without remote response bodies or headers."""


class BlackboardError(Exception):
    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status

    def as_dict(self) -> dict:
        return {"state": self.code, "message": str(self), "http_status": self.status}
