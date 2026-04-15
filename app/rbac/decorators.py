from functools import wraps

from flask import abort, session

from app.rbac.permissions import ROLE_PERMISSIONS


def has_permission(required_permission: str) -> bool:
    roles = session.get("roles", [])
    for role in roles:
        allowed = ROLE_PERMISSIONS.get(role, set())
        if "*" in allowed or required_permission in allowed:
            return True
    return False


def require_permission(permission: str):
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not has_permission(permission):
                abort(403)
            return fn(*args, **kwargs)

        return wrapped

    return decorator


def require_any_permission(*permissions: str):
    """Доступ, если есть хотя бы одно из перечисленных прав."""

    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not any(has_permission(p) for p in permissions):
                abort(403)
            return fn(*args, **kwargs)

        return wrapped

    return decorator
