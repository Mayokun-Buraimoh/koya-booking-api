"""Domain errors. Each carries the HTTP status it maps to; views turn them into {"error": ...}."""


class ApiError(Exception):
    status = 500

    def __init__(self, message, headers=None):
        super().__init__(message)
        self.message = message
        self.headers = headers or {}


class BadRequest(ApiError):
    status = 400


class NotFound(ApiError):
    status = 404


class Conflict(ApiError):
    status = 409


class Unprocessable(ApiError):
    status = 422


class RateLimited(ApiError):
    status = 429
