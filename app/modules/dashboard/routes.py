from flask import Blueprint, redirect, url_for

from app.rbac.decorators import require_permission


bp = Blueprint("dashboard", __name__)


@bp.get("/")
@require_permission("dashboard.read")
def index():
    """Дашборд с виджетами RMS отключён (слишком долго). Главная ведёт в раздел СЦ."""
    return redirect(url_for("service_centers.list_page"))
