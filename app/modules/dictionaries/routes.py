from typing import Any, Dict, List, Optional

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

    params: dict[str, Any] = {"limit": RMS_LIST_PAGE_LIMIT}
    if cursor:
        params["cursor"] = cursor
    if brand_uid:
        params["brand_uid"] = brand_uid
    if work_uid:
        params["work_uid"] = work_uid

    client = RMSClient()
    resp = client.get("/api/v1/tech_specification_list", params=params)
    items = _parse_tech_cards(resp.data)
    meta = _parse_tech_metadata(resp.data)

    brand_options, work_options, _, _ = _fetch_tech_card_form_options(client)

    return render_template(
        "dictionaries/tech_cards.html",
        items=items,
        page=meta,
        error=resp.error if not resp.ok else None,
        brand_options=brand_options,
        work_options=work_options,
        selected_brand_uid=brand_uid,
        selected_work_uid=work_uid,
        selected_brand_label=_label_for_uid(brand_options, brand_uid),
        selected_work_label=_label_for_uid(work_options, work_uid),
        can_create=has_permission("dictionary.tech_card.read"),
        create_url=url_for("dictionaries.tech_cards_new"),
        tc_msg=request.args.get("tc_msg"),
        tc_msg_type=request.args.get("tc_msg_type") or "info",
    )


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
    if not brand_uid:
        return redirect(_redirect_tech_cards_new("Выберите бренд", "error"))

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
        resource_info.append(
            {
                "resource_type_uid": resource_type_uid,
                "quantity": quantity,
                "brands": [{"uid": brand_uid}],
            }
        )

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
