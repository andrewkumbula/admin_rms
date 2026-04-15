from flask import Blueprint, redirect, render_template, request, url_for

from app.list_limits import RMS_LIST_PAGE_LIMIT
from app.rbac.decorators import require_permission
from app.rms_client.client import RMSClient
from app.service_center_catalog import fetch_service_center_options, label_for_sc_uid


bp = Blueprint("appointments", __name__, url_prefix="/appointments")


@bp.get("")
@require_permission("appointment.read")
def list_page():
    sc_uid = (request.args.get("sc_uid") or "").strip()
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    status = (request.args.get("status") or "").strip()
    cursor = (request.args.get("cursor") or "").strip()

    params = {"limit": RMS_LIST_PAGE_LIMIT}
    if sc_uid:
        params["service_center_uid"] = sc_uid
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to
    if status:
        params["status"] = status
    if cursor:
        params["cursor"] = cursor

    client = RMSClient()
    sc_options = fetch_service_center_options(client)
    selected_sc_label = (label_for_sc_uid(sc_options, sc_uid) or sc_uid) if sc_uid else ""
    response = client.get("/api/v1/appointment/service_center/appointments", params=params)
    if not response.ok:
        return render_template(
            "appointments/list.html",
            items=[],
            filters={"sc_uid": sc_uid, "date_from": date_from, "date_to": date_to, "status": status},
            page={"next_cursor": "", "has_more": False},
            status_options=["", "active", "cancelled", "completed"],
            sc_options=sc_options,
            selected_sc_label=selected_sc_label,
            error=response.error,
        )

    payload = response.data if isinstance(response.data, dict) else {}
    items = _extract_appointments(payload)
    metadata = payload.get("metadata", {}) if isinstance(payload, dict) else {}
    return render_template(
        "appointments/list.html",
        items=items,
        filters={"sc_uid": sc_uid, "date_from": date_from, "date_to": date_to, "status": status},
        page={
            "next_cursor": metadata.get("cursor") if isinstance(metadata, dict) else "",
            "has_more": bool(metadata.get("has_more")) if isinstance(metadata, dict) else False,
        },
        status_options=["", "active", "cancelled", "completed"],
        sc_options=sc_options,
        selected_sc_label=selected_sc_label,
        error=None,
    )


@bp.get("/<uid>")
@require_permission("appointment.read")
def detail_page(uid: str):
    client = RMSClient()
    response = client.get(f"/api/v1/appointment/{uid}/info")
    if not response.ok:
        return render_template(
            "appointments/detail.html",
            uid=uid,
            appointment={},
            error=response.error,
            action_message=None,
            action_type=None,
        )

    appointment = _extract_appointment(response.data)
    action_message = request.args.get("message")
    action_type = request.args.get("type")
    return render_template(
        "appointments/detail.html",
        uid=uid,
        appointment=appointment,
        error=None,
        action_message=action_message,
        action_type=action_type,
    )


@bp.post("/<uid>/cancel")
@require_permission("appointment.cancel")
def cancel(uid: str):
    client = RMSClient()
    current = client.get(f"/api/v1/appointment/{uid}/info")
    if current.ok:
        appointment = _extract_appointment(current.data)
        current_status = str(appointment.get("status", "")).lower()
        if current_status == "cancelled":
            return redirect(
                url_for(
                    "appointments.detail_page",
                    uid=uid,
                    type="warning",
                    message="Запись уже отменена",
                )
            )

    response = client.delete(f"/api/v1/appointment/{uid}")
    if response.ok:
        return redirect(
            url_for(
                "appointments.detail_page",
                uid=uid,
                type="success",
                message="Запись успешно отменена",
            )
        )
    return redirect(
        url_for(
            "appointments.detail_page",
            uid=uid,
            type="error",
            message=f"Ошибка отмены: {response.error or 'unknown'}",
        )
    )


def _extract_appointments(payload: dict) -> list:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data", {})
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("appointments", "items", "data"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def _extract_appointment(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data", {})
    if isinstance(data, dict):
        if isinstance(data.get("appointment"), dict):
            return data["appointment"]
        return data
    return {}
