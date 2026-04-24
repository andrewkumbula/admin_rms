import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from flask import Blueprint, redirect, render_template, request, url_for

from app.list_limits import RMS_LIST_PAGE_LIMIT
from app.rbac.decorators import has_permission, require_permission
from app.rms_client.client import RMSClient
from app.service_center_catalog import fetch_service_center_options, label_for_sc_uid


bp = Blueprint("dictionaries", __name__, url_prefix="/dictionaries")

# Значения как в App\Enum\ResourceTypeEnum (родительский тип в RMS)
PARENT_RESOURCE_TYPES = (
    "Сотрудник",
    "Внешний сотрудник",
    "Оборудование",
    "Диагностическое оборудование",
    "Внешнее оборудование",
    "Зона ремонта",
    "Мойка",
)


# Сколько брендов/работ подтянуть в выпадающие списки (несколько страниц API)
_TECH_CARD_FILTER_OPTIONS_MAX = 4000
_TECH_CARD_SCAN_MAX = 20000
_EXTERNAL_EMPLOYEE_SCAN_MAX = 4000
_REPLACEMENT_TYPE_SCAN_MAX = 5000
_GLOBAL_SCHEDULE_SCAN_MAX = 500
_JSON_DIR = Path(__file__).resolve().parents[2] / "JSON"


def _fetch_paginated_top_level(client: RMSClient, path: str, max_total: int) -> List[dict]:
    """GET endpoints с полями data, cursor, has_more на корне ответа (brands, works)."""
    acc: List[dict] = []
    cursor: Optional[str] = None
    while len(acc) < max_total:
        chunk_limit = min(RMS_LIST_PAGE_LIMIT, max_total - len(acc))
        params: dict[str, Any] = {"limit": chunk_limit}
        if cursor:
            params["cursor"] = cursor
        resp = client.get(path, params=params)
        if not resp.ok:
            break
        payload = resp.data if isinstance(resp.data, dict) else {}
        data = payload.get("data")
        chunk = data if isinstance(data, list) else []
        acc.extend([x for x in chunk if isinstance(x, dict)])
        if not bool(payload.get("has_more")):
            break
        nxt = str(payload.get("cursor") or "").strip()
        if not nxt:
            break
        cursor = nxt
    return acc


def _brand_option_label(row: dict) -> str:
    return str(row.get("brand_name") or row.get("name") or row.get("uid") or "—")


def _work_option_label(row: dict) -> str:
    return str(row.get("name") or row.get("uid") or "—")


def _fetch_external_employee_resource_options(client: RMSClient) -> List[Dict[str, str]]:
    rows = _fetch_paginated_top_level(client, "/api/v1/resource_type", _TECH_CARD_FILTER_OPTIONS_MAX)
    out: List[Dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        uid = str(row.get("uid") or "").strip()
        if not uid or uid in seen:
            continue
        parent = str(row.get("parent_resource_type") or "").strip()
        if parent.casefold() != "внешний сотрудник":
            continue
        child = str(row.get("children_resource_type") or "").strip()
        label = f"{child} ({parent})" if child and parent else (child or parent or uid)
        seen.add(uid)
        out.append({"uid": uid, "label": label})
    out.sort(key=lambda x: str(x.get("label") or "").casefold())
    return out


def _extract_employee_list(payload: Any) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("employees", "items", "resources"):
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def _fetch_external_employees_all(client: RMSClient, max_total: int) -> tuple[List[Dict[str, Any]], bool, Optional[str]]:
    acc: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    truncated = False
    while len(acc) < max_total:
        chunk_limit = min(RMS_LIST_PAGE_LIMIT, max_total - len(acc))
        params: Dict[str, Any] = {"limit": chunk_limit}
        if cursor:
            params["cursor"] = cursor
        # Просим API сразу вернуть внешних, но дополнительно фильтруем локально.
        params["is_external"] = True
        resp = client.get("/api/v1/employee/external", params=params)
        if not resp.ok:
            return acc, truncated, resp.error
        payload = resp.data if isinstance(resp.data, dict) else {}
        chunk = _extract_employee_list(payload)
        for row in chunk:
            if not bool(row.get("is_external")):
                continue
            acc.append(row)
            if len(acc) >= max_total:
                truncated = bool(payload.get("has_more"))
                break
        if truncated:
            break
        if not bool(payload.get("has_more")):
            break
        nxt = str(payload.get("cursor") or "").strip()
        if not nxt:
            break
        cursor = nxt
    if len(acc) >= max_total:
        truncated = True
    return acc, truncated, None


def _redirect_external_employees(msg: Optional[str] = None, msg_type: str = "info") -> str:
    q: Dict[str, str] = {}
    if msg:
        q["ee_msg"] = msg
        q["ee_msg_type"] = msg_type
    return url_for("dictionaries.external_employees", **q)


def _redirect_replacement_types(msg: Optional[str] = None, msg_type: str = "info") -> str:
    q: Dict[str, str] = {}
    if msg:
        q["rp_msg"] = msg
        q["rp_msg_type"] = msg_type
    return url_for("dictionaries.replacement_types", **q)


def _redirect_global_schedule(msg: Optional[str] = None, msg_type: str = "info") -> str:
    q: Dict[str, str] = {}
    if msg:
        q["gs_msg"] = msg
        q["gs_msg_type"] = msg_type
    return url_for("dictionaries.global_schedule", **q)


def _fetch_global_schedule_all(
    client: RMSClient, max_total: int
) -> tuple[List[Dict[str, Any]], bool, Optional[str]]:
    acc: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    truncated = False
    while len(acc) < max_total:
        chunk_limit = min(RMS_LIST_PAGE_LIMIT, max_total - len(acc))
        params: Dict[str, Any] = {"limit": chunk_limit}
        if cursor:
            params["cursor"] = cursor
        resp = client.get("/api/v1/global_schedule", params=params)
        if not resp.ok:
            if resp.status_code == 405:
                local_rows = _load_global_schedule_local()
                if local_rows:
                    return local_rows[:max_total], len(local_rows) > max_total, (
                        "RMS API не поддерживает GET /api/v1/global_schedule; "
                        "показаны локальные данные из JSON."
                    )
                return [], False, (
                    "RMS API не поддерживает чтение global schedule (GET /api/v1/global_schedule). "
                    "Доступны только операции create/update."
                )
            if resp.status_code == 404:
                return acc, truncated, None
            return acc, truncated, resp.error
        payload = resp.data if isinstance(resp.data, dict) else {}
        data = payload.get("data")
        chunk = data if isinstance(data, list) else []
        for row in chunk:
            if not isinstance(row, dict):
                continue
            acc.append(row)
            if len(acc) >= max_total:
                truncated = bool(payload.get("has_more"))
                break
        if truncated:
            break
        if not bool(payload.get("has_more")):
            break
        nxt = str(payload.get("cursor") or "").strip()
        if not nxt:
            break
        cursor = nxt
    if len(acc) >= max_total:
        truncated = True
    return acc, truncated, None


def _load_global_schedule_local() -> List[Dict[str, Any]]:
    path = _JSON_DIR / "global_schedules.json"
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    if not isinstance(raw, list):
        return []
    rows = [r for r in raw if isinstance(r, dict)]
    rows.sort(key=lambda x: int(x.get("week_day") or 0))
    return rows


def _parse_schedule_form_values(form: Dict[str, str]) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    week_day_raw = (form.get("week_day") or "").strip()
    start_time = (form.get("start_time") or "").strip()
    end_time = (form.get("end_time") or "").strip()
    if not week_day_raw or not start_time or not end_time:
        return None, "Заполните день недели, начало и конец"
    try:
        week_day = int(week_day_raw)
    except ValueError:
        return None, "День недели должен быть целым числом от 1 до 7"
    if week_day < 1 or week_day > 7:
        return None, "День недели должен быть в диапазоне 1..7"
    try:
        start_dt = datetime.strptime(start_time, "%H:%M")
        end_dt = datetime.strptime(end_time, "%H:%M")
    except ValueError:
        return None, "Формат времени должен быть HH:MM"
    if start_dt >= end_dt:
        return None, "Время начала должно быть раньше времени окончания"
    return {
        "week_day": week_day,
        "start_time": f"{start_time}:00",
        "end_time": f"{end_time}:00",
    }, None


def _fetch_global_holidays_all(client: RMSClient, max_total: int) -> tuple[List[Dict[str, Any]], bool, Optional[str]]:
    acc: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    truncated = False
    while len(acc) < max_total:
        chunk_limit = min(RMS_LIST_PAGE_LIMIT, max_total - len(acc))
        params: Dict[str, Any] = {"limit": chunk_limit}
        if cursor:
            params["cursor"] = cursor
        resp = client.get("/api/v1/holiday", params=params)
        if not resp.ok:
            if resp.status_code in (404, 405):
                local_rows = _load_global_holidays_local()
                if local_rows:
                    return local_rows[:max_total], len(local_rows) > max_total, (
                        "RMS API не поддерживает чтение holiday (GET /api/v1/holiday); "
                        "показаны локальные данные из JSON."
                    )
                return [], False, (
                    "RMS API не поддерживает чтение holiday (GET /api/v1/holiday). "
                    "Доступно только добавление через POST."
                )
            return acc, truncated, resp.error
        payload = resp.data if isinstance(resp.data, dict) else {}
        data = payload.get("data")
        chunk = data if isinstance(data, list) else []
        for row in chunk:
            if not isinstance(row, dict):
                continue
            acc.append(row)
            if len(acc) >= max_total:
                truncated = bool(payload.get("has_more"))
                break
        if truncated:
            break
        if not bool(payload.get("has_more")):
            break
        nxt = str(payload.get("cursor") or "").strip()
        if not nxt:
            break
        cursor = nxt
    if len(acc) >= max_total:
        truncated = True
    acc.sort(key=lambda x: str(x.get("date") or ""))
    return acc, truncated, None


def _load_global_holidays_local() -> List[Dict[str, Any]]:
    path = _JSON_DIR / "hollidays.json"
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    if not isinstance(raw, list):
        return []
    rows = [r for r in raw if isinstance(r, dict) and not bool(r.get("is_deleted"))]
    rows.sort(key=lambda x: str(x.get("date") or ""))
    return rows


def _parse_holiday_form_values(form: Dict[str, str]) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    date_raw = (form.get("holiday_date") or "").strip()
    reason = (form.get("holiday_reason") or "").strip()
    if not date_raw:
        return None, "Укажите дату глобального выходного"
    try:
        datetime.strptime(date_raw, "%Y-%m-%d")
    except ValueError:
        return None, "Дата должна быть в формате YYYY-MM-DD"
    return {"date": date_raw, "reason": reason or None}, None


def _fetch_replacement_types_all(
    client: RMSClient,
    is_active: bool,
    max_total: int,
    replacing_uid: str = "",
    replaced_uid: str = "",
) -> tuple[List[Dict[str, Any]], bool, Optional[str]]:
    acc: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    truncated = False
    while len(acc) < max_total:
        chunk_limit = min(RMS_LIST_PAGE_LIMIT, max_total - len(acc))
        params: Dict[str, Any] = {"limit": chunk_limit, "is_active": is_active}
        if cursor:
            params["cursor"] = cursor
        resp = client.get("/api/v1/replacement_type", params=params)
        if not resp.ok:
            if resp.status_code == 404:
                return acc, truncated, None
            return acc, truncated, resp.error
        payload = resp.data if isinstance(resp.data, dict) else {}
        data = payload.get("data")
        chunk = data if isinstance(data, list) else []
        for row in chunk:
            if not isinstance(row, dict):
                continue
            rep = row.get("replacing_resource") if isinstance(row.get("replacing_resource"), dict) else {}
            red = row.get("replaced_resource") if isinstance(row.get("replaced_resource"), dict) else {}
            rep_uid = str(rep.get("resource_type_uid") or "").strip()
            red_uid = str(red.get("resource_type_uid") or "").strip()
            if replacing_uid and rep_uid != replacing_uid:
                continue
            if replaced_uid and red_uid != replaced_uid:
                continue
            acc.append(row)
            if len(acc) >= max_total:
                truncated = bool(payload.get("has_more"))
                break
        if truncated:
            break
        if not bool(payload.get("has_more")):
            break
        nxt = str(payload.get("cursor") or "").strip()
        if not nxt:
            break
        cursor = nxt
    if len(acc) >= max_total:
        truncated = True
    return acc, truncated, None


def _fetch_tech_cards_all_pages(
    client: RMSClient,
    brand_uid: str,
    work_uid: str,
    search_text: str,
    max_total: int,
) -> tuple[List[dict], bool, Optional[str]]:
    """
    Обход всех страниц /api/v1/tech_specification_list с локальной фильтрацией по UID.
    Возвращает (items, truncated, error).
    """
    acc: List[dict] = []
    cursor: Optional[str] = None
    truncated = False
    search_norm = search_text.casefold()
    while len(acc) < max_total:
        chunk_limit = min(RMS_LIST_PAGE_LIMIT, max_total - len(acc))
        params: dict[str, Any] = {"limit": chunk_limit}
        if cursor:
            params["cursor"] = cursor
        # Читаем сырые страницы без серверных фильтров RMS, чтобы локально отфильтровать
        # одинаково корректно (в т.ч. для техкарт без бренда).
        resp = client.get("/api/v1/tech_specification_list", params=params)
        if not resp.ok:
            return acc, truncated, resp.error
        payload = resp.data if isinstance(resp.data, dict) else {}
        chunk_raw = payload.get("data")
        chunk = chunk_raw if isinstance(chunk_raw, list) else []
        for row in chunk:
            if not isinstance(row, dict):
                continue
            row_brand = str(row.get("brand_uid") or "").strip()
            row_work = str(row.get("work_uid") or "").strip()
            if brand_uid and row_brand != brand_uid:
                continue
            if work_uid and row_work != work_uid:
                continue
            if search_norm:
                haystack = " ".join(
                    [
                        str(row.get("work_name") or row.get("name") or ""),
                        str(row.get("brand_name") or ""),
                        row_work,
                        row_brand,
                    ]
                ).casefold()
                if search_norm not in haystack:
                    continue
            acc.append(row)
            if len(acc) >= max_total:
                truncated = bool(payload.get("has_more"))
                break
        if truncated:
            break
        if not bool(payload.get("has_more")):
            break
        nxt = str(payload.get("cursor") or "").strip()
        if not nxt:
            break
        cursor = nxt
    if len(acc) >= max_total:
        truncated = True
    return acc, truncated, None


def _redirect_resource_types(msg: Optional[str] = None, msg_type: str = "info") -> str:
    q: Dict[str, str] = {}
    if msg:
        q["rt_msg"] = msg
        q["rt_msg_type"] = msg_type
    return url_for("dictionaries.resource_types", **q)


@bp.get("")
@require_permission("dictionary.resource_type.read")
def index():
    return render_template("dictionaries/index.html")


@bp.get("/external_employees")
@require_permission("service_center.read")
def external_employees():
    client = RMSClient()
    items, truncated, err = _fetch_external_employees_all(client, _EXTERNAL_EMPLOYEE_SCAN_MAX)
    resource_options = _fetch_external_employee_resource_options(client)
    if not err and truncated:
        err = (
            f"Показаны первые {_EXTERNAL_EMPLOYEE_SCAN_MAX} внешних сотрудников. "
            "Уточните отбор на стороне RMS при необходимости."
        )
    return render_template(
        "dictionaries/external_employees.html",
        items=items,
        error=err,
        resource_type_options=resource_options,
        can_manage=has_permission("service_center.update"),
        ee_msg=request.args.get("ee_msg"),
        ee_msg_type=request.args.get("ee_msg_type") or "info",
    )


@bp.post("/external_employees/create")
@require_permission("service_center.update")
def external_employees_create():
    sso_id = (request.form.get("sso_id") or "").strip()
    resource_type_uid = (request.form.get("resource_type_uid") or "").strip()
    contract_due_date = (request.form.get("contract_due_date") or "").strip()
    if not sso_id or not resource_type_uid:
        return redirect(_redirect_external_employees("Укажите SSO ID и тип ресурса", "error"))
    body: Dict[str, Any] = {
        "sso_id": sso_id,
        "resource_type_uid": resource_type_uid,
        "is_external": True,
    }
    if contract_due_date:
        body["contract_due_date"] = contract_due_date
    client = RMSClient()
    resp = client.post("/api/v1/employee", json=body)
    if not resp.ok:
        resp = client.post("/api/v2/employee", json=body)
    if resp.ok:
        return redirect(_redirect_external_employees("Внешний сотрудник добавлен", "success"))
    return redirect(_redirect_external_employees(resp.error or "Ошибка создания внешнего сотрудника", "error"))


@bp.post("/external_employees/<employee_uid>/patch")
@require_permission("service_center.update")
def external_employees_patch(employee_uid: str):
    resource_type_uid = (request.form.get("resource_type_uid") or "").strip()
    contract_due_date = (request.form.get("contract_due_date") or "").strip()
    body: Dict[str, Any] = {}
    if resource_type_uid:
        body["resource_type_uid"] = resource_type_uid
    if contract_due_date:
        body["contract_due_date"] = contract_due_date
    if not body:
        return redirect(_redirect_external_employees("Укажите поле для обновления", "error"))
    client = RMSClient()
    resp = client.patch(f"/api/v1/employee/{employee_uid}", json=body)
    if not resp.ok:
        resp = client.patch(f"/api/v2/employee/{employee_uid}", json=body)
    if resp.ok:
        return redirect(_redirect_external_employees("Внешний сотрудник обновлён", "success"))
    return redirect(_redirect_external_employees(resp.error or "Ошибка обновления", "error"))


@bp.post("/external_employees/<employee_uid>/disable")
@require_permission("service_center.update")
def external_employees_disable(employee_uid: str):
    client = RMSClient()
    resp = client.patch(f"/api/v1/employee/{employee_uid}", json={"is_available": False})
    if not resp.ok:
        resp = client.patch(f"/api/v2/employee/{employee_uid}", json={"is_available": False})
    if resp.ok:
        return redirect(_redirect_external_employees("Внешний сотрудник отключён", "success"))
    err = (resp.error or "").lower()
    if resp.status_code == 409 and ("already" in err or "соответствует" in err):
        return redirect(_redirect_external_employees("Внешний сотрудник уже отключён", "info"))
    return redirect(_redirect_external_employees(resp.error or "Ошибка отключения", "error"))


@bp.get("/replacement_types")
@require_permission("dictionary.replacement_type.read")
def replacement_types():
    replacing_uid = (request.args.get("replacing_uid") or "").strip()
    replaced_uid = (request.args.get("replaced_uid") or "").strip()
    status_raw = (request.args.get("status") or "active").strip().lower()
    is_active = status_raw != "inactive"

    client = RMSClient()
    items, truncated, err = _fetch_replacement_types_all(
        client,
        is_active=is_active,
        max_total=_REPLACEMENT_TYPE_SCAN_MAX,
        replacing_uid=replacing_uid,
        replaced_uid=replaced_uid,
    )
    resource_type_options = _fetch_paginated_top_level(client, "/api/v1/resource_type", _TECH_CARD_FILTER_OPTIONS_MAX)
    rt_options: List[Dict[str, str]] = []
    seen_rt: set[str] = set()
    for row in resource_type_options:
        uid = str(row.get("uid") or "").strip()
        if not uid or uid in seen_rt:
            continue
        seen_rt.add(uid)
        rt_options.append({"uid": uid, "label": _resource_type_option_label(row)})
    rt_options.sort(key=lambda x: str(x.get("label") or "").casefold())

    if not err and truncated:
        err = (
            f"Показаны первые {_REPLACEMENT_TYPE_SCAN_MAX} связей. "
            "Уточните фильтры, чтобы сузить выборку."
        )

    return render_template(
        "dictionaries/replacement_types.html",
        items=items,
        error=err,
        rt_options=rt_options,
        selected_replacing_uid=replacing_uid,
        selected_replaced_uid=replaced_uid,
        selected_status=("inactive" if not is_active else "active"),
        can_create=has_permission("dictionary.replacement_type.create"),
        can_update=has_permission("dictionary.replacement_type.update"),
        can_delete=has_permission("dictionary.replacement_type.delete"),
        rp_msg=request.args.get("rp_msg"),
        rp_msg_type=request.args.get("rp_msg_type") or "info",
    )


@bp.post("/replacement_types/create")
@require_permission("dictionary.replacement_type.create")
def replacement_types_create():
    replacing_uid = (request.form.get("replacing_resource_type_uid") or "").strip()
    replaced_uid = (request.form.get("replaced_resource_type_uid") or "").strip()
    if not replacing_uid or not replaced_uid:
        return redirect(_redirect_replacement_types("Выберите оба типа ресурса", "error"))
    body = {
        "replacing_resource_type_uid": replacing_uid,
        "replaced_resource_type_uid": replaced_uid,
    }
    resp = RMSClient().post("/api/v1/replacement_type", json=body)
    if resp.ok:
        return redirect(_redirect_replacement_types("Связь взаимозаменяемости создана", "success"))
    return redirect(_redirect_replacement_types(resp.error or "Ошибка создания связи", "error"))


@bp.post("/replacement_types/<uid>/toggle")
@require_permission("dictionary.replacement_type.update")
def replacement_types_toggle(uid: str):
    is_active_raw = (request.form.get("is_active") or "").strip().lower()
    is_active = is_active_raw in {"1", "true", "yes", "on"}
    resp = RMSClient().patch(f"/api/v1/replacement_type/{uid}", json={"is_active": is_active})
    if resp.ok:
        return redirect(
            _redirect_replacement_types(
                "Связь активирована" if is_active else "Связь деактивирована",
                "success",
            )
        )
    return redirect(_redirect_replacement_types(resp.error or "Ошибка обновления связи", "error"))


@bp.post("/replacement_types/<uid>/delete")
@require_permission("dictionary.replacement_type.delete")
def replacement_types_delete(uid: str):
    resp = RMSClient().delete(f"/api/v1/replacement_type/{uid}")
    if resp.ok:
        return redirect(_redirect_replacement_types("Связь деактивирована", "success"))
    return redirect(_redirect_replacement_types(resp.error or "Ошибка удаления связи", "error"))


@bp.get("/global_schedule")
@require_permission("dictionary.global_schedule.read")
def global_schedule():
    client = RMSClient()
    items, truncated, err = _fetch_global_schedule_all(client, _GLOBAL_SCHEDULE_SCAN_MAX)
    holiday_items, holidays_truncated, holidays_err = _fetch_global_holidays_all(client, _GLOBAL_SCHEDULE_SCAN_MAX)
    items.sort(key=lambda x: int(x.get("week_day") or 0))
    if not err and truncated:
        err = (
            f"Показаны первые {_GLOBAL_SCHEDULE_SCAN_MAX} строк глобального расписания. "
            "Уточните фильтры на стороне RMS."
        )
    if not holidays_err and holidays_truncated:
        holidays_err = (
            f"Показаны первые {_GLOBAL_SCHEDULE_SCAN_MAX} глобальных выходных. "
            "Уточните фильтры на стороне RMS."
        )
    return render_template(
        "dictionaries/global_schedule.html",
        items=items,
        error=err,
        holiday_items=holiday_items,
        holidays_error=holidays_err,
        can_create=has_permission("dictionary.global_schedule.create"),
        can_update=has_permission("dictionary.global_schedule.update"),
        can_delete=has_permission("dictionary.global_schedule.delete"),
        gs_msg=request.args.get("gs_msg"),
        gs_msg_type=request.args.get("gs_msg_type") or "info",
    )


@bp.post("/global_schedule/create")
@require_permission("dictionary.global_schedule.create")
def global_schedule_create():
    body, err = _parse_schedule_form_values(request.form)
    if err:
        return redirect(_redirect_global_schedule(err, "error"))
    resp = RMSClient().post("/api/v1/global_schedule", json={"schedule": [body], "holidays": []})
    if resp.ok:
        return redirect(_redirect_global_schedule("Строка глобального расписания создана", "success"))
    return redirect(_redirect_global_schedule(resp.error or "Ошибка создания строки", "error"))


@bp.post("/global_schedule/<uid>/patch")
@require_permission("dictionary.global_schedule.update")
def global_schedule_patch(uid: str):
    body, err = _parse_schedule_form_values(request.form)
    if err:
        return redirect(_redirect_global_schedule(err, "error"))
    resp = RMSClient().patch("/api/v1/global_schedule", json={"schedule": [body]})
    if resp.ok:
        return redirect(_redirect_global_schedule("Строка глобального расписания обновлена", "success"))
    return redirect(_redirect_global_schedule(resp.error or "Ошибка обновления строки", "error"))


@bp.post("/global_schedule/<uid>/delete")
@require_permission("dictionary.global_schedule.delete")
def global_schedule_delete(uid: str):
    return redirect(
        _redirect_global_schedule(
            "RMS API не поддерживает удаление global schedule (DELETE). Используйте обновление расписания.",
            "error",
        )
    )


@bp.post("/global_schedule/holiday/create")
@require_permission("dictionary.global_schedule.create")
def global_schedule_holiday_create():
    holiday_body, err = _parse_holiday_form_values(request.form)
    if err:
        return redirect(_redirect_global_schedule(err, "error"))
    resp = RMSClient().post("/api/v1/holiday", json={"holidays": [holiday_body]})
    if resp.ok:
        return redirect(_redirect_global_schedule("Глобальный выходной добавлен", "success"))
    return redirect(_redirect_global_schedule(resp.error or "Ошибка добавления глобального выходного", "error"))


@bp.get("/resource_types")
@require_permission("dictionary.resource_type.read")
def resource_types():
    cursor = (request.args.get("cursor") or "").strip()
    params: dict[str, Any] = {"limit": RMS_LIST_PAGE_LIMIT}
    if cursor:
        params["cursor"] = cursor
    resp = RMSClient().get("/api/v1/resource_type", params=params)
    items, meta = _parse_standard_list(resp.data)
    return render_template(
        "dictionaries/resource_types.html",
        items=items,
        page=meta,
        error=resp.error if not resp.ok else None,
        can_create=has_permission("dictionary.resource_type.create"),
        parent_resource_types=PARENT_RESOURCE_TYPES,
        rt_msg=request.args.get("rt_msg"),
        rt_msg_type=request.args.get("rt_msg_type") or "info",
    )


@bp.post("/resource_types/create")
@require_permission("dictionary.resource_type.create")
def resource_types_create():
    parent = (request.form.get("parent_resource_type") or "").strip()
    children = (request.form.get("children_resource_type") or "").strip()
    id_raw = (request.form.get("system_id") or "").strip()

    if not parent or parent not in PARENT_RESOURCE_TYPES:
        return redirect(_redirect_resource_types("Выберите корректный родительский тип", "error"))
    if not children:
        return redirect(_redirect_resource_types("Укажите название дочернего типа", "error"))

    body: dict[str, Any] = {
        "parent_resource_type": parent,
        "children_resource_type": children,
    }
    if id_raw:
        try:
            body["id"] = int(id_raw)
        except ValueError:
            return redirect(_redirect_resource_types("Поле «ID во внешней системе» должно быть целым числом", "error"))

    resp = RMSClient().post("/api/v1/resource_type", json=body)
    if not resp.ok:
        return redirect(_redirect_resource_types(resp.error or "Ошибка создания", "error"))

    uid = None
    if isinstance(resp.data, dict):
        inner = resp.data.get("data")
        if isinstance(inner, dict):
            uid = inner.get("uid")
    if uid:
        return redirect(_redirect_resource_types(f"Тип ресурса создан (uid: {uid})", "success"))
    return redirect(_redirect_resource_types("Тип ресурса создан", "success"))


def _label_for_uid(options: List[dict[str, str]], uid: str) -> str:
    for o in options:
        if o.get("uid") == uid:
            return str(o.get("label") or "")
    return ""


def _resource_type_option_label(row: dict) -> str:
    child = str(row.get("children_resource_type") or "").strip()
    parent = str(row.get("parent_resource_type") or "").strip()
    if child and parent:
        return f"{child} ({parent})"
    if child:
        return child
    if parent:
        return parent
    return str(row.get("uid") or "—")


def _fetch_tech_card_form_options(
    client: RMSClient,
) -> tuple[List[dict[str, str]], List[dict[str, str]], Dict[str, List[dict[str, str]]], Dict[str, str]]:
    brand_rows = _fetch_paginated_top_level(client, "/api/v1/brands", _TECH_CARD_FILTER_OPTIONS_MAX)
    work_rows = _fetch_paginated_top_level(client, "/api/v1/works", _TECH_CARD_FILTER_OPTIONS_MAX)
    resource_type_rows = _fetch_paginated_top_level(
        client, "/api/v1/resource_type", _TECH_CARD_FILTER_OPTIONS_MAX
    )

    brand_options: List[dict[str, str]] = []
    seen_b: set[str] = set()
    for row in brand_rows:
        uid = str(row.get("uid") or "").strip()
        if not uid or uid in seen_b:
            continue
        seen_b.add(uid)
        brand_options.append({"uid": uid, "label": _brand_option_label(row)})

    work_options: List[dict[str, str]] = []
    seen_w: set[str] = set()
    for row in work_rows:
        uid = str(row.get("uid") or "").strip()
        if not uid or uid in seen_w:
            continue
        seen_w.add(uid)
        work_options.append({"uid": uid, "label": _work_option_label(row)})

    resource_types_by_parent: Dict[str, List[dict[str, str]]] = {}
    resource_type_parent_by_uid: Dict[str, str] = {}
    seen_rt: set[str] = set()
    for row in resource_type_rows:
        uid = str(row.get("uid") or "").strip()
        if not uid or uid in seen_rt:
            continue
        seen_rt.add(uid)
        child = str(row.get("children_resource_type") or "").strip()
        parent = str(row.get("parent_resource_type") or "").strip() or "Без родителя"
        label = child or str(row.get("uid") or "—")
        resource_types_by_parent.setdefault(parent, []).append({"uid": uid, "label": label})
        resource_type_parent_by_uid[uid] = parent

    brand_options.sort(key=lambda x: x["label"].casefold())
    work_options.sort(key=lambda x: x["label"].casefold())
    for parent in list(resource_types_by_parent.keys()):
        resource_types_by_parent[parent].sort(key=lambda x: x["label"].casefold())
    return brand_options, work_options, resource_types_by_parent, resource_type_parent_by_uid


def _redirect_tech_cards(msg: Optional[str] = None, msg_type: str = "info") -> str:
    q: Dict[str, str] = {}
    if msg:
        q["tc_msg"] = msg
        q["tc_msg_type"] = msg_type
    return url_for("dictionaries.tech_cards", **q)


def _redirect_tech_cards_new(msg: Optional[str] = None, msg_type: str = "info") -> str:
    q: Dict[str, str] = {}
    if msg:
        q["tc_msg"] = msg
        q["tc_msg_type"] = msg_type
    return url_for("dictionaries.tech_cards_new", **q)


@bp.get("/tech_cards")
@require_permission("dictionary.tech_card.read")
def tech_cards():
    cursor = (request.args.get("cursor") or "").strip()
    brand_uid = (request.args.get("brand_uid") or "").strip()
    work_uid = (request.args.get("work_uid") or "").strip()
    search_q = (request.args.get("search") or "").strip()

    client = RMSClient()
    if brand_uid or work_uid or search_q:
        items, truncated, local_error = _fetch_tech_cards_all_pages(
            client,
            brand_uid=brand_uid,
            work_uid=work_uid,
            search_text=search_q,
            max_total=_TECH_CARD_SCAN_MAX,
        )
        meta = {"next_cursor": "", "has_more": False}
        error = local_error
        if not error and truncated:
            error = (
                f"Показаны первые {_TECH_CARD_SCAN_MAX} техкарт по фильтру. "
                "Уточните фильтр, чтобы сузить выборку."
            )
    else:
        params: dict[str, Any] = {"limit": RMS_LIST_PAGE_LIMIT}
        if cursor:
            params["cursor"] = cursor
        resp = client.get("/api/v1/tech_specification_list", params=params)
        items = _parse_tech_cards(resp.data)
        meta = _parse_tech_metadata(resp.data)
        error = resp.error if not resp.ok else None

    brand_options, work_options, _, _ = _fetch_tech_card_form_options(client)

    return render_template(
        "dictionaries/tech_cards.html",
        items=items,
        page=meta,
        error=error,
        brand_options=brand_options,
        work_options=work_options,
        selected_brand_uid=brand_uid,
        selected_work_uid=work_uid,
        selected_search=search_q,
        selected_brand_label=_label_for_uid(brand_options, brand_uid),
        selected_work_label=_label_for_uid(work_options, work_uid),
        can_create=has_permission("dictionary.tech_card.read"),
        create_url=url_for("dictionaries.tech_cards_new"),
        tc_msg=request.args.get("tc_msg"),
        tc_msg_type=request.args.get("tc_msg_type") or "info",
    )


@bp.post("/tech_cards/delete")
@require_permission("dictionary.tech_card.read")
def tech_cards_delete():
    work_uid = (request.form.get("work_uid") or "").strip()
    brand_uid = (request.form.get("brand_uid") or "").strip()
    ret_brand_uid = (request.form.get("ret_brand_uid") or "").strip()
    ret_work_uid = (request.form.get("ret_work_uid") or "").strip()
    ret_cursor = (request.form.get("ret_cursor") or "").strip()

    if not work_uid or not brand_uid:
        return redirect(_redirect_tech_cards("Для удаления нужны work_uid и brand_uid", "error"))

    # В RMS техкарта идентифицируется связкой work_uid + brand_uid.
    query = urlencode({"work_uid": work_uid, "brand_uid": brand_uid})
    resp = RMSClient().delete(f"/api/v1/tech_specification?{query}")
    if not resp.ok:
        # Фолбэк на вариант API, где параметры ожидаются в JSON body.
        resp = RMSClient().delete(
            "/api/v1/tech_specification",
            json={"work_uid": work_uid, "brand_uid": brand_uid},
        )

    if not resp.ok:
        return redirect(_redirect_tech_cards(resp.error or "Не удалось удалить техкарту", "error"))

    q: Dict[str, str] = {"tc_msg": "Техкарта удалена", "tc_msg_type": "success"}
    if ret_brand_uid:
        q["brand_uid"] = ret_brand_uid
    if ret_work_uid:
        q["work_uid"] = ret_work_uid
    if ret_cursor:
        q["cursor"] = ret_cursor
    return redirect(url_for("dictionaries.tech_cards", **q))


@bp.get("/tech_cards/new")
@require_permission("dictionary.tech_card.read")
def tech_cards_new():
    client = RMSClient()
    brand_options, work_options, resource_types_by_parent, _ = _fetch_tech_card_form_options(client)
    parent_options = sorted(resource_types_by_parent.keys(), key=lambda x: x.casefold())
    return render_template(
        "dictionaries/tech_cards_new.html",
        brand_options=brand_options,
        work_options=work_options,
        resource_types_by_parent=resource_types_by_parent,
        parent_options=parent_options,
        tc_msg=request.args.get("tc_msg"),
        tc_msg_type=request.args.get("tc_msg_type") or "info",
    )


@bp.post("/tech_cards/create")
@require_permission("dictionary.tech_card.read")
def tech_cards_create():
    work_uid = (request.form.get("work_uid") or "").strip()
    brand_uid = (request.form.get("brand_uid") or "").strip()
    parent_values = [str(x or "").strip() for x in request.form.getlist("parent_resource_type")]
    resource_type_uids = [str(x or "").strip() for x in request.form.getlist("resource_type_uid")]
    quantity_values = [str(x or "").strip() for x in request.form.getlist("quantity")]

    if not work_uid:
        return redirect(_redirect_tech_cards_new("Выберите работу", "error"))

    if not resource_type_uids or not quantity_values:
        return redirect(_redirect_tech_cards_new("Добавьте хотя бы один тип ресурса", "error"))
    if not (
        len(resource_type_uids) == len(quantity_values)
        and len(parent_values) == len(resource_type_uids)
    ):
        return redirect(_redirect_tech_cards_new("Некорректный набор полей типа ресурса", "error"))

    _, _, _, resource_type_parent_by_uid = _fetch_tech_card_form_options(RMSClient())
    resource_info: List[Dict[str, Any]] = []
    for idx, resource_type_uid in enumerate(resource_type_uids):
        if not resource_type_uid:
            return redirect(_redirect_tech_cards_new("Выберите тип ресурса во всех строках", "error"))
        if resource_type_uid not in resource_type_parent_by_uid:
            return redirect(
                _redirect_tech_cards_new("Выбранный тип ресурса не найден в справочнике RMS", "error")
            )
        parent_expected = resource_type_parent_by_uid.get(resource_type_uid, "")
        parent_selected = parent_values[idx]
        if parent_selected and parent_expected and parent_selected != parent_expected:
            return redirect(_redirect_tech_cards_new("Тип ресурса не соответствует выбранному родителю", "error"))

        quantity_raw = quantity_values[idx]
        if not quantity_raw:
            return redirect(_redirect_tech_cards_new("Укажите количество ресурса во всех строках", "error"))
        try:
            quantity = int(quantity_raw)
        except ValueError:
            return redirect(_redirect_tech_cards_new("Количество должно быть целым числом", "error"))
        if quantity <= 0:
            return redirect(_redirect_tech_cards_new("Количество должно быть больше нуля", "error"))
        row: Dict[str, Any] = {
            "resource_type_uid": resource_type_uid,
            "quantity": quantity,
        }
        if brand_uid:
            row["brands"] = [{"uid": brand_uid}]
        resource_info.append(row)

    body: Dict[str, Any] = {
        "work_resources": [
            {
                "work_uid": work_uid,
                "resource_info": resource_info,
            }
        ]
    }

    resp = RMSClient().post("/api/v1/tech_specification", json=body)
    if not resp.ok:
        return redirect(_redirect_tech_cards(resp.error or "Ошибка создания техкарты", "error"))
    return redirect(_redirect_tech_cards("Техкарта создана", "success"))


def _tech_cards_back_url() -> str:
    q: dict[str, str] = {}
    rb = (request.args.get("ret_brand_uid") or "").strip()
    rw = (request.args.get("ret_work_uid") or "").strip()
    rc = (request.args.get("ret_cursor") or "").strip()
    if rb:
        q["brand_uid"] = rb
    if rw:
        q["work_uid"] = rw
    if rc:
        q["cursor"] = rc
    return url_for("dictionaries.tech_cards", **q)


@bp.get("/tech_cards/view")
@require_permission("dictionary.tech_card.read")
def tech_card_detail():
    work_uid = (request.args.get("work_uid") or "").strip()
    brand_uid = (request.args.get("brand_uid") or "").strip()
    back_url = _tech_cards_back_url()

    if not work_uid or not brand_uid:
        return render_template(
            "dictionaries/tech_card_detail.html",
            error="Не заданы параметры work_uid и brand_uid.",
            detail=None,
            back_url=back_url,
        )

    resp = RMSClient().get(
        "/api/v1/tech_specification",
        params={"work_uid": work_uid, "brand_uid": brand_uid},
    )
    if not resp.ok:
        return render_template(
            "dictionaries/tech_card_detail.html",
            error=resp.error or f"Ошибка RMS (HTTP {resp.status_code})",
            detail=None,
            back_url=back_url,
        )

    payload = resp.data if isinstance(resp.data, dict) else {}
    inner = payload.get("data")
    detail: Optional[dict[str, Any]] = None
    if isinstance(inner, dict):
        res = inner.get("resources")
        resources = res if isinstance(res, list) else []
        detail = {
            "work_name": inner.get("work_name"),
            "brand_name": inner.get("brand_name"),
            "resources": [x for x in resources if isinstance(x, dict)],
        }

    if detail is None:
        return render_template(
            "dictionaries/tech_card_detail.html",
            error="Неожиданный формат ответа RMS.",
            detail=None,
            back_url=back_url,
        )

    return render_template(
        "dictionaries/tech_card_detail.html",
        error=None,
        detail=detail,
        back_url=back_url,
    )


@bp.get("/brands")
@require_permission("dictionary.brand.read")
def brands():
    cursor = (request.args.get("cursor") or "").strip()
    params: dict[str, Any] = {"limit": RMS_LIST_PAGE_LIMIT}
    if cursor:
        params["cursor"] = cursor
    resp = RMSClient().get("/api/v1/brands", params=params)
    items, meta = _parse_standard_list(resp.data)
    return render_template(
        "dictionaries/brands.html",
        items=items,
        page=meta,
        error=resp.error if not resp.ok else None,
    )


@bp.get("/working_zones")
@require_permission("dictionary.working_zone.read")
def working_zones():
    sc_uid = (request.args.get("sc_uid") or "").strip()
    cursor = (request.args.get("cursor") or "").strip()
    items: List[Any] = []
    meta: dict[str, Any] = {"next_cursor": "", "has_more": False}
    error = None
    client = RMSClient()
    sc_options = fetch_service_center_options(client)
    selected_sc_label = (label_for_sc_uid(sc_options, sc_uid) or sc_uid) if sc_uid else ""
    if sc_uid:
        params: dict[str, Any] = {"limit": RMS_LIST_PAGE_LIMIT}
        if cursor:
            params["cursor"] = cursor
        resp = client.get(f"/api/v1/service_center/{sc_uid}/working_zone", params=params)
        error = resp.error if not resp.ok else None
        if resp.ok:
            items, meta = _parse_standard_list(resp.data)
    return render_template(
        "dictionaries/working_zones.html",
        sc_uid=sc_uid,
        sc_options=sc_options,
        selected_sc_label=selected_sc_label,
        items=items,
        page=meta,
        error=error,
    )


def _parse_standard_list(payload: Any) -> tuple[list, dict]:
    if not isinstance(payload, dict):
        return [], {"next_cursor": "", "has_more": False}
    data = payload.get("data")
    items: list = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for key in ("resource_types", "brands", "working_zones", "items", "service_centers"):
            if isinstance(data.get(key), list):
                items = data[key]
                break
    metadata = payload.get("metadata", {})
    if isinstance(metadata, dict):
        return items, {
            "next_cursor": metadata.get("cursor") or "",
            "has_more": bool(metadata.get("has_more")),
        }
    return items, {"next_cursor": "", "has_more": False}


def _parse_tech_cards(payload: Any) -> list:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return data["items"]
    return []


def _parse_tech_metadata(payload: Any) -> dict:
    if not isinstance(payload, dict):
        return {"next_cursor": "", "has_more": False}
    if "metadata" in payload and isinstance(payload["metadata"], dict):
        m = payload["metadata"]
        return {"next_cursor": m.get("cursor") or "", "has_more": bool(m.get("has_more"))}
    return {
        "next_cursor": str(payload.get("cursor") or ""),
        "has_more": bool(payload.get("has_more")),
    }
