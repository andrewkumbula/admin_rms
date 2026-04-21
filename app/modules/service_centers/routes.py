import json
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, redirect, render_template, request, url_for

from app.list_limits import RMS_LIST_PAGE_LIMIT
from app.rbac.decorators import has_permission, require_any_permission, require_permission
from app.rms_client.client import RMSClient


bp = Blueprint("service_centers", __name__, url_prefix="/service_centers")
RESOURCE_TYPE_OPTIONS_MAX = 4000
PROVIDER_CODES = ("ROSSKO", "BERG", "1C", "FORUM_AUTO")
WAREHOUSE_SCAN_MAX_PAGES = 80


def _redirect_detail(uid: str, tab: str, message: Optional[str] = None, msg_type: str = "info") -> str:
    kwargs = {"uid": uid, "tab": tab}
    if message:
        kwargs["sc_msg"] = message
        kwargs["sc_msg_type"] = msg_type
    return url_for("service_centers.detail_page", **kwargs)


def _extract_franchisees_payload(payload: Any) -> list:
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


def _fetch_franchisee_select_options(client: RMSClient) -> List[Dict[str, str]]:
    resp = client.get("/api/v1/franchisee", params={"limit": RMS_LIST_PAGE_LIMIT})
    if not resp.ok:
        return []
    rows = _extract_franchisees_payload(resp.data)
    out: List[Dict[str, str]] = []
    for x in rows:
        if not isinstance(x, dict):
            continue
        u = str(x.get("uid") or "").strip()
        if not u:
            continue
        name = str(x.get("name") or "").strip()
        out.append({"uid": u, "label": name or u})
    out.sort(key=lambda r: r["label"].casefold())
    return out


def _fetch_resource_type_labels(client: RMSClient) -> Dict[str, str]:
    labels: Dict[str, str] = {}
    cursor: Optional[str] = None
    while len(labels) < RESOURCE_TYPE_OPTIONS_MAX:
        chunk_limit = min(RMS_LIST_PAGE_LIMIT, RESOURCE_TYPE_OPTIONS_MAX - len(labels))
        params: Dict[str, Any] = {"limit": chunk_limit}
        if cursor:
            params["cursor"] = cursor
        resp = client.get("/api/v1/resource_type", params=params)
        if not resp.ok:
            break
        payload = resp.data if isinstance(resp.data, dict) else {}
        data = payload.get("data")
        chunk = data if isinstance(data, list) else []
        for row in chunk:
            if not isinstance(row, dict):
                continue
            uid = str(row.get("uid") or "").strip()
            if not uid or uid in labels:
                continue
            child = str(row.get("children_resource_type") or "").strip()
            parent = str(row.get("parent_resource_type") or "").strip()
            labels[uid] = child or parent or uid
        if not bool(payload.get("has_more")):
            break
        nxt = str(payload.get("cursor") or "").strip()
        if not nxt:
            break
        cursor = nxt
    return labels


def _fetch_working_zone_resource_options(client: RMSClient) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    cursor: Optional[str] = None
    allowed_parents = {"мойка", "зона ремонта"}
    while len(items) < RESOURCE_TYPE_OPTIONS_MAX:
        chunk_limit = min(RMS_LIST_PAGE_LIMIT, RESOURCE_TYPE_OPTIONS_MAX - len(items))
        params: Dict[str, Any] = {"limit": chunk_limit}
        if cursor:
            params["cursor"] = cursor
        resp = client.get("/api/v1/resource_type", params=params)
        if not resp.ok:
            break
        payload = resp.data if isinstance(resp.data, dict) else {}
        data = payload.get("data")
        chunk = data if isinstance(data, list) else []
        for row in chunk:
            if not isinstance(row, dict):
                continue
            uid = str(row.get("uid") or "").strip()
            if not uid:
                continue
            parent = str(row.get("parent_resource_type") or "").strip()
            if parent.casefold() not in allowed_parents:
                continue
            child = str(row.get("children_resource_type") or "").strip()
            label = f"{child} ({parent})" if child and parent else (child or parent or uid)
            items.append({"uid": uid, "label": label})
        if not bool(payload.get("has_more")):
            break
        nxt = str(payload.get("cursor") or "").strip()
        if not nxt:
            break
        cursor = nxt
    uniq: Dict[str, Dict[str, str]] = {}
    for row in items:
        uid = row.get("uid") or ""
        if uid and uid not in uniq:
            uniq[uid] = row
    out = list(uniq.values())
    out.sort(key=lambda x: str(x.get("label") or "").casefold())
    return out


def _fetch_equipment_resource_options(client: RMSClient) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    cursor: Optional[str] = None
    allowed_parents = {"оборудование", "диагностическое оборудование", "внешнее оборудование"}
    while len(items) < RESOURCE_TYPE_OPTIONS_MAX:
        chunk_limit = min(RMS_LIST_PAGE_LIMIT, RESOURCE_TYPE_OPTIONS_MAX - len(items))
        params: Dict[str, Any] = {"limit": chunk_limit}
        if cursor:
            params["cursor"] = cursor
        resp = client.get("/api/v1/resource_type", params=params)
        if not resp.ok:
            break
        payload = resp.data if isinstance(resp.data, dict) else {}
        data = payload.get("data")
        chunk = data if isinstance(data, list) else []
        for row in chunk:
            if not isinstance(row, dict):
                continue
            uid = str(row.get("uid") or "").strip()
            if not uid:
                continue
            parent = str(row.get("parent_resource_type") or "").strip()
            if parent.casefold() not in allowed_parents:
                continue
            child = str(row.get("children_resource_type") or "").strip()
            label = f"{child} ({parent})" if child and parent else (child or parent or uid)
            items.append({"uid": uid, "label": label})
        if not bool(payload.get("has_more")):
            break
        nxt = str(payload.get("cursor") or "").strip()
        if not nxt:
            break
        cursor = nxt
    uniq: Dict[str, Dict[str, str]] = {}
    for row in items:
        uid = row.get("uid") or ""
        if uid and uid not in uniq:
            uniq[uid] = row
    out = list(uniq.values())
    out.sort(key=lambda x: str(x.get("label") or "").casefold())
    return out


def _fetch_employee_resource_options(client: RMSClient, external: bool = False) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    cursor: Optional[str] = None
    allowed_parents = {"внешний сотрудник"} if external else {"сотрудник"}
    while len(items) < RESOURCE_TYPE_OPTIONS_MAX:
        chunk_limit = min(RMS_LIST_PAGE_LIMIT, RESOURCE_TYPE_OPTIONS_MAX - len(items))
        params: Dict[str, Any] = {"limit": chunk_limit}
        if cursor:
            params["cursor"] = cursor
        resp = client.get("/api/v1/resource_type", params=params)
        if not resp.ok:
            break
        payload = resp.data if isinstance(resp.data, dict) else {}
        data = payload.get("data")
        chunk = data if isinstance(data, list) else []
        for row in chunk:
            if not isinstance(row, dict):
                continue
            uid = str(row.get("uid") or "").strip()
            if not uid:
                continue
            parent = str(row.get("parent_resource_type") or "").strip()
            if parent.casefold() not in allowed_parents:
                continue
            child = str(row.get("children_resource_type") or "").strip()
            label = f"{child} ({parent})" if child and parent else (child or parent or uid)
            items.append({"uid": uid, "label": label})
        if not bool(payload.get("has_more")):
            break
        nxt = str(payload.get("cursor") or "").strip()
        if not nxt:
            break
        cursor = nxt
    uniq: Dict[str, Dict[str, str]] = {}
    for row in items:
        uid = row.get("uid") or ""
        if uid and uid not in uniq:
            uniq[uid] = row
    out = list(uniq.values())
    out.sort(key=lambda x: str(x.get("label") or "").casefold())
    return out


@bp.get("")
@require_permission("service_center.read")
def list_page():
    name = (request.args.get("name") or "").strip()
    franchisee_uid = (request.args.get("franchisee_uid") or "").strip()
    cursor = (request.args.get("cursor") or "").strip()
    show_deleted = request.args.get("show_deleted") == "1"
    params = {"limit": RMS_LIST_PAGE_LIMIT}
    if name:
        params["name"] = name
    if franchisee_uid:
        params["franchisee_id"] = franchisee_uid
    if cursor:
        params["cursor"] = cursor
    if show_deleted:
        params["show_deleted"] = "1"

    client = RMSClient()
    response = client.get("/api/v2/service_center", params=params)
    if not response.ok:
        return render_template(
            "service_centers/list.html",
            items=[],
            filters={
                "name": name,
                "franchisee_uid": franchisee_uid,
                "show_deleted": show_deleted,
            },
            page={"next_cursor": "", "has_more": False},
            error=response.error,
            can_create_sc=has_permission("service_center.create"),
        )

    payload = response.data if isinstance(response.data, dict) else {}
    data = payload.get("data", {})
    metadata = payload.get("metadata", {})
    items = data.get("service_centers", []) if isinstance(data, dict) else []
    if isinstance(items, list):
        for row in items:
            if not isinstance(row, dict):
                continue
            fu = row.get("franchisee_uid")
            if fu is None and row.get("franchiseeUid") is not None:
                row["franchisee_uid"] = row.get("franchiseeUid")
            elif fu is None and row.get("franchise_uid") is not None:
                row["franchisee_uid"] = row.get("franchise_uid")

    next_cursor = metadata.get("cursor") if isinstance(metadata, dict) else ""
    has_more = bool(metadata.get("has_more")) if isinstance(metadata, dict) else False

    return render_template(
        "service_centers/list.html",
        items=items if isinstance(items, list) else [],
        filters={
            "name": name,
            "franchisee_uid": franchisee_uid,
            "show_deleted": show_deleted,
        },
        page={"next_cursor": next_cursor or "", "has_more": has_more},
        error=None,
        can_create_sc=has_permission("service_center.create"),
    )


@bp.get("/create")
@require_permission("service_center.create")
def create_page():
    return render_template("service_centers/create.html", form_error=None, form_values=None)


@bp.post("/create")
@require_permission("service_center.create")
def create_submit():
    name = (request.form.get("name") or "").strip()
    address_raw = (request.form.get("address_json") or "").strip()
    if not name:
        return render_template(
            "service_centers/create.html",
            form_error="Укажите название",
            form_values=request.form,
        )
    if not address_raw:
        return render_template(
            "service_centers/create.html",
            form_error="Укажите JSON адреса (поле address для RMS)",
            form_values=request.form,
        )
    try:
        address_obj: Any = json.loads(address_raw)
    except json.JSONDecodeError as exc:
        return render_template(
            "service_centers/create.html",
            form_error=f"Некорректный JSON адреса: {exc}",
            form_values=request.form,
        )
    if not isinstance(address_obj, dict) or not address_obj:
        return render_template(
            "service_centers/create.html",
            form_error="Адрес должен быть непустым JSON-объектом",
            form_values=request.form,
        )

    client = RMSClient()
    body: Dict[str, Any] = {"name": name, "address": address_obj}
    resp = client.post("/api/v2/service_center", json=body)
    if not resp.ok:
        return render_template(
            "service_centers/create.html",
            form_error=resp.error or "Ошибка создания",
            form_values=request.form,
        )

    sc_id = resp.data.get("service_center_id") if isinstance(resp.data, dict) else None
    uid_str = str(sc_id) if sc_id else ""
    if uid_str:
        return redirect(_redirect_detail(uid_str, "overview", "Сервисный центр создан", "success"))
    return redirect(url_for("service_centers.list_page"))


@bp.get("/<uid>")
@require_permission("service_center.read")
def detail_page(uid: str):
    tab = (request.args.get("tab") or "overview").strip()
    can_read_department = has_permission("department.read")
    can_read_working_zone = has_permission("dictionary.working_zone.read")
    can_read_warehouse = has_permission("warehouse.read")
    allowed_tabs = {"overview", "schedule", "employees", "equipment", "day_offs", "slots"}
    if can_read_department:
        allowed_tabs.add("departments")
    if can_read_working_zone:
        allowed_tabs.add("working_zones")
    if can_read_warehouse:
        allowed_tabs.add("warehouses")
    if tab not in allowed_tabs:
        tab = "overview"

    client = RMSClient()

    sc_resp = client.get(f"/api/v1/service_center/{uid}")
    employees_resp = client.get(
        f"/api/v1/service_center/{uid}/employee", params={"limit": RMS_LIST_PAGE_LIMIT}
    )
    equipment_resp = client.get(
        f"/api/v1/service_center/{uid}/equipment", params={"limit": RMS_LIST_PAGE_LIMIT}
    )
    day_offs_resp = client.get(
        f"/api/v1/service_center/{uid}/day_off", params={"limit": RMS_LIST_PAGE_LIMIT}
    )

    sc_data = _extract_object(sc_resp.data)
    schedule_items = sc_data.get("schedule") if isinstance(sc_data.get("schedule"), list) else []
    all_employee_items = _extract_list(
        employees_resp.data, ["service_centers", "employees", "resources", "items"]
    )
    employee_items: List[Dict[str, Any]] = []
    external_employee_items: List[Dict[str, Any]] = []
    employee_rt_labels = (
        _fetch_resource_type_labels(client)
        if isinstance(all_employee_items, list) and all_employee_items
        else {}
    )
    for row in all_employee_items if isinstance(all_employee_items, list) else []:
        if not isinstance(row, dict):
            continue
        rt_list = row.get("resource_types")
        if isinstance(rt_list, list) and rt_list:
            labels: List[str] = []
            for rt_row in rt_list:
                if not isinstance(rt_row, dict):
                    continue
                rt_name = str(rt_row.get("resource_type") or "").strip()
                if rt_name:
                    labels.append(rt_name)
                    continue
                rt_uid = str(rt_row.get("resource_type_uid") or "").strip()
                if rt_uid:
                    labels.append(employee_rt_labels.get(rt_uid, rt_uid))
            if labels:
                uniq: List[str] = []
                seen: set[str] = set()
                for x in labels:
                    k = x.casefold()
                    if k in seen:
                        continue
                    seen.add(k)
                    uniq.append(x)
                row["resource_type_label"] = ", ".join(uniq)
                continue
        rt = row.get("resource_type")
        if isinstance(rt, str) and rt.strip():
            rt_str = rt.strip()
            row["resource_type_label"] = employee_rt_labels.get(rt_str, rt_str)
            continue
        if isinstance(rt, dict):
            child = str(rt.get("children_resource_type") or "").strip()
            parent = str(rt.get("parent_resource_type") or "").strip()
            if child or parent:
                row["resource_type_label"] = child or parent
                continue
        child = str(row.get("children_resource_type") or row.get("resource_type_name") or "").strip()
        parent = str(row.get("parent_resource_type") or "").strip()
        if child or parent:
            row["resource_type_label"] = child or parent
            continue
        rt_uid = str(row.get("resource_type_uid") or row.get("resource_uid") or "").strip()
        if rt_uid:
            row["resource_type_label"] = employee_rt_labels.get(rt_uid, rt_uid)
        if bool(row.get("is_external")):
            external_employee_items.append(row)
        else:
            employee_items.append(row)
    equipment_items = _extract_list(
        equipment_resp.data, ["service_centers", "equipment", "items"]
    )
    can_manage_equipment = has_permission("service_center.update") or has_permission("service_center.read")
    can_manage_external_employee = has_permission("service_center.update")
    equipment_resource_options: List[Dict[str, str]] = (
        _fetch_equipment_resource_options(client) if can_manage_equipment else []
    )
    employee_resource_options: List[Dict[str, str]] = (
        _fetch_employee_resource_options(client, external=False) if has_permission("service_center.update") else []
    )
    external_employee_resource_options: List[Dict[str, str]] = (
        _fetch_employee_resource_options(client, external=True) if can_manage_external_employee else []
    )
    equipment_rt_labels = _fetch_resource_type_labels(client) if isinstance(equipment_items, list) and equipment_items else {}
    for row in equipment_items if isinstance(equipment_items, list) else []:
        if not isinstance(row, dict):
            continue
        rt_list = row.get("resource_types")
        if isinstance(rt_list, list) and rt_list:
            labels: List[str] = []
            for rt_row in rt_list:
                if not isinstance(rt_row, dict):
                    continue
                rt_name = str(rt_row.get("resource_type") or "").strip()
                if rt_name:
                    labels.append(rt_name)
                    continue
                rt_uid = str(rt_row.get("resource_type_uid") or "").strip()
                if rt_uid:
                    labels.append(equipment_rt_labels.get(rt_uid, rt_uid))
            if labels:
                uniq: List[str] = []
                seen: set[str] = set()
                for x in labels:
                    k = x.casefold()
                    if k in seen:
                        continue
                    seen.add(k)
                    uniq.append(x)
                row["resource_type_label"] = ", ".join(uniq)
                continue
        rt = row.get("resource_type")
        if isinstance(rt, str) and rt.strip():
            rt_str = rt.strip()
            row["resource_type_label"] = equipment_rt_labels.get(rt_str, rt_str)
            continue
        if isinstance(rt, dict):
            child = str(rt.get("children_resource_type") or "").strip()
            parent = str(rt.get("parent_resource_type") or "").strip()
            if child or parent:
                row["resource_type_label"] = child or parent
                continue
        child = str(row.get("children_resource_type") or row.get("resource_type_name") or "").strip()
        parent = str(row.get("parent_resource_type") or "").strip()
        if child or parent:
            row["resource_type_label"] = child or parent
            continue
        rt_uid = str(row.get("resource_type_uid") or row.get("resource_uid") or "").strip()
        if rt_uid:
            row["resource_type_label"] = equipment_rt_labels.get(rt_uid, rt_uid)

    if day_offs_resp.ok:
        day_off_items = _extract_day_offs(day_offs_resp.data)
    elif day_offs_resp.status_code == 404:
        day_off_items = []
    else:
        day_off_items = []

    working_zone_items: List[Any] = []
    working_zones_page = {"next_cursor": "", "has_more": False}
    working_zones_error: Optional[str] = None
    wz_cursor = (request.args.get("wz_cursor") or "").strip()
    if can_read_working_zone:
        wz_params: Dict[str, Any] = {"limit": RMS_LIST_PAGE_LIMIT}
        if wz_cursor:
            wz_params["cursor"] = wz_cursor
        wz_resp = client.get(f"/api/v1/service_center/{uid}/working_zone", params=wz_params)
        if wz_resp.ok:
            working_zone_items = _extract_list(wz_resp.data, ["working_zones", "items", "data"])
            working_zones_page = _extract_page_metadata(wz_resp.data)
            rt_labels = _fetch_resource_type_labels(client)
            for row in working_zone_items:
                if not isinstance(row, dict):
                    continue
                if isinstance(row.get("resource_type"), str) and str(row.get("resource_type")).strip():
                    row["resource_type_label"] = str(row.get("resource_type")).strip()
                    continue
                if isinstance(row.get("resource_type"), dict):
                    nested = row.get("resource_type")
                    child = str(nested.get("children_resource_type") or "").strip()
                    parent = str(nested.get("parent_resource_type") or "").strip()
                    if child or parent:
                        row["resource_type_label"] = child or parent
                        continue
                rt_uid = str(
                    row.get("resource_type_uid")
                    or row.get("resource_uid")
                    or row.get("resourceTypeUid")
                    or ""
                ).strip()
                if rt_uid:
                    row["resource_type_label"] = rt_labels.get(rt_uid, rt_uid)
        elif wz_resp.status_code == 404:
            working_zone_items = []
        else:
            working_zones_error = wz_resp.error
    can_manage_working_zone = has_permission("service_center.update") or has_permission(
        "service_center.read"
    )
    working_zone_resource_options: List[Dict[str, str]] = (
        _fetch_working_zone_resource_options(client) if can_manage_working_zone else []
    )

    provider_rows: List[Dict[str, Any]] = []
    provider_err: Optional[str] = None
    provider_resp = client.get(
        "/api/v1/warehouse/provider_map",
        params={"limit": RMS_LIST_PAGE_LIMIT, "rms_uid": uid},
    )
    if provider_resp.ok and isinstance(provider_resp.data, dict):
        raw_rows = provider_resp.data.get("data")
        if isinstance(raw_rows, list):
            for row in raw_rows:
                if not isinstance(row, dict):
                    continue
                pmap: Dict[str, str] = {}
                providers = row.get("providers")
                if isinstance(providers, list):
                    for p in providers:
                        if not isinstance(p, dict):
                            continue
                        code = str(p.get("provider") or "").strip()
                        if not code:
                            continue
                        pmap[code] = str(p.get("ids") or "").strip()
                provider_rows.append(
                    {
                        "address": str(row.get("address") or "").strip(),
                        "providers_map": pmap,
                    }
                )
    elif provider_resp.status_code != 404:
        provider_err = provider_resp.error

    department_items: List[dict] = []
    departments_error: Optional[str] = None
    if can_read_department:
        dept_resp = client.get(
            "/api/v1/department/",
            params={"limit": RMS_LIST_PAGE_LIMIT, "service_center_uid": uid},
        )
        if dept_resp.ok:
            department_items = _extract_departments_for_sc(dept_resp.data, uid)
        elif dept_resp.status_code == 404:
            department_items = []
        else:
            departments_error = dept_resp.error

    warehouse_items: List[Dict[str, Any]] = []
    warehouses_error: Optional[str] = None
    if can_read_warehouse:
        warehouse_items, warehouses_error = _fetch_warehouses_for_service_center(client, uid)

    slot_items: List[Any] = []
    slots_tab_error: Optional[str] = None
    slots_need_params = False
    slots_q_start = (request.args.get("slots_start_date") or request.args.get("start_date") or "").strip()
    slots_q_car = (request.args.get("slots_car") or request.args.get("car") or "").strip()
    slots_q_jobs = (request.args.get("slots_jobs") or request.args.get("jobs") or "").strip()

    if tab == "slots":
        if slots_q_start and slots_q_car and slots_q_jobs:
            job_tokens = [t.strip() for t in slots_q_jobs.split(",") if t.strip()]
            if not job_tokens:
                slots_tab_error = "Укажите хотя бы один идентификатор работы (через запятую)"
            else:
                try:
                    car_int = int(slots_q_car)
                except ValueError:
                    slots_tab_error = "Поле car должно быть целым числом (MDM)"
                else:
                    slot_params: Dict[str, Any] = {
                        "start_date": slots_q_start,
                        "car": car_int,
                        "jobs": job_tokens,
                    }
                    slots_resp = client.get(f"/api/v2/service_center/{uid}/slots", params=slot_params)
                    if slots_resp.ok:
                        slot_items = _extract_slots_payload(slots_resp.data)
                    elif slots_resp.status_code == 404:
                        slot_items = []
                    else:
                        slots_tab_error = slots_resp.error
        else:
            slots_need_params = True
            if not slots_q_start:
                slots_q_start = date.today().isoformat()

    errors = [
        f"service_center: {sc_resp.error}" if not sc_resp.ok and sc_resp.error else None,
        f"employees: {employees_resp.error}" if not employees_resp.ok and employees_resp.error else None,
        f"equipment: {equipment_resp.error}" if not equipment_resp.ok and equipment_resp.error else None,
        f"day_offs: {day_offs_resp.error}"
        if not day_offs_resp.ok and day_offs_resp.status_code != 404 and day_offs_resp.error
        else None,
        f"working_zones: {working_zones_error}" if working_zones_error else None,
        f"departments: {departments_error}" if departments_error else None,
        f"providers: {provider_err}" if provider_err else None,
        f"warehouses: {warehouses_error}" if warehouses_error else None,
    ]

    can_mutate_franchise = has_permission("service_center.update") or has_permission("franchisee.update")
    can_manage_day_off = has_permission("service_center.update") or has_permission("service_center.read")
    franchisee_options: List[Dict[str, str]] = []
    if sc_resp.ok and can_mutate_franchise and has_permission("franchisee.read"):
        franchisee_options = _fetch_franchisee_select_options(client)

    return render_template(
        "service_centers/detail.html",
        uid=uid,
        tab=tab,
        service_center=sc_data,
        schedule_items=schedule_items,
        employee_items=employee_items,
        equipment_items=equipment_items,
        can_manage_equipment=can_manage_equipment,
        can_manage_external_employee=can_manage_external_employee,
        employee_resource_options=employee_resource_options,
        external_employee_resource_options=external_employee_resource_options,
        external_employee_items=external_employee_items,
        equipment_resource_options=equipment_resource_options,
        day_off_items=day_off_items,
        slot_items=slot_items,
        slots_need_params=slots_need_params,
        slots_tab_error=slots_tab_error,
        slots_q_start=slots_q_start,
        slots_q_car=slots_q_car,
        slots_q_jobs=slots_q_jobs,
        errors=[e for e in errors if e],
        sc_msg=request.args.get("sc_msg"),
        sc_msg_type=request.args.get("sc_msg_type") or "info",
        can_mutate_sc=has_permission("service_center.update"),
        can_manage_day_off=can_manage_day_off,
        can_mutate_franchise=can_mutate_franchise,
        franchisee_options=franchisee_options,
        can_read_department=can_read_department,
        can_create_department=(
            has_permission("department.create")
            or has_permission("service_center.update")
            or has_permission("service_center.read")
        ),
        can_update_department=has_permission("department.update"),
        can_delete_department=has_permission("department.delete"),
        department_items=department_items,
        can_read_working_zone=can_read_working_zone,
        can_manage_working_zone=can_manage_working_zone,
        working_zone_items=working_zone_items if isinstance(working_zone_items, list) else [],
        working_zones_page=working_zones_page,
        working_zone_resource_options=working_zone_resource_options,
        provider_rows=provider_rows,
        provider_codes=PROVIDER_CODES,
        can_read_warehouse=can_read_warehouse,
        warehouse_items=warehouse_items,
    )


@bp.post("/<uid>/franchise/attach")
@require_any_permission("service_center.update", "franchisee.update")
def franchise_attach(uid: str):
    fid = (request.form.get("franchisee_uid") or "").strip()
    if not fid:
        return redirect(_redirect_detail(uid, "overview", "Выберите или укажите франчайзи", "error"))
    client = RMSClient()
    resp = client.post(f"/api/v1/service_center/{uid}/franchise", json={"franchisee_uid": fid})
    if resp.ok:
        return redirect(_redirect_detail(uid, "overview", "Сервисный центр привязан к франчайзи", "success"))
    return redirect(_redirect_detail(uid, "overview", resp.error or "Ошибка привязки", "error"))


@bp.post("/<uid>/franchise/detach")
@require_any_permission("service_center.update", "franchisee.update")
def franchise_detach(uid: str):
    client = RMSClient()
    resp = client.delete(f"/api/v1/service_center/{uid}/franchise")
    if resp.ok:
        return redirect(_redirect_detail(uid, "overview", "Сервисный центр отвязан от франчайзи", "success"))
    return redirect(_redirect_detail(uid, "overview", resp.error or "Ошибка отвязки", "error"))


@bp.post("/<uid>/update")
@require_permission("service_center.update")
def service_center_v2_update(uid: str):
    name = (request.form.get("name") or "").strip()
    img = request.files.get("image")
    has_file = bool(img and getattr(img, "filename", None))
    if not name and not has_file:
        return redirect(
            _redirect_detail(uid, "overview", "Укажите новое название и/или выберите изображение", "error")
        )

    data = {"name": name} if name else None
    files = None
    if has_file:
        files = {
            "image": (
                img.filename,
                img.stream,
                img.mimetype or "application/octet-stream",
            )
        }

    client = RMSClient()
    resp = client.post_multipart(f"/api/v2/service_center/{uid}", data=data, files=files)
    if resp.ok:
        return redirect(_redirect_detail(uid, "overview", "Данные СЦ обновлены", "success"))
    return redirect(_redirect_detail(uid, "overview", resp.error or "Ошибка обновления СЦ", "error"))


@bp.post("/<uid>/schedule/create")
@require_permission("service_center.update")
def schedule_create(uid: str):
    week_day = request.form.get("week_day", "").strip()
    start_time = (request.form.get("start_time") or "").strip()
    end_time = (request.form.get("end_time") or "").strip()
    if not week_day or not start_time or not end_time:
        return redirect(_redirect_detail(uid, "schedule", "Заполните день недели и время", "error"))
    try:
        wd = int(week_day)
    except ValueError:
        return redirect(_redirect_detail(uid, "schedule", "Некорректный день недели", "error"))
    body = {"schedule": [{"week_day": wd, "start_time": start_time, "end_time": end_time}]}
    client = RMSClient()
    resp = client.post(f"/api/v1/service_center/{uid}/schedule", json=body)
    if resp.ok:
        return redirect(_redirect_detail(uid, "schedule", "Расписание добавлено", "success"))
    return redirect(_redirect_detail(uid, "schedule", resp.error or "Ошибка создания", "error"))


@bp.post("/<uid>/schedule/update")
@require_permission("service_center.update")
def schedule_update(uid: str):
    week_day = request.form.get("week_day", "").strip()
    start_time = (request.form.get("start_time") or "").strip()
    end_time = (request.form.get("end_time") or "").strip()
    if not week_day or not start_time or not end_time:
        return redirect(_redirect_detail(uid, "schedule", "Заполните день недели и время", "error"))
    try:
        wd = int(week_day)
    except ValueError:
        return redirect(_redirect_detail(uid, "schedule", "Некорректный день недели", "error"))
    body = {"schedule": [{"week_day": wd, "start_time": start_time, "end_time": end_time}]}
    client = RMSClient()
    resp = client.patch(f"/api/v1/service_center/{uid}/schedule", json=body)
    if resp.ok:
        return redirect(_redirect_detail(uid, "schedule", "Расписание обновлено", "success"))
    return redirect(_redirect_detail(uid, "schedule", resp.error or "Ошибка обновления", "error"))


@bp.post("/<uid>/schedule/delete")
@require_permission("service_center.update")
def schedule_delete(uid: str):
    sch_uid = (request.form.get("sch_uid") or "").strip()
    if not sch_uid:
        return redirect(_redirect_detail(uid, "schedule", "Укажите UUID расписания для удаления", "error"))
    client = RMSClient()
    resp = client.delete(f"/api/v1/service_center/{uid}/schedule/{sch_uid}")
    if resp.ok:
        return redirect(_redirect_detail(uid, "schedule", "Расписание удалено", "success"))
    return redirect(_redirect_detail(uid, "schedule", resp.error or "Ошибка удаления", "error"))


@bp.post("/<uid>/day_off/create")
@require_any_permission("service_center.update", "service_center.read")
def day_off_create(uid: str):
    start_raw = (request.form.get("start_datetime") or "").strip()
    end_raw = (request.form.get("end_datetime") or "").strip()
    off_type = (request.form.get("off_type") or "").strip() or None
    if not start_raw or not end_raw:
        return redirect(_redirect_detail(uid, "day_offs", "Укажите начало и конец периода", "error"))
    start_dt = start_raw.replace("T", " ")[:16]
    end_dt = end_raw.replace("T", " ")[:16]
    body = {"start_datetime": start_dt, "end_datetime": end_dt}
    if off_type:
        body["off_type"] = off_type
    client = RMSClient()
    resp = client.post(f"/api/v1/service_center/{uid}/day_off", json=body)
    if resp.ok:
        return redirect(_redirect_detail(uid, "day_offs", "Выходной период создан", "success"))
    return redirect(_redirect_detail(uid, "day_offs", resp.error or "Ошибка создания", "error"))


@bp.post("/<uid>/day_off/<off_day_uid>/delete")
@require_any_permission("service_center.update", "service_center.read")
def day_off_delete(uid: str, off_day_uid: str):
    client = RMSClient()
    resp = client.delete(f"/api/v1/service_center/{uid}/day_off/{off_day_uid}")
    if resp.ok:
        return redirect(_redirect_detail(uid, "day_offs", "Выходной период удален", "success"))
    return redirect(_redirect_detail(uid, "day_offs", resp.error or "Ошибка удаления", "error"))


@bp.post("/<uid>/working_zones/create")
@require_any_permission("service_center.update", "service_center.read")
def working_zone_create(uid: str):
    resource_type_uid = (request.form.get("resource_type_uid") or "").strip()
    description = (request.form.get("description") or "").strip()
    if not resource_type_uid:
        return redirect(_redirect_detail(uid, "working_zones", "Выберите тип ресурса рабочей зоны", "error"))
    body: Dict[str, Any] = {"resource_type_uid": resource_type_uid}
    if description:
        body["description"] = description
    client = RMSClient()
    resp = client.post(f"/api/v1/service_center/{uid}/working_zone", json=body)
    if resp.ok:
        return redirect(_redirect_detail(uid, "working_zones", "Рабочая зона создана", "success"))
    return redirect(_redirect_detail(uid, "working_zones", resp.error or "Ошибка создания рабочей зоны", "error"))


@bp.post("/<uid>/working_zones/<wz_uid>/delete")
@require_any_permission("service_center.update", "service_center.read")
def working_zone_delete(uid: str, wz_uid: str):
    client = RMSClient()
    resp = client.patch(
        f"/api/v1/service_center/{uid}/working_zone/{wz_uid}",
        json={"is_available": False},
    )
    if resp.ok:
        return redirect(_redirect_detail(uid, "working_zones", "Рабочая зона отключена", "success"))
    err = (resp.error or "").lower()
    if resp.status_code == 409 and ("соответствует" in err or "already" in err):
        return redirect(_redirect_detail(uid, "working_zones", "Рабочая зона уже отключена", "info"))
    return redirect(_redirect_detail(uid, "working_zones", resp.error or "Ошибка удаления рабочей зоны", "error"))


def _extract_slots_payload(payload: Any) -> list:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    return data if isinstance(data, list) else []


def _extract_day_offs(payload) -> list:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("day_offs", "off_days", "items"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def _extract_object(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if isinstance(data, dict):
        if "service_center" in data and isinstance(data["service_center"], dict):
            return data["service_center"]
        return data
    return {}


def _extract_list(payload: dict, keys: list[str]) -> list:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data", {})
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


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


def _extract_service_center_uid_from_warehouse(row: Dict[str, Any]) -> str:
    sc = row.get("service_center")
    if isinstance(sc, dict):
        sc_uid = str(sc.get("service_center_uid") or sc.get("uid") or "").strip()
        if sc_uid:
            return sc_uid
    return str(row.get("rms_uid") or "").strip()


def _fetch_warehouses_for_service_center(
    client: RMSClient, service_center_uid: str
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    items: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    for _ in range(WAREHOUSE_SCAN_MAX_PAGES):
        params: Dict[str, Any] = {"limit": RMS_LIST_PAGE_LIMIT}
        if cursor:
            params["cursor"] = cursor
        resp = client.get("/api/v1/warehouse", params=params)
        if not resp.ok:
            if resp.status_code == 404:
                return [], None
            return [], resp.error
        payload = resp.data if isinstance(resp.data, dict) else {}
        rows = payload.get("data")
        if not isinstance(rows, list):
            return [], "Некорректный формат ответа RMS по складам"
        for row in rows:
            if not isinstance(row, dict):
                continue
            if _extract_service_center_uid_from_warehouse(row) == service_center_uid:
                items.append(row)
        if not payload.get("has_more"):
            break
        nxt = payload.get("cursor")
        if not nxt:
            break
        cursor = str(nxt)
    return items, None


def _extract_departments_for_sc(payload: Any, sc_uid: str) -> List[dict]:
    rows: List[dict] = []
    if not isinstance(payload, dict):
        return rows
    data = payload.get("data")
    if not isinstance(data, list):
        return rows
    for block in data:
        if not isinstance(block, dict):
            continue
        block_sc_uid = str(block.get("service_center_uid") or "").strip()
        if block_sc_uid and block_sc_uid != sc_uid:
            continue
        depts = block.get("departments")
        if not isinstance(depts, list):
            continue
        for d in depts:
            if not isinstance(d, dict):
                continue
            rows.append(
                {
                    "uid": str(d.get("uid") or "").strip(),
                    "name": str(d.get("name") or "").strip(),
                    "external_uid": str(d.get("external_uid") or "").strip(),
                }
            )
    return rows


@bp.post("/<uid>/departments/create")
@require_any_permission("department.create", "service_center.update", "service_center.read")
def department_create(uid: str):
    name = (request.form.get("name") or "").strip()
    external_uid = (request.form.get("external_uid") or "").strip()
    if not name:
        return redirect(_redirect_detail(uid, "departments", "Укажите название отдела", "error"))
    if not external_uid:
        return redirect(
            _redirect_detail(uid, "departments", "Укажите external_uid (UUID отдела в 1С)", "error")
        )
    client = RMSClient()
    body: Dict[str, Any] = {
        "name": name,
        "service_center_uid": uid,
        "external_uid": external_uid,
    }
    resp = client.post("/api/v1/department", json=body)
    if resp.ok:
        return redirect(_redirect_detail(uid, "departments", "Отдел создан", "success"))
    return redirect(_redirect_detail(uid, "departments", resp.error or "Ошибка создания отдела", "error"))


@bp.post("/<uid>/departments/<department_uid>/patch")
@require_permission("department.update")
def department_patch(uid: str, department_uid: str):
    name = (request.form.get("name") or "").strip()
    external_uid = (request.form.get("external_uid") or "").strip()
    body: Dict[str, Any] = {}
    if name:
        body["name"] = name
    if external_uid:
        body["external_uid"] = external_uid
    if not body:
        return redirect(_redirect_detail(uid, "departments", "Укажите поле для обновления", "error"))
    client = RMSClient()
    resp = client.patch(f"/api/v1/department/{department_uid}", json=body)
    if resp.ok:
        return redirect(_redirect_detail(uid, "departments", "Отдел обновлён", "success"))
    return redirect(_redirect_detail(uid, "departments", resp.error or "Ошибка обновления отдела", "error"))


@bp.post("/<uid>/departments/<department_uid>/delete")
@require_permission("department.delete")
def department_delete(uid: str, department_uid: str):
    client = RMSClient()
    resp = client.delete(f"/api/v1/department/{department_uid}")
    if resp.ok:
        return redirect(_redirect_detail(uid, "departments", "Отдел удалён", "success"))
    return redirect(_redirect_detail(uid, "departments", resp.error or "Ошибка удаления отдела", "error"))


@bp.post("/<uid>/employee/create")
@require_permission("service_center.update")
def employee_create(uid: str):
    sso_id = (request.form.get("sso_id") or "").strip()
    resource_type_uid = (request.form.get("resource_type_uid") or "").strip()
    contract_due_date = (request.form.get("contract_due_date") or "").strip()
    if not sso_id or not resource_type_uid:
        return redirect(_redirect_detail(uid, "employees", "Укажите SSO ID и resource_type_uid", "error"))
    body: dict = {"sso_id": sso_id, "resource_type_uid": resource_type_uid}
    if contract_due_date:
        body["contract_due_date"] = contract_due_date
    client = RMSClient()
    resp = client.post(f"/api/v2/service_center/{uid}/employee", json=body)
    if resp.ok:
        return redirect(_redirect_detail(uid, "employees", "Сотрудник добавлен", "success"))
    return redirect(_redirect_detail(uid, "employees", resp.error or "Ошибка добавления сотрудника", "error"))


@bp.post("/<uid>/external_employee/create")
@require_permission("service_center.update")
def external_employee_create(uid: str):
    sso_id = (request.form.get("sso_id") or "").strip()
    resource_type_uid = (request.form.get("resource_type_uid") or "").strip()
    contract_due_date = (request.form.get("contract_due_date") or "").strip()
    if not sso_id or not resource_type_uid:
        return redirect(
            _redirect_detail(
                uid,
                "external_employees",
                "Укажите SSO ID и resource_type_uid для внешнего сотрудника",
                "error",
            )
        )
    body: Dict[str, Any] = {
        "sso_id": sso_id,
        "resource_type_uid": resource_type_uid,
        "is_external": True,
    }
    if contract_due_date:
        body["contract_due_date"] = contract_due_date
    client = RMSClient()
    resp = client.post(f"/api/v2/service_center/{uid}/employee", json=body)
    if resp.ok:
        return redirect(_redirect_detail(uid, "external_employees", "Внешний сотрудник добавлен", "success"))
    return redirect(
        _redirect_detail(uid, "external_employees", resp.error or "Ошибка добавления внешнего сотрудника", "error")
    )


@bp.post("/<uid>/external_employee/<employee_uid>/patch")
@require_permission("service_center.update")
def external_employee_patch(uid: str, employee_uid: str):
    resource_type_uid = (request.form.get("resource_type_uid") or "").strip()
    contract_due_date = (request.form.get("contract_due_date") or "").strip()
    body: Dict[str, Any] = {}
    if resource_type_uid:
        body["resource_type_uid"] = resource_type_uid
    if contract_due_date:
        body["contract_due_date"] = contract_due_date
    if not body:
        return redirect(
            _redirect_detail(
                uid,
                "external_employees",
                "Укажите хотя бы одно поле для обновления внешнего сотрудника",
                "error",
            )
        )
    client = RMSClient()
    resp = client.patch(f"/api/v1/service_center/{uid}/employee/{employee_uid}", json=body)
    if not resp.ok:
        resp = client.patch(f"/api/v2/service_center/{uid}/employee/{employee_uid}", json=body)
    if resp.ok:
        return redirect(_redirect_detail(uid, "external_employees", "Внешний сотрудник обновлён", "success"))
    return redirect(
        _redirect_detail(uid, "external_employees", resp.error or "Ошибка обновления внешнего сотрудника", "error")
    )


@bp.post("/<uid>/external_employee/<employee_uid>/disable")
@require_permission("service_center.update")
def external_employee_disable(uid: str, employee_uid: str):
    client = RMSClient()
    resp = client.patch(
        f"/api/v1/service_center/{uid}/employee/{employee_uid}",
        json={"is_available": False},
    )
    if not resp.ok:
        resp = client.patch(
            f"/api/v2/service_center/{uid}/employee/{employee_uid}",
            json={"is_available": False},
        )
    if resp.ok:
        return redirect(_redirect_detail(uid, "external_employees", "Внешний сотрудник отключён", "success"))
    err = (resp.error or "").lower()
    if resp.status_code == 409 and ("соответствует" in err or "already" in err):
        return redirect(_redirect_detail(uid, "external_employees", "Внешний сотрудник уже отключён", "info"))
    return redirect(
        _redirect_detail(uid, "external_employees", resp.error or "Ошибка отключения внешнего сотрудника", "error")
    )


@bp.post("/<uid>/equipment/<eq_uid>/patch")
@require_permission("service_center.update")
def equipment_patch(uid: str, eq_uid: str):
    name = (request.form.get("name") or "").strip()
    is_available_raw = (request.form.get("is_available") or "").strip()
    body: dict = {}
    if name:
        body["name"] = name
    if is_available_raw in ("1", "0", "true", "false"):
        body["is_available"] = is_available_raw in ("1", "true")
    if not body:
        return redirect(_redirect_detail(uid, "equipment", "Укажите имя или измените доступность", "error"))
    client = RMSClient()
    resp = client.patch(f"/api/v1/service_center/{uid}/equipment/{eq_uid}", json=body)
    if resp.ok:
        return redirect(_redirect_detail(uid, "equipment", "Оборудование обновлено", "success"))
    return redirect(_redirect_detail(uid, "equipment", resp.error or "Ошибка обновления", "error"))


@bp.post("/<uid>/equipment/create")
@require_any_permission("service_center.update", "service_center.read")
def equipment_create(uid: str):
    resource_type_uid = (request.form.get("resource_type_uid") or "").strip()
    name = (request.form.get("name") or "").strip()
    if not resource_type_uid:
        return redirect(_redirect_detail(uid, "equipment", "Выберите тип ресурса оборудования", "error"))
    body: Dict[str, Any] = {
        "service_center_uid": uid,
        "resource_type_uid": resource_type_uid,
        "is_external": False,
    }
    if name:
        body["name"] = name
    client = RMSClient()
    resp = client.post("/api/v2/service_center/equipment", json=body)
    if resp.ok:
        return redirect(_redirect_detail(uid, "equipment", "Оборудование создано", "success"))
    return redirect(_redirect_detail(uid, "equipment", resp.error or "Ошибка создания оборудования", "error"))


@bp.post("/<uid>/equipment/<eq_uid>/delete")
@require_any_permission("service_center.update", "service_center.read")
def equipment_delete(uid: str, eq_uid: str):
    client = RMSClient()
    resp = client.patch(
        f"/api/v1/service_center/{uid}/equipment/{eq_uid}",
        json={"is_available": False},
    )
    if resp.ok:
        return redirect(_redirect_detail(uid, "equipment", "Оборудование отключено", "success"))
    err = (resp.error or "").lower()
    if resp.status_code == 409 and ("соответствует" in err or "already" in err):
        return redirect(_redirect_detail(uid, "equipment", "Оборудование уже отключено", "info"))
    return redirect(_redirect_detail(uid, "equipment", resp.error or "Ошибка удаления оборудования", "error"))
