"""Загрузка списка сервисных центров для полей выбора в админке (GET /api/v2/service_center)."""

from typing import Any, Dict, List, Optional, Union

from app.list_limits import RMS_LIST_PAGE_LIMIT
from app.rms_client.client import RMSClient

# Сколько СЦ подтянуть для выпадающих списков (пагинация курсором)
SERVICE_CENTER_OPTIONS_MAX = 4000

ScOption = Dict[str, Any]


def _address_hint_from_v2_row(row: dict) -> str:
    addr = row.get("address")
    if not isinstance(addr, dict):
        return ""
    parts: List[str] = []
    uv = addr.get("unrestricted_value")
    if isinstance(uv, str) and uv.strip():
        return uv.strip()[:200]
    geo = addr.get("geo_object")
    if isinstance(geo, dict):
        lat, lon = geo.get("geo_lat"), geo.get("geo_lon")
        if lat is not None and lon is not None:
            parts.append(f"{lat}, {lon}")
    aid = addr.get("address_id")
    if aid is not None:
        parts.append(f"address_id: {aid}")
    return ", ".join(parts) if parts else ""


def fetch_service_center_options(
    client: RMSClient,
    max_total: int = SERVICE_CENTER_OPTIONS_MAX,
    *,
    without_franchisee: bool = False,
) -> List[ScOption]:
    acc: List[ScOption] = []
    seen: set[str] = set()
    cursor: Optional[str] = None
    while len(acc) < max_total:
        chunk_limit = min(RMS_LIST_PAGE_LIMIT, max_total - len(acc))
        params: Dict[str, Any] = {"limit": chunk_limit}
        if without_franchisee:
            params["without_franchisee"] = "1"
        if cursor:
            params["cursor"] = cursor
        resp = client.get("/api/v2/service_center", params=params)
        if not resp.ok:
            break
        payload = resp.data if isinstance(resp.data, dict) else {}
        data = payload.get("data")
        chunk: List[dict] = []
        if isinstance(data, dict):
            scs = data.get("service_centers")
            if isinstance(scs, list):
                chunk = [x for x in scs if isinstance(x, dict)]
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        for row in chunk:
            uid = str(row.get("uid") or "").strip()
            if not uid or uid in seen:
                continue
            seen.add(uid)
            name = str(row.get("name") or "").strip()
            label = name if name else uid
            hint = _address_hint_from_v2_row(row)
            item: ScOption = {"uid": uid, "label": label}
            if "franchisee_uid" in row:
                item["franchisee_uid"] = str(row.get("franchisee_uid") or "").strip()
            if hint:
                item["address_hint"] = hint
            acc.append(item)
        if not bool(metadata.get("has_more")):
            break
        nxt = str(metadata.get("cursor") or "").strip()
        if not nxt:
            break
        cursor = nxt
    acc.sort(key=lambda x: str(x.get("label") or "").casefold())
    return acc


def pick_unassigned_sc_options(all_sc: List[ScOption]) -> List[ScOption]:
    """Свободные СЦ: franchisee_uid пустой в ответе v2."""
    return [o for o in all_sc if "franchisee_uid" in o and not str(o.get("franchisee_uid") or "").strip()]


def resolve_unassigned_sc_options(
    client: RMSClient, *, cache_all: Optional[List[ScOption]] = None
) -> List[ScOption]:
    """Список СЦ без франчайзи: по полю franchisee_uid в v2 или запрос without_franchisee (старый RMS)."""
    all_sc = cache_all if cache_all is not None else fetch_service_center_options(client)
    if all_sc and any("franchisee_uid" in o for o in all_sc):
        return pick_unassigned_sc_options(all_sc)
    # Фолбэк для старого RMS: если without_franchisee не поддержан/игнорируется,
    # дополнительно проверяем каждый СЦ по карточке v1.
    fallback = fetch_service_center_options(client, without_franchisee=True)
    return [o for o in fallback if _is_sc_unassigned_v1(client, str(o.get("uid") or ""))]


def label_for_sc_uid(options: List[Union[Dict[str, str], ScOption]], uid: str) -> str:
    for o in options:
        if o.get("uid") == uid:
            return str(o.get("label") or "")
    return ""


def _extract_v1_service_center(payload: Any) -> dict:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if isinstance(data, dict):
        nested = data.get("service_center")
        if isinstance(nested, dict):
            return nested
        return data
    return {}


def format_sc_address_line(sc: dict) -> str:
    addr = sc.get("address")
    if isinstance(addr, dict):
        uv = addr.get("unrestricted_value")
        if isinstance(uv, str) and uv.strip():
            return uv.strip()
        geo = addr.get("geo_object")
        if isinstance(geo, dict):
            lat, lon = geo.get("geo_lat"), geo.get("geo_lon")
            if lat is not None and lon is not None:
                return f"{lat}, {lon}"
        aid = addr.get("address_id")
        if aid is not None:
            return f"address_id: {aid}"
    return "—"


def fetch_service_center_table_row(client: RMSClient, uid: str) -> Dict[str, Any]:
    """Карточка строки таблицы по GET /api/v1/service_center/{uid}."""
    row: Dict[str, Any] = {
        "uid": uid,
        "name": uid,
        "address": "—",
        "is_deleted_label": "—",
        "departments_label": "—",
        "qc_geo": "—",
    }
    resp = client.get(f"/api/v1/service_center/{uid}")
    if not resp.ok:
        row["load_error"] = resp.error or "нет данных"
        return row
    sc = _extract_v1_service_center(resp.data)
    if not sc:
        row["load_error"] = "пустой ответ"
        return row
    name = str(sc.get("name") or "").strip()
    if name:
        row["name"] = name
    row["address"] = format_sc_address_line(sc)
    if isinstance(sc.get("is_deleted"), bool):
        row["is_deleted_label"] = "да" if sc["is_deleted"] else "нет"
    depts = sc.get("departments")
    if isinstance(depts, list):
        row["departments_label"] = str(len(depts))
    addr = sc.get("address") if isinstance(sc.get("address"), dict) else {}
    geo = addr.get("geo_object") if isinstance(addr.get("geo_object"), dict) else {}
    if geo.get("qc_geo") is not None:
        row["qc_geo"] = str(geo.get("qc_geo"))
    return row


def _is_sc_unassigned_v1(client: RMSClient, uid: str) -> bool:
    """Проверяет по GET /api/v1/service_center/{uid}, что СЦ не привязан к франчайзи."""
    uid = (uid or "").strip()
    if not uid:
        return False
    resp = client.get(f"/api/v1/service_center/{uid}")
    if not resp.ok:
        return False
    sc = _extract_v1_service_center(resp.data)
    fr_uid = str(
        sc.get("franchisee_uid")
        or sc.get("franchise_uid")
        or sc.get("franchiseeUid")
        or ""
    ).strip()
    return not fr_uid
