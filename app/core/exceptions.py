from __future__ import annotations


class AppError(Exception):
    def __init__(self, message: str, code: str = "app_error", status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


class PermissionDenied(AppError):
    def __init__(self, message: str = "权限不足"):
        super().__init__(message=message, code="permission_denied", status_code=403)


class InvalidStateTransition(AppError):
    def __init__(self, message: str):
        super().__init__(message=message, code="invalid_state_transition", status_code=400)


class ValidationFailed(AppError):
    def __init__(self, message: str):
        super().__init__(message=message, code="validation_failed", status_code=422)
