from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, redirect, render_template, request, url_for

from app.list_limits import RMS_LIST_PAGE_LIMIT
from app.rbac.decorators import has_permission, require_permission
from app.rms_client.client import RMSClient
from app.service_center_catalog import fetch_service_center_options, label_for_sc_uid


bp = Blueprint("departments", __name__, url_prefix="/departments")


def _redirect_list(msg: Optional[str] = None, msg_type: str = "info") -> str:
    q: Dict[str, str] = {}
    if msg:
        q["dept_msg"] = msg
        q["dept_msg_type"] = msg_type
    return url_for("departments.list_page", **q)


def _parse_department_list(payload: Any) -> Tuple[List[dict], Dict[str, Any]]:
    """Разворачивает data[] { service_center_uid, departments[] } в плоские строки."""
    rows: List[dict] = []
    if not isinstance(payload, dict):
        return rows, {"next_cursor": "", "has_more": False}
    data = payload.get("data")
    if isinstance(data, list):
        for block in data:
            if not isinstance(block, dict):
                continue
            sc_uid = str(block.get("service_center_uid") or "").strip()
            depts = block.get("departments")
            if not isinstance(depts, list):
                continue
            for d in depts:
                if not isinstance(d, dict):
                    continue
                rows.append(
                    {
                        "service_center_uid": sc_uid,
                        "department_uid": str(d.get("uid") or "").strip(),
                        "name": d.get("name"),
                        "external_uid": str(d.get("external_uid") or "").strip(),
                    }
                )
    return rows, {
        "next_cursor": str(payload.get("cursor") or ""),
        "has_more": bool(payload.get("has_more")),
    }


def _fetch_departments(
    client: RMSClient,
    limit: int,
    cursor: Optional[str],
    service_center_uid: str,
    external_uid: str,
) -> Tuple[List[dict], Dict[str, Any], Optional[str]]:
    params: Dict[str, Any] = {"limit": limit}
    if cursor:
        params["cursor"] = cursor
    if service_center_uid:
        params["service_center_uid"] = service_center_uid
    if external_uid:
        params["external_uid"] = external_uid

    # В RMS маршрут списка — GET .../department/ (со слэшем). Без слэша часто 302 → слэш;
    # при редиректе requests сбрасывает Authorization и приходит ложное «token не валидный».
    resp = client.get("/api/v1/department/", params=params)
    if resp.status_code == 404:
        return [], {"next_cursor": "", "has_more": False}, None
    if not resp.ok:
        return [], {"next_cursor": "", "has_more": False}, resp.error
    rows, meta = _parse_department_list(resp.data)
    return rows, meta, None


@bp.get("")
@require_permission("department.read")
def list_page():
    sc_uid = (request.args.get("sc_uid") or "").strip()
    external_uid = (request.args.get("external_uid") or "").strip()
    cursor = (request.args.get("cursor") or "").strip()

    client = RMSClient()
    sc_options = fetch_service_center_options(client)
    selected_sc_label = (label_for_sc_uid(sc_options, sc_uid) or sc_uid) if sc_uid else ""

    limit = RMS_LIST_PAGE_LIMIT
    items: List[dict] = []
    page = {"next_cursor": "", "has_more": False}
    error: Optional[str] = None

    # RMS: без service_center_uid список — по всем СЦ с отделами; для админки достаточно опционального фильтра
    items, page, err = _fetch_departments(client, limit, cursor or None, sc_uid, external_uid)
    error = err
    sc_label_by_uid = {
        str(o.get("uid") or ""): str(o.get("label") or str(o.get("uid") or ""))
        for o in sc_options
        if isinstance(o, dict) and str(o.get("uid") or "")
    }
    for row in items:
        if not isinstance(row, dict):
            continue
        s_uid = str(row.get("service_center_uid") or "").strip()
        row["service_center_name"] = sc_label_by_uid.get(s_uid) or s_uid or "—"

    return render_template(
        "departments/list.html",
        items=items,
        page=page,
        error=error,
        sc_uid=sc_uid,
        sc_options=sc_options,
        selected_sc_label=selected_sc_label,
        external_uid=external_uid,
        dept_msg=request.args.get("dept_msg"),
        dept_msg_type=request.args.get("dept_msg_type") or "info",
        can_create=has_permission("department.create"),
        can_update=has_permission("department.update"),
        can_delete=has_permission("department.delete"),
    )


@bp.get("/new")
@require_permission("department.create")
def new_page():
    client = RMSClient()
    sc_options = fetch_service_center_options(client)
    return render_template(
        "departments/new.html",
        sc_options=sc_options,
        selected_sc_uid="",
        selected_sc_label="",
        form_error=None,
        form_values=None,
    )


@bp.post("/new")
@require_permission("department.create")
def new_submit():
    client = RMSClient()
    sc_options = fetch_service_center_options(client)
    name = (request.form.get("name") or "").strip()
    sc_uid = (request.form.get("service_center_uid") or "").strip()
    ext_uid = (request.form.get("external_uid") or "").strip()
    sc_label = (label_for_sc_uid(sc_options, sc_uid) or sc_uid) if sc_uid else ""

    def _fail(msg: str):
        return render_template(
            "departments/new.html",
            sc_options=sc_options,
            selected_sc_uid=sc_uid,
            selected_sc_label=sc_label,
            form_error=msg,
            form_values=request.form,
        )

    if not name:
        return _fail("Укажите название отдела")
    if not sc_uid:
        return _fail("Выберите сервисный центр")
    if not ext_uid:
        return _fail("Укажите external_uid (в RMS — строка в формате UUID)")

    body: Dict[str, Any] = {
        "name": name,
        "service_center_uid": sc_uid,
        "external_uid": ext_uid,
    }
    resp = client.post("/api/v1/department", json=body)
    if not resp.ok:
        return _fail(resp.error or "Ошибка создания отдела")

    new_uid = None
    if isinstance(resp.data, dict):
        inner = resp.data.get("data")
        if isinstance(inner, dict):
            new_uid = inner.get("uid")
    if new_uid:
        return redirect(
            url_for(
                "departments.list_page",
                dept_msg=f"Отдел создан (uid: {new_uid})",
                dept_msg_type="success",
            )
        )
    return redirect(_redirect_list("Отдел создан", "success"))


@bp.get("/<uid>/edit")
@require_permission("department.update")
def edit_page(uid: str):
    client = RMSClient()
    sc_options = fetch_service_center_options(client)
    fv = {
        "name": (request.args.get("prefill_name") or "").strip(),
        "service_center_uid": (request.args.get("prefill_sc") or "").strip(),
        "external_uid": (request.args.get("prefill_ext") or "").strip(),
    }
    if not any(fv.values()):
        fv = {}
    scu = fv.get("service_center_uid") or ""
    sc_label = (label_for_sc_uid(sc_options, scu) or scu) if scu else ""
    return render_template(
        "departments/edit.html",
        department_uid=uid,
        sc_options=sc_options,
        selected_sc_uid=scu,
        selected_sc_label=sc_label,
        form_error=None,
        form_values=fv if fv else None,
    )


@bp.post("/<uid>/edit")
@require_permission("department.update")
def edit_submit(uid: str):
    client = RMSClient()
    sc_options = fetch_service_center_options(client)
    name = (request.form.get("name") or "").strip()
    sc_uid = (request.form.get("service_center_uid") or "").strip()
    ext_uid = (request.form.get("external_uid") or "").strip()
    sc_label = (label_for_sc_uid(sc_options, sc_uid) or sc_uid) if sc_uid else ""

    body: Dict[str, Any] = {}
    if name:
        body["name"] = name
    if sc_uid:
        body["service_center_uid"] = sc_uid
    if ext_uid:
        body["external_uid"] = ext_uid

    def _fail(msg: str):
        sc_u = (request.form.get("service_center_uid") or "").strip()
        return render_template(
            "departments/edit.html",
            department_uid=uid,
            sc_options=sc_options,
            selected_sc_uid=sc_u,
            selected_sc_label=(label_for_sc_uid(sc_options, sc_u) or sc_u) if sc_u else "",
            form_error=msg,
            form_values=request.form,
        )

    if not body:
        return _fail("Укажите хотя бы одно поле для изменения")

    resp = client.patch(f"/api/v1/department/{uid}", json=body)
    if not resp.ok:
        return _fail(resp.error or "Ошибка обновления")

    return redirect(
        url_for(
            "departments.list_page",
            dept_msg="Отдел обновлён",
            dept_msg_type="success",
        )
    )


@bp.post("/<uid>/delete")
@require_permission("department.delete")
def delete_submit(uid: str):
    client = RMSClient()
    resp = client.delete(f"/api/v1/department/{uid}")
    if resp.ok:
        return redirect(_redirect_list("Отдел удалён", "success"))
    return redirect(_redirect_list(resp.error or "Ошибка удаления", "error"))
