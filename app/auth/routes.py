import base64
import json
from typing import Optional
from urllib.parse import urlencode

from flask import Blueprint, current_app, redirect, render_template, request, session, url_for

from app.rms_client.client import RMSClient
from app.rms_errors import safe_next_path


bp = Blueprint("auth", __name__)


@bp.get("/login")
def login():
    context = _login_context()
    if current_app.config["DEV_AUTH_STUB"]:
        return render_template("auth/login.html", dev_stub=True, keycloak_url=None, **context)

    keycloak_url = build_keycloak_auth_url()
    if not keycloak_url:
        return render_template("auth/login.html", dev_stub=False, keycloak_url=None, **context)
    return redirect(keycloak_url)


@bp.get("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return redirect(url_for("auth.login"))

    if current_app.config["DEV_AUTH_STUB"]:
        session["access_token"] = f"stub-token-for-{code}"
        session["roles"] = ["super_admin"]
        return redirect(url_for("service_centers.list_page"))

    client = RMSClient()
    token_response = client.post("/api/v1/auth/code", json={"code": code})
    if not token_response.ok:
        return render_template(
            "auth/login.html",
            dev_stub=False,
            keycloak_url=build_keycloak_auth_url(),
            **_login_context(),
            error=f"Не удалось получить токен: {token_response.error or 'unknown error'}",
        )

    token_data = token_response.data.get("data", {})
    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    if not access_token:
        return render_template(
            "auth/login.html",
            dev_stub=False,
            keycloak_url=build_keycloak_auth_url(),
            **_login_context(),
            error="RMS не вернул access_token",
        )

    session["access_token"] = access_token
    if refresh_token:
        session["refresh_token"] = refresh_token
    session["roles"] = extract_roles_from_token(access_token)
    return redirect(url_for("service_centers.list_page"))


@bp.post("/login/token")
def login_with_token():
    raw_token = (request.form.get("access_token") or "").strip()
    if not raw_token:
        return render_template(
            "auth/login.html",
            dev_stub=current_app.config["DEV_AUTH_STUB"],
            keycloak_url=build_keycloak_auth_url(),
            **_login_context(),
            error="Введите access token",
        )

    token = raw_token.replace("Bearer ", "").strip()
    session["access_token"] = token
    session["roles"] = extract_roles_from_token(token)
    return redirect(url_for("service_centers.list_page"))


@bp.post("/session/token")
def update_session_token():
    """Обновить access token в сессии с текущей страницы (после ошибки RMS)."""
    raw_token = (request.form.get("access_token") or "").strip()
    nxt = safe_next_path(request.form.get("next"))
    if not raw_token:
        return redirect(nxt or url_for("service_centers.list_page"))
    token = raw_token.replace("Bearer ", "").strip()
    if not token:
        return redirect(nxt or url_for("service_centers.list_page"))
    session["access_token"] = token
    session["roles"] = extract_roles_from_token(token)
    return redirect(nxt or url_for("service_centers.list_page"))


@bp.post("/login/environment")
def select_environment():
    env_name = (request.form.get("environment") or "").strip().lower()
    env_map = {
        "dev": current_app.config.get("RMS_API_BASE_URL_DEV", "").strip(),
        "prod": current_app.config.get("RMS_API_BASE_URL_PROD", "").strip(),
        "custom": (request.form.get("custom_rms_api_base_url") or "").strip(),
    }
    selected_url = env_map.get(env_name, "")
    if not selected_url:
        return render_template(
            "auth/login.html",
            dev_stub=current_app.config["DEV_AUTH_STUB"],
            keycloak_url=build_keycloak_auth_url(),
            **_login_context(),
            error="Не задан URL для выбранного окружения",
        )

    session["rms_api_base_url"] = selected_url
    session["rms_environment"] = env_name
    return redirect(url_for("auth.login"))


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


def build_keycloak_auth_url() -> Optional[str]:
    server_url = current_app.config["KEYCLOAK_SERVER_URL"].rstrip("/")
    realm = current_app.config["KEYCLOAK_REALM"]
    client_id = current_app.config["KEYCLOAK_CLIENT_ID"]
    redirect_uri = current_app.config["KEYCLOAK_REDIRECT_URI"]
    if not (server_url and realm and client_id and redirect_uri):
        return None

    query = urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "scope": "openid",
            "redirect_uri": redirect_uri,
        }
    )
    return f"{server_url}/realms/{realm}/protocol/openid-connect/auth?{query}"


def extract_roles_from_token(access_token: str) -> list[str]:
    parts = access_token.split(".")
    if len(parts) < 2:
        return ["analyst"]

    payload = parts[1]
    padding = "=" * ((4 - len(payload) % 4) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload + padding).decode("utf-8")
        claims = json.loads(decoded)
    except (ValueError, json.JSONDecodeError):
        return ["analyst"]

    realm_roles = claims.get("realm_access", {}).get("roles", [])
    mapped = []
    if "super_admin" in realm_roles:
        mapped.append("super_admin")
    if "franchise_manager" in realm_roles:
        mapped.append("franchise_manager")
    if "sc_operator" in realm_roles:
        mapped.append("sc_operator")
    if "analyst" in realm_roles:
        mapped.append("analyst")
    return mapped or ["analyst"]


def _login_context() -> dict:
    dev_url = current_app.config.get("RMS_API_BASE_URL_DEV", "")
    prod_url = current_app.config.get("RMS_API_BASE_URL_PROD", "")
    selected_base_url = session.get("rms_api_base_url") or current_app.config["RMS_API_BASE_URL"]
    selected_env = session.get("rms_environment", "default")
    return {
        "allow_manual_token": True,
        "rms_env_options": {
            "dev": dev_url,
            "prod": prod_url,
            "default": current_app.config["RMS_API_BASE_URL"],
        },
        "selected_rms_base_url": selected_base_url,
        "selected_rms_environment": selected_env,
    }
