from typing import Any, Dict, List, Optional

from flask import Blueprint, redirect, render_template, request, url_for

from app.list_limits import RMS_LIST_PAGE_LIMIT
from app.rbac.decorators import has_permission, require_any_permission, require_permission
from app.rms_client.client import RMSClient
from app.service_center_catalog import (
    fetch_service_center_options,
    fetch_service_center_table_row,
    resolve_unassigned_sc_options,
)


bp = Blueprint("franchisees", __name__, url_prefix="/franchisees")


def _redirect_list(message: Optional[str] = None, msg_type: str = "info") -> str:
    qs: Dict[str, str] = {}
    if message:
        qs["fr_msg"] = message
        qs["fr_msg_type"] = msg_type
    return url_for("franchisees.list_page", **qs)


def _redirect_detail(uid: str, message: Optional[str] = None, msg_type: str = "info") -> str:
    qs: Dict[str, str] = {"uid": uid}
    if message:
        qs["fr_msg"] = message
        qs["fr_msg_type"] = msg_type
    return url_for("franchisees.detail_page", **qs)


@bp.get("")
@require_permission("franchisee.read")
def list_page():
    name = (request.args.get("name") or "").strip()
    org_id_raw = (request.args.get("org_id") or "").strip()
    user_id = (request.args.get("user_id") or "").strip()
    cursor = (request.args.get("cursor") or "").strip()
    show_deleted = request.args.get("show_deleted") == "1"
    params: Dict[str, Any] = {"limit": RMS_LIST_PAGE_LIMIT}
    if name:
        params["name"] = name
    if org_id_raw:
        try:
            params["org_id"] = int(org_id_raw)
        except ValueError:
            pass
    if user_id:
        params["user_id"] = user_id
    if cursor:
        params["cursor"] = cursor
    if show_deleted:
        params["is_deleted"] = "true"

    client = RMSClient()
    response = client.get("/api/v1/franchisee", params=params)

    if response.ok:
        items = _extract_franchisees(response.data)
        page = _extract_page_metadata(response.data)
        error = None
    elif response.status_code == 404:
        items = []
        page = {"next_cursor": "", "has_more": False}
        error = None
    else:
        items = []
        page = {"next_cursor": "", "has_more": False}
        error = response.error

    return render_template(
        "franchisees/list.html",
        items=items,
        filters={
            "name": name,
            "org_id": org_id_raw,
            "user_id": user_id,
            "show_deleted": show_deleted,
        },
        page=page,
        error=error,
        fr_msg=request.args.get("fr_msg"),
        fr_msg_type=request.args.get("fr_msg_type") or "info",
        can_create=has_permission("franchisee.create"),
    )


@bp.get("/new")
@require_permission("franchisee.create")
def new_page():
    return render_template("franchisees/new.html")


@bp.post("/new")
@require_permission("franchisee.create")
def create_submit():
    name = (request.form.get("name") or "").strip()
    org_id_raw = (request.form.get("org_id") or "").strip()
    user_id = (request.form.get("user_id") or "").strip()
    if not name:
        return render_template(
            "franchisees/new.html",
            form_error="Укажите название",
            form_values=request.form,
        )

    body: Dict[str, Any] = {"name": name}
    if org_id_raw:
        try:
            body["org_id"] = int(org_id_raw)
        except ValueError:
            return render_template(
                "franchisees/new.html",
                form_error="Некорректный org_id",
                form_values=request.form,
            )
    if user_id:
        body["user_id"] = user_id

    client = RMSClient()
    resp = client.post("/api/v1/franchisee", json=body)
    if not resp.ok:
        return render_template(
            "franchisees/new.html",
            form_error=resp.error or "Ошибка создания",
            form_values=request.form,
        )

    fid = resp.data.get("franchisee_id") if isinstance(resp.data, dict) else None
    uid_str = str(fid) if fid else ""
    if uid_str:
        return redirect(_redirect_detail(uid_str, "Франчайзи создан", "success"))
    return redirect(_redirect_list("Создано, но ответ API без franchisee_id", "error"))


@bp.get("/<uid>")
@require_permission("franchisee.read")
def detail_page(uid: str):
    client = RMSClient()
    response = client.get(f"/api/v1/franchisee/{uid}")
    can_update = has_permission("franchisee.update")
    can_delete = has_permission("franchisee.delete")
    # Просмотр СЦ (analyst и др.) — тоже показываем привязку; POST защищен тем же набором прав.
    can_attach_sc = (
        can_update
        or has_permission("service_center.update")
        or has_permission("service_center.read")
    )
    if not response.ok:
        return render_template(
            "franchisees/detail.html",
            uid=uid,
            franchisee=None,
            service_center_uids=[],
            attached_sc_rows=[],
            sc_attach_options=[],
            error=response.error,
            fr_msg=request.args.get("fr_msg"),
            fr_msg_type=request.args.get("fr_msg_type") or "info",
            can_update=can_update,
            can_attach_sc=can_attach_sc,
            can_delete=can_delete,
        )
    franchisee = _extract_franchisee_detail(response.data)
    sc_raw = franchisee.get("service_centers") if isinstance(franchisee, dict) else None
    service_center_uids = [str(x) for x in sc_raw] if isinstance(sc_raw, list) else []
    if can_attach_sc:
        all_sc_options = fetch_service_center_options(client)
        attached_set = {x for x in service_center_uids if x}
        sc_attach_options = [
            o
            for o in resolve_unassigned_sc_options(client, cache_all=all_sc_options)
            if str(o.get("uid") or "").strip() not in attached_set
        ]
    else:
        sc_attach_options = []
    attached_sc_rows: List[Dict[str, Any]] = [
        fetch_service_center_table_row(client, u) for u in service_center_uids
    ]
    return render_template(
        "franchisees/detail.html",
        uid=uid,
        franchisee=franchisee,
        service_center_uids=service_center_uids,
        attached_sc_rows=attached_sc_rows,
        sc_attach_options=sc_attach_options,
        error=None,
        fr_msg=request.args.get("fr_msg"),
        fr_msg_type=request.args.get("fr_msg_type") or "info",
        can_update=can_update,
        can_attach_sc=can_attach_sc,
        can_delete=can_delete,
    )


@bp.post("/<uid>/update")
@require_permission("franchisee.update")
def update_submit(uid: str):
    name = (request.form.get("name") or "").strip()
    org_id_raw = (request.form.get("org_id") or "").strip()
    user_id = (request.form.get("user_id") or "").strip()
    if not name:
        return redirect(_redirect_detail(uid, "Название обязательно", "error"))

    body: Dict[str, Any] = {"name": name}
    if org_id_raw:
        try:
            body["org_id"] = int(org_id_raw)
        except ValueError:
            return redirect(_redirect_detail(uid, "Некорректный org_id", "error"))
    else:
        body["org_id"] = None
    body["user_id"] = user_id or None

    client = RMSClient()
    resp = client.patch(f"/api/v1/franchisee/{uid}", json=body)
    if resp.ok:
        return redirect(_redirect_detail(uid, "Сохранено", "success"))
    return redirect(_redirect_detail(uid, resp.error or "Ошибка сохранения", "error"))


@bp.post("/<uid>/delete")
@require_permission("franchisee.delete")
def delete_submit(uid: str):
    client = RMSClient()
    resp = client.delete(f"/api/v1/franchisee/{uid}")
    if resp.ok:
        return redirect(_redirect_list("Франчайзи удалён", "success"))
    return redirect(_redirect_detail(uid, resp.error or "Ошибка удаления", "error"))


@bp.post("/<uid>/attach_service_centers")
@require_any_permission("franchisee.update", "service_center.update", "service_center.read")
def attach_service_centers(uid: str):
    uids = [str(x).strip() for x in request.form.getlist("service_center_ids") if str(x).strip()]
    if not uids:
        return redirect(_redirect_detail(uid, "Выберите хотя бы один сервисный центр", "error"))
    payload: List[Dict[str, str]] = [{"service_center_id": t} for t in uids]
    client = RMSClient()
    resp = client.post(f"/api/v1/franchisee/{uid}/service_center", json=payload)
    if resp.ok:
        return redirect(_redirect_detail(uid, "Сервисные центры закреплены", "success"))
    return redirect(_redirect_detail(uid, resp.error or "Ошибка закрепления СЦ", "error"))


def _extract_page_metadata(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {"next_cursor": "", "has_more": False}
    meta = payload.get("metadata", {})
    if not isinstance(meta, dict):
        return {"next_cursor": "", "has_more": False}
    return {
        "next_cursor": str(meta.get("cursor") or ""),
        "has_more": bool(meta.get("has_more")),
    }


def _extract_franchisees(payload: Any) -> list:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []

    data = payload.get("data", {})
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("franchisees", "items", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def _extract_franchisee_detail(payload: Any) -> dict:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    return data if isinstance(data, dict) else {}
