from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, redirect, render_template, request, url_for

from app.list_limits import RMS_LIST_PAGE_LIMIT
from app.rbac.decorators import has_permission, require_any_permission, require_permission
from app.rms_client.client import RMSClient
from app.service_center_catalog import fetch_service_center_options, label_for_sc_uid


bp = Blueprint("warehouses", __name__, url_prefix="/warehouses")

SYSTEM_NAMES = ("ROSSKO", "BERG", "1C", "FORUM_AUTO", "ALFA_AUTO")

# Подписи в UI (значения API / URL — латиница из SystemNameEnum)
PROVIDER_LABELS: Dict[str, str] = {
    "ROSSKO": "ROSSKO",
    "BERG": "BERG",
    "1C": "1С",
    "FORUM_AUTO": "FORUM_AUTO",
    "ALFA_AUTO": "ALFA_AUTO",
}

_MAX_SCAN_PAGES = 80
_LIST_LIMIT = RMS_LIST_PAGE_LIMIT


def _normalize_external_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return "".join(text.split()).upper()


def _redirect_list(msg: Optional[str] = None, msg_type: str = "info") -> str:
    q: Dict[str, str] = {}
    if msg:
        q["wh_msg"] = msg
        q["wh_msg_type"] = msg_type
    return url_for("warehouses.list_page", **q)


def _redirect_detail(uid: str, msg: Optional[str] = None, msg_type: str = "info") -> str:
    q: Dict[str, str] = {"uid": uid}
    if msg:
        q["wh_msg"] = msg
        q["wh_msg_type"] = msg_type
    return url_for("warehouses.detail_page", **q)


def _redirect_delivery_hub(msg: Optional[str] = None, msg_type: str = "info") -> str:
    q: Dict[str, str] = {}
    if msg:
        q["wh_msg"] = msg
        q["wh_msg_type"] = msg_type
    return url_for("warehouses.delivery_routes_hub", **q)


def _fetch_warehouse_row(client: RMSClient, uid: str) -> Tuple[Optional[dict], Optional[str]]:
    cursor: Optional[str] = None
    for _ in range(_MAX_SCAN_PAGES):
        params: Dict[str, Any] = {"limit": _LIST_LIMIT}
        if cursor:
            params["cursor"] = cursor
        resp = client.get("/api/v1/warehouse", params=params)
        if not resp.ok:
            return None, resp.error
        payload = resp.data if isinstance(resp.data, dict) else {}
        items = payload.get("data", [])
        if not isinstance(items, list):
            return None, "Некорректный формат ответа RMS"
        for row in items:
            if isinstance(row, dict) and str(row.get("uid", "")) == uid:
                return row, None
        if not payload.get("has_more"):
            break
        nxt = payload.get("cursor")
        if not nxt:
            break
        cursor = str(nxt)
    return None, None


def _fetch_warehouse_options(client: RMSClient, max_total: int = 3000) -> List[Dict[str, str]]:
    options: List[Dict[str, str]] = []
    cursor: Optional[str] = None
    while len(options) < max_total:
        chunk_limit = min(RMS_LIST_PAGE_LIMIT, max_total - len(options))
        params: Dict[str, Any] = {"limit": chunk_limit}
        if cursor:
            params["cursor"] = cursor
        resp = client.get("/api/v1/warehouse", params=params)
        if not resp.ok:
            break
        payload = resp.data if isinstance(resp.data, dict) else {}
        rows = payload.get("data")
        if not isinstance(rows, list):
            break
        for row in rows:
            if not isinstance(row, dict):
                continue
            uid = str(row.get("uid") or "").strip()
            if not uid:
                continue
            name = str(row.get("name") or uid).strip()
            sc = row.get("service_center") if isinstance(row.get("service_center"), dict) else {}
            sc_uid = str(sc.get("service_center_uid") or sc.get("uid") or row.get("rms_uid") or "").strip()
            sc_name = str(sc.get("name") or "").strip()
            onec_id = ""
            providers = row.get("providers")
            if isinstance(providers, list):
                for p in providers:
                    if not isinstance(p, dict):
                        continue
                    code = str(p.get("provider") or "").strip().upper()
                    ids = str(p.get("ids") or "").strip()
                    if code == "1C" and ids:
                        onec_id = ids
                        break
            label = f"{name} ({uid})"
            if sc_uid:
                label += f" · СЦ: {sc_name or sc_uid}"
            options.append({"uid": uid, "label": label, "sc_uid": sc_uid, "onec_id": onec_id})
        if not payload.get("has_more"):
            break
        nxt = str(payload.get("cursor") or "").strip()
        if not nxt:
            break
        cursor = nxt
    uniq: Dict[str, Dict[str, str]] = {}
    for row in options:
        u = row.get("uid") or ""
        if u and u not in uniq:
            uniq[u] = row
    out = list(uniq.values())
    out.sort(key=lambda x: str(x.get("label") or "").casefold())
    return out


def _fetch_onec_to_warehouse_uid_map(client: RMSClient, max_total: int = 3000) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    cursor: Optional[str] = None
    loaded = 0
    while loaded < max_total:
        chunk_limit = min(RMS_LIST_PAGE_LIMIT, max_total - loaded)
        params: Dict[str, Any] = {"limit": chunk_limit}
        if cursor:
            params["cursor"] = cursor
        resp = client.get("/api/v1/warehouse", params=params)
        if not resp.ok:
            break
        payload = resp.data if isinstance(resp.data, dict) else {}
        rows = payload.get("data")
        if not isinstance(rows, list):
            break
        loaded += len(rows)
        for row in rows:
            if not isinstance(row, dict):
                continue
            uid = str(row.get("uid") or "").strip()
            if not uid:
                continue
            providers = row.get("providers")
            if not isinstance(providers, list):
                continue
            for p in providers:
                if not isinstance(p, dict):
                    continue
                code = str(p.get("provider") or "").strip().upper()
                ids = str(p.get("ids") or "").strip()
                if code != "1C" or not ids:
                    continue
                if ids not in mapping:
                    mapping[ids] = uid
                norm_ids = _normalize_external_id(ids)
                if norm_ids and norm_ids not in mapping:
                    mapping[norm_ids] = uid
        if not payload.get("has_more"):
            break
        nxt = str(payload.get("cursor") or "").strip()
        if not nxt:
            break
        cursor = nxt
    return mapping


def _fetch_sc_onec_id_from_provider_map(client: RMSClient, sc_uid: str) -> str:
    if not sc_uid:
        return ""
    resp = client.get(
        "/api/v1/warehouse/provider_map",
        params={"limit": 20, "rms_uid": sc_uid},
    )
    if not resp.ok or not isinstance(resp.data, dict):
        return ""
    rows = resp.data.get("data")
    if not isinstance(rows, list):
        return ""
    for row in rows:
        if not isinstance(row, dict):
            continue
        providers = row.get("providers")
        if not isinstance(providers, list):
            continue
        for p in providers:
            if not isinstance(p, dict):
                continue
            code = str(p.get("provider") or "").strip().upper()
            ids = str(p.get("ids") or "").strip()
            if code == "1C" and ids:
                return ids
    return ""


@bp.get("/delivery_routes")
@require_permission("warehouse.read")
def delivery_routes_hub():
    onec_id = (request.args.get("onec_id") or "").strip()
    limit_raw = (request.args.get("limit") or str(RMS_LIST_PAGE_LIMIT)).strip()
    cursor_dm = (request.args.get("cursor_dm") or "").strip()
    cursor_pm = (request.args.get("cursor_pm") or "").strip()
    rms_uid = (request.args.get("rms_uid") or "").strip()
    open_create = (request.args.get("open_create") or "").strip().lower() in ("1", "true", "yes", "on")

    try:
        limit = max(10, min(100, int(limit_raw)))
    except ValueError:
        limit = 20

    client = RMSClient()
    sc_options = fetch_service_center_options(client)
    rms_combo_label = (label_for_sc_uid(sc_options, rms_uid) or rms_uid) if rms_uid else ""
    wh_options = _fetch_warehouse_options(client)
    sc_warehouse_uid = ""
    if rms_uid:
        for w in wh_options:
            if str(w.get("sc_uid") or "") == rms_uid:
                sc_warehouse_uid = str(w.get("uid") or "")
                break

    effective_onec_id = onec_id
    if not effective_onec_id and rms_uid:
        effective_onec_id = _fetch_sc_onec_id_from_provider_map(client, rms_uid)

    dm_params: Dict[str, Any] = {"limit": limit}
    if cursor_dm:
        dm_params["cursor"] = cursor_dm
    if effective_onec_id:
        dm_params["1с_id"] = effective_onec_id

    dm_resp = client.get("/api/v1/warehouse/delivery_map", params=dm_params)
    dm_rows: List[Any] = []
    dm_meta: Dict[str, Any] = {}
    dm_err: Optional[str] = None
    if dm_resp.ok and isinstance(dm_resp.data, dict):
        dm_rows = dm_resp.data.get("data", []) if isinstance(dm_resp.data.get("data"), list) else []
        onec_to_uid = _fetch_onec_to_warehouse_uid_map(client)
        sc_label_by_uid = {
            str(o.get("uid") or ""): str(o.get("label") or "")
            for o in sc_options
            if isinstance(o, dict) and str(o.get("uid") or "").strip()
        }
        warehouse_sc_uid_by_uid = {
            str(w.get("uid") or ""): str(w.get("sc_uid") or "")
            for w in wh_options
            if isinstance(w, dict) and str(w.get("uid") or "").strip()
        }
        for row in dm_rows:
            if not isinstance(row, dict):
                continue
            to_onec_id = str(row.get("to_warehouse_id") or "").strip()
            to_warehouse_uid = onec_to_uid.get(to_onec_id, "")
            row["to_warehouse_uid"] = to_warehouse_uid
            to_sc_uid = warehouse_sc_uid_by_uid.get(to_warehouse_uid, "")
            row["to_sc_name"] = sc_label_by_uid.get(to_sc_uid, "")
            sources = row.get("source_warehouses")
            if not isinstance(sources, list):
                continue
            filtered_sources: List[Dict[str, Any]] = []
            for s in sources:
                if not isinstance(s, dict):
                    continue
                from_onec_id = str(s.get("from_source_warehouse_id") or "").strip()
                from_warehouse_uid = onec_to_uid.get(from_onec_id, "")
                if not from_warehouse_uid:
                    from_warehouse_uid = onec_to_uid.get(_normalize_external_id(from_onec_id), "")
                s["from_warehouse_uid"] = from_warehouse_uid
                s["to_warehouse_uid"] = row.get("to_warehouse_uid") or ""
                # Строки без from/to UID нельзя корректно править или удалять через текущие API-ручки.
                # Скрываем их из карты, чтобы не показывать "битые" записи.
                if s["from_warehouse_uid"] and s["to_warehouse_uid"]:
                    filtered_sources.append(s)
            row["source_warehouses"] = filtered_sources
        dm_meta = {
            "cursor": str(dm_resp.data.get("cursor") or ""),
            "has_more": bool(dm_resp.data.get("has_more")),
        }
    elif dm_resp.status_code != 404:
        dm_err = dm_resp.error

    return render_template(
        "warehouses/delivery_routes.html",
        onec_id=effective_onec_id,
        rms_uid=rms_uid,
        sc_options=sc_options,
        selected_sc_label=rms_combo_label,
        limit=limit,
        dm_rows=dm_rows,
        dm_meta=dm_meta,
        dm_err=dm_err,
        wh_options=wh_options,
        sc_warehouse_uid=sc_warehouse_uid,
        wh_msg=request.args.get("wh_msg"),
        wh_msg_type=request.args.get("wh_msg_type") or "info",
        can_mutate=(has_permission("warehouse.update") or has_permission("warehouse.read")),
        open_create=open_create,
    )


@bp.get("/provider_map")
@require_permission("warehouse.read")
def provider_map_page():
    cursor_pm = (request.args.get("cursor_pm") or "").strip()
    limit_raw = (request.args.get("limit") or str(RMS_LIST_PAGE_LIMIT)).strip()
    rms_uid = (request.args.get("rms_uid") or "").strip()
    try:
        limit = max(10, min(100, int(limit_raw)))
    except ValueError:
        limit = 20

    client = RMSClient()
    sc_options = fetch_service_center_options(client)
    sc_label_by_uid: Dict[str, str] = {
        str(o.get("uid") or ""): str(o.get("label") or str(o.get("uid") or ""))
        for o in sc_options
        if isinstance(o, dict) and str(o.get("uid") or "")
    }
    selected_sc_label = (label_for_sc_uid(sc_options, rms_uid) or rms_uid) if rms_uid else ""

    pm_params: Dict[str, Any] = {"limit": limit}
    if cursor_pm:
        pm_params["cursor"] = cursor_pm
    if rms_uid:
        pm_params["rms_uid"] = rms_uid

    pm_resp = client.get("/api/v1/warehouse/provider_map", params=pm_params)
    pm_rows: List[Any] = []
    pm_meta: Dict[str, Any] = {"cursor": "", "has_more": False}
    pm_err: Optional[str] = None
    if pm_resp.ok and isinstance(pm_resp.data, dict):
        pm_rows = pm_resp.data.get("data", []) if isinstance(pm_resp.data.get("data"), list) else []
        pm_meta = {
            "cursor": str(pm_resp.data.get("cursor") or ""),
            "has_more": bool(pm_resp.data.get("has_more")),
        }
        for row in pm_rows:
            if not isinstance(row, dict):
                continue
            uid = str(row.get("rms_uid") or "").strip()
            row["sc_name"] = sc_label_by_uid.get(uid) or uid or "—"
            providers = row.get("providers")
            pmap: Dict[str, str] = {}
            if isinstance(providers, list):
                for p in providers:
                    if not isinstance(p, dict):
                        continue
                    code = str(p.get("provider") or "").strip()
                    if not code:
                        continue
                    pmap[code] = str(p.get("ids") or "").strip()
            row["providers_map"] = pmap
    elif pm_resp.status_code != 404:
        pm_err = pm_resp.error

    return render_template(
        "warehouses/provider_map.html",
        rms_uid=rms_uid,
        selected_sc_label=selected_sc_label,
        sc_options=sc_options,
        limit=limit,
        pm_rows=pm_rows,
        pm_meta=pm_meta,
        pm_err=pm_err,
        provider_labels=PROVIDER_LABELS,
    )


@bp.post("/delivery_routes/create")
@require_any_permission("warehouse.update", "warehouse.read")
def delivery_route_create():
    from_uid = (request.form.get("from_warehouse_uid") or "").strip()
    to_uid = (request.form.get("to_warehouse_uid") or "").strip()
    is_active = request.form.get("is_active") in ("1", "true", "yes", "on")
    days_raw = (request.form.get("delivery_days") or "0").strip()
    try:
        delivery_days = float(days_raw.replace(",", "."))
    except ValueError:
        return redirect(_redirect_delivery_hub("Некорректное число delivery_days", "error"))

    if not from_uid or not to_uid:
        return redirect(_redirect_delivery_hub("Укажите from и to warehouse UID", "error"))

    body = {
        "from_warehouse_uid": from_uid,
        "to_warehouse_uid": to_uid,
        "is_active": is_active,
        "delivery_days": delivery_days,
    }
    client = RMSClient()
    resp = client.post("/api/v1/warehouse/delivery_route", json=body)
    if resp.ok:
        return redirect(_redirect_delivery_hub("Маршрут создан", "success"))
    return redirect(_redirect_delivery_hub(resp.error or "Ошибка создания маршрута", "error"))


@bp.post("/delivery_routes/update")
@require_any_permission("warehouse.update", "warehouse.read")
def delivery_route_update():
    from_uid = (request.form.get("from_warehouse_uid") or "").strip()
    to_uid = (request.form.get("to_warehouse_uid") or "").strip()
    is_active = request.form.get("is_active") in ("1", "true", "yes", "on")
    days_raw = (request.form.get("delivery_days") or "").strip()

    if not from_uid or not to_uid:
        return redirect(_redirect_delivery_hub("Укажите from и to warehouse UID", "error"))

    body: Dict[str, Any] = {"is_active": is_active}
    if days_raw != "":
        try:
            body["delivery_days"] = float(days_raw.replace(",", "."))
        except ValueError:
            return redirect(_redirect_delivery_hub("Некорректное число delivery_days", "error"))

    client = RMSClient()
    path = f"/api/v1/warehouse/delivery_route/{from_uid}/{to_uid}"
    resp = client.patch(path, json=body)
    if resp.ok:
        return redirect(_redirect_delivery_hub("Маршрут обновлён", "success"))
    return redirect(_redirect_delivery_hub(resp.error or "Ошибка обновления маршрута", "error"))


@bp.post("/delivery_routes/delete")
@require_any_permission("warehouse.update", "warehouse.read")
def delivery_route_delete():
    from_uid = (request.form.get("from_warehouse_uid") or "").strip()
    to_uid = (request.form.get("to_warehouse_uid") or "").strip()
    if not from_uid or not to_uid:
        return redirect(_redirect_delivery_hub("Укажите from и to warehouse UID", "error"))
    client = RMSClient()
    path = f"/api/v1/warehouse/delivery_route/{from_uid}/{to_uid}"
    # В текущем API нет DELETE для delivery_route, поэтому удаление выполняем как soft-disable.
    resp = client.patch(path, json={"is_active": False})
    if resp.ok:
        return redirect(_redirect_delivery_hub("Маршрут деактивирован", "success"))
    return redirect(_redirect_delivery_hub(resp.error or "Ошибка удаления маршрута", "error"))


@bp.get("")
@require_permission("warehouse.read")
def list_page():
    cursor = (request.args.get("cursor") or "").strip()
    params: Dict[str, Any] = {"limit": _LIST_LIMIT}
    if cursor:
        params["cursor"] = cursor

    client = RMSClient()
    resp = client.get("/api/v1/warehouse", params=params)

    if resp.ok:
        payload = resp.data if isinstance(resp.data, dict) else {}
        items = payload.get("data", [])
        if not isinstance(items, list):
            items = []
        page = {
            "next_cursor": str(payload.get("cursor") or ""),
            "has_more": bool(payload.get("has_more")),
        }
        error = None
    elif resp.status_code == 404:
        items = []
        page = {"next_cursor": "", "has_more": False}
        error = None
    else:
        items = []
        page = {"next_cursor": "", "has_more": False}
        error = resp.error

    return render_template(
        "warehouses/list.html",
        items=items,
        page=page,
        error=error,
        wh_msg=request.args.get("wh_msg"),
        wh_msg_type=request.args.get("wh_msg_type") or "info",
        can_create=has_permission("warehouse.create"),
    )


@bp.get("/new")
@require_permission("warehouse.create")
def new_page():
    client = RMSClient()
    sc_options = fetch_service_center_options(client)
    return render_template(
        "warehouses/new.html",
        form_error=None,
        form_values=None,
        sc_options=sc_options,
        selected_rms_uid="",
        selected_sc_label="",
    )


@bp.post("/new")
@require_permission("warehouse.create")
def create_submit():
    client = RMSClient()
    sc_options = fetch_service_center_options(client)
    name = (request.form.get("name") or "").strip()
    rms_uid = (request.form.get("rms_uid") or "").strip()
    rms_combo_label = (label_for_sc_uid(sc_options, rms_uid) or rms_uid) if rms_uid else ""
    acc_raw = (request.form.get("acceptance_time") or "0").strip()
    try:
        acceptance_time = float(acc_raw.replace(",", "."))
    except ValueError:
        return render_template(
            "warehouses/new.html",
            form_error="Некорректное acceptance_time",
            form_values=request.form,
            sc_options=sc_options,
            selected_rms_uid=rms_uid,
            selected_sc_label=rms_combo_label,
        )
    if not name:
        return render_template(
            "warehouses/new.html",
            form_error="Укажите название",
            form_values=request.form,
            sc_options=sc_options,
            selected_rms_uid=rms_uid,
            selected_sc_label=rms_combo_label,
        )

    body: Dict[str, Any] = {"name": name, "acceptance_time": acceptance_time}
    if rms_uid:
        body["rms_uid"] = rms_uid

    resp = client.post("/api/v1/warehouse", json=body)
    if not resp.ok:
        return render_template(
            "warehouses/new.html",
            form_error=resp.error or "Ошибка создания",
            form_values=request.form,
            sc_options=sc_options,
            selected_rms_uid=rms_uid,
            selected_sc_label=rms_combo_label,
        )

    uid_new = None
    if isinstance(resp.data, dict):
        inner = resp.data.get("data")
        if isinstance(inner, dict):
            uid_new = inner.get("uid")
        if uid_new is None:
            uid_new = resp.data.get("uid")
    uid_str = str(uid_new) if uid_new else ""
    if uid_str:
        return redirect(_redirect_detail(uid_str, "Склад создан", "success"))
    return redirect(_redirect_list("Создано, но в ответе нет uid", "error"))


@bp.get("/<uid>")
@require_permission("warehouse.read")
def detail_page(uid: str):
    can_manage_external = has_permission("warehouse.update") or has_permission("warehouse.read")
    client = RMSClient()
    row, err = _fetch_warehouse_row(client, uid)
    if err:
        return render_template(
            "warehouses/detail.html",
            uid=uid,
            warehouse=None,
            error=err,
            wh_msg=request.args.get("wh_msg"),
            wh_msg_type=request.args.get("wh_msg_type") or "info",
            can_update=(has_permission("warehouse.update") or has_permission("warehouse.read")),
            can_delete=has_permission("warehouse.delete"),
            can_manage_external=can_manage_external,
            system_names=SYSTEM_NAMES,
            provider_labels=PROVIDER_LABELS,
        )
    if row is None:
        return render_template(
            "warehouses/detail.html",
            uid=uid,
            warehouse=None,
            error="Склад не найден (в пределах выборки API)",
            wh_msg=request.args.get("wh_msg"),
            wh_msg_type=request.args.get("wh_msg_type") or "info",
            can_update=(has_permission("warehouse.update") or has_permission("warehouse.read")),
            can_delete=has_permission("warehouse.delete"),
            can_manage_external=can_manage_external,
            system_names=SYSTEM_NAMES,
            provider_labels=PROVIDER_LABELS,
        )

    return render_template(
        "warehouses/detail.html",
        uid=uid,
        warehouse=row,
        error=None,
        wh_msg=request.args.get("wh_msg"),
        wh_msg_type=request.args.get("wh_msg_type") or "info",
        can_update=(has_permission("warehouse.update") or has_permission("warehouse.read")),
        can_delete=has_permission("warehouse.delete"),
        can_manage_external=can_manage_external,
        system_names=SYSTEM_NAMES,
        provider_labels=PROVIDER_LABELS,
    )


@bp.post("/<uid>/patch")
@require_any_permission("warehouse.update", "warehouse.read")
def warehouse_patch(uid: str):
    name = (request.form.get("name") or "").strip()
    acc_raw = (request.form.get("acceptance_time") or "").strip()
    body: Dict[str, Any] = {}
    if name:
        body["name"] = name
    if acc_raw != "":
        try:
            body["acceptance_time"] = float(acc_raw.replace(",", "."))
        except ValueError:
            return redirect(_redirect_detail(uid, "Некорректное acceptance_time", "error"))
    if not body:
        return redirect(_redirect_detail(uid, "Укажите name и/или acceptance_time", "error"))

    client = RMSClient()
    resp = client.patch(f"/api/v1/warehouse/{uid}", json=body)
    if resp.ok:
        return redirect(_redirect_detail(uid, "Сохранено", "success"))
    return redirect(_redirect_detail(uid, resp.error or "Ошибка сохранения", "error"))


@bp.post("/<uid>/delete")
@require_permission("warehouse.delete")
def warehouse_delete(uid: str):
    client = RMSClient()
    resp = client.delete(f"/api/v1/warehouse/{uid}")
    if resp.ok:
        return redirect(_redirect_list("Склад удалён", "success"))
    return redirect(_redirect_detail(uid, resp.error or "Ошибка удаления", "error"))


@bp.post("/<uid>/external_system/create")
@require_any_permission("warehouse.update", "warehouse.read")
def external_system_create(uid: str):
    system_name = (request.form.get("system_name") or "").strip()
    system_id = (request.form.get("system_id") or "").strip()
    if not system_name or not system_id:
        return redirect(_redirect_detail(uid, "Укажите system_name и system_id", "error"))
    if system_name not in SYSTEM_NAMES:
        return redirect(_redirect_detail(uid, "Неизвестный system_name", "error"))

    client = RMSClient()
    resp = client.post(
        f"/api/v1/warehouse/{uid}/external_system",
        json={"system_name": system_name, "system_id": system_id},
    )
    if resp.ok:
        return redirect(_redirect_detail(uid, "Внешняя система привязана", "success"))
    return redirect(_redirect_detail(uid, resp.error or "Ошибка привязки", "error"))


@bp.post("/<uid>/external_system/patch")
@require_any_permission("warehouse.update", "warehouse.read")
def external_system_patch(uid: str):
    system_name = (request.form.get("system_name") or "").strip()
    system_id = (request.form.get("system_id") or "").strip()
    if not system_name or not system_id:
        return redirect(_redirect_detail(uid, "Укажите system_name и system_id", "error"))
    if system_name not in SYSTEM_NAMES:
        return redirect(_redirect_detail(uid, "Неизвестный system_name", "error"))

    client = RMSClient()
    resp = client.patch(
        f"/api/v1/warehouse/{uid}/external_system/{system_name}",
        json={"system_id": system_id},
    )
    if resp.ok:
        return redirect(_redirect_detail(uid, "Идентификатор внешней системы обновлён", "success"))
    return redirect(_redirect_detail(uid, resp.error or "Ошибка обновления", "error"))
