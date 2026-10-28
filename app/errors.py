"""Business-rule failures whose message is safe to show to the caller."""


class ServiceError(Exception):
    status_code = 400

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class NotFoundError(ServiceError):
    status_code = 404


class ValidationFailed(ServiceError):
    status_code = 422


class RateLimitedError(ServiceError):
    status_code = 429
