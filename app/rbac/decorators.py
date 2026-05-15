import base64
import json
from functools import wraps

from flask import abort, session

from app.rbac.permissions import ROLE_PERMISSIONS


def _extract_token_roles() -> set[str]:
    raw_token = str(session.get("access_token") or "").strip()
    if not raw_token:
        return set()
    parts = raw_token.split(".")
    if len(parts) < 2:
        return set()
    payload = parts[1]
    padding = "=" * ((4 - len(payload) % 4) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload + padding).decode("utf-8")
        claims = json.loads(decoded)
    except (ValueError, json.JSONDecodeError):
        return set()

    roles: set[str] = set()
    rms_roles = claims.get("rms-roles", [])
    if isinstance(rms_roles, list):
        roles.update(str(r) for r in rms_roles if isinstance(r, str))

    resource_access = claims.get("resource_access", {})
    if isinstance(resource_access, dict):
        rms_api = resource_access.get("rms-api", {})
        if isinstance(rms_api, dict):
            raw = rms_api.get("roles", [])
            if isinstance(raw, list):
                roles.update(str(r) for r in raw if isinstance(r, str))
    return roles


def _token_allows_permission(required_permission: str) -> bool:
    token_roles = _extract_token_roles()
    if not token_roles:
        return False
    if any(
        role in {"rms-api:*:*", "rms-api::*", "rms-api:*"}
        or role.startswith("rms-api:*:")
        or role.startswith("rms-api::")
        for role in token_roles
    ):
        return True
    # Минимальный bridge для CRUD-прав сервисных центров через RMS wildcard namespaces.
    if required_permission.startswith("service_center.") and any(
        role.startswith("rms-api:service_center") for role in token_roles
    ):
        return True
    return False


def has_permission(required_permission: str) -> bool:
    roles = session.get("roles", [])
    for role in roles:
        allowed = ROLE_PERMISSIONS.get(role, set())
        if "*" in allowed or required_permission in allowed:
            return True
    if _token_allows_permission(required_permission):
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
