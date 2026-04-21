import json
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, render_template, request

from app.rbac.decorators import require_permission

bp = Blueprint("analytics", __name__, url_prefix="/analytics")

JSON_DIR = Path(__file__).resolve().parents[3] / "JSON"
SLOT_MINUTES_DEFAULT = 60
MAX_DAYS = 31
DAY_START = time(9, 0)
DAY_END = time(20, 0)


def _safe_parse_dt(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    s = s.replace(" ", "T")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _day_bounds(d: date) -> Tuple[datetime, datetime]:
    return datetime.combine(d, DAY_START), datetime.combine(d, DAY_END)


def _minutes_overlap(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> int:
    start = max(a_start, b_start)
    end = min(a_end, b_end)
    if end <= start:
        return 0
    return int((end - start).total_seconds() // 60)


def _count_consecutive_booking_starts(
    free_by_slot: List[int],
    day_by_slot: List[str],
    slot_minutes: int,
    need_minutes: int,
) -> int:
    """
    Оценка «сколько записей поместится» для длительности need_minutes:
    - запись занимает ceil(need_minutes / slot_minutes) подряд слотов;
    - в каждом слоте окна должен быть полный запас (free >= slot_minutes);
    - внутри дня считаем жадно без пересечений окон (non-overlap).
    """
    if slot_minutes <= 0 or need_minutes <= 0:
        return 0
    n = (need_minutes + slot_minutes - 1) // slot_minutes
    if n <= 0 or len(free_by_slot) < n or len(free_by_slot) != len(day_by_slot):
        return 0

    cnt = 0
    i = 0
    while i <= len(free_by_slot) - n:
        same_day = all(day_by_slot[i + j] == day_by_slot[i] for j in range(n))
        fits = same_day and all(free_by_slot[i + j] >= slot_minutes for j in range(n))
        if fits:
            cnt += 1
            i += n
        else:
            i += 1
    return cnt


def _safe_parse_time(raw: Any) -> Optional[time]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return time.fromisoformat(s)
    except ValueError:
        return None


def _parse_calendar_date(raw: Any) -> Optional[date]:
    """Дата календаря из JSON (YYYY-MM-DD или ISO datetime). Пусто / невалидно → None."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if "T" in s:
        s = s.split("T", 1)[0]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


@lru_cache(maxsize=64)
def _read_json_cached(name: str, mtime_ns: int) -> List[Dict[str, Any]]:
    path = JSON_DIR / name
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, list) else []


def _read_json(name: str) -> List[Dict[str, Any]]:
    path = JSON_DIR / name
    if not path.exists():
        return []
    return _read_json_cached(name, path.stat().st_mtime_ns)


def _extract_sc_day_off_dates(sc_uid: str) -> set[date]:
    """
    Календарные «закрытия» СЦ для расчёта мощности. Только строки, явно привязанные к этому СЦ.
    Записи off_days.json без service_center_uid — персональные отсутствия сотрудников; их сюда не подмешиваем,
    иначе любой отгул по сети обнулял бы мощность у всех центров.
    """
    out: set[date] = set()
    for fname in ("sc_off_days.json", "off_days.json", "service_center_holidays.json", "hollidays.json"):
        for row in _read_json(fname):
            if not isinstance(row, dict):
                continue
            row_sc = str(row.get("service_center_uid") or row.get("sc_uid") or "").strip()
            if row_sc != sc_uid:
                continue
            for key in ("start_datetime", "start_time", "date", "day", "start_date"):
                dt = _safe_parse_dt(row.get(key))
                if dt:
                    out.add(dt.date())
                    break
    return out


def _is_non_cancelled(appt: Dict[str, Any]) -> bool:
    status = str(appt.get("status") or "").strip().casefold()
    if status and status in {"cancelled", "canceled", "отменена", "отменен"}:
        return False
    return not bool(appt.get("is_deleted"))


def _calc_fte_and_decision(deficit_minutes_total: int, period_days: int) -> Tuple[float, int, str]:
    one_fte_minutes_period = period_days * int((datetime.combine(date.min, DAY_END) - datetime.combine(date.min, DAY_START)).total_seconds() // 60)
    if one_fte_minutes_period <= 0:
        return 0.0, 0, "insufficient_data"
    fte = deficit_minutes_total / one_fte_minutes_period
    if fte >= 0.7:
        return round(fte, 2), 1, "add_staff"
    return round(fte, 2), 0, "stable"


def _classify_bucket(parent_label: str, child_label: str) -> str:
    raw = f"{parent_label} {child_label}".strip().casefold()
    if "мойк" in raw:
        return "washer"
    if "механ" in raw or "ремонт" in raw or "подъ" in raw or "пост" in raw:
        return "mechanic"
    return "other"


def _build_bucket_stats(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    stats: Dict[str, Dict[str, Any]] = {
        "mechanic": {"count": 0, "busy_minutes": 0, "capacity_minutes": 0, "avg_load_pct": 0.0},
        "washer": {"count": 0, "busy_minutes": 0, "capacity_minutes": 0, "avg_load_pct": 0.0},
        "other": {"count": 0, "busy_minutes": 0, "capacity_minutes": 0, "avg_load_pct": 0.0},
    }
    for row in rows:
        bucket = str(row.get("bucket") or "other")
        if bucket not in stats:
            bucket = "other"
        stats[bucket]["count"] += 1
        stats[bucket]["busy_minutes"] += int(row.get("busy_minutes") or 0)
        stats[bucket]["capacity_minutes"] += int(row.get("capacity_minutes") or 0)
    for bucket in stats:
        cap = int(stats[bucket]["capacity_minutes"] or 0)
        busy = int(stats[bucket]["busy_minutes"] or 0)
        stats[bucket]["avg_load_pct"] = round((busy / cap * 100), 1) if cap > 0 else 0.0
    return stats


def _employee_schedule_rows(date_from: date, date_to: date) -> Dict[str, List[Dict[str, Any]]]:
    schedules = [
        row
        for row in _read_json("cyclical_schedules.json")
        if isinstance(row, dict) and not bool(row.get("is_deleted"))
    ]
    breaks = [
        row
        for row in _read_json("cyclical_breaks.json")
        if isinstance(row, dict) and not bool(row.get("is_deleted"))
    ]
    breaks_by_schedule: Dict[str, int] = defaultdict(int)
    break_ranges_by_schedule: Dict[str, List[Tuple[time, time]]] = defaultdict(list)
    for row in breaks:
        sched_uid = str(row.get("cyclical_schedule_uid") or "").strip()
        st = _safe_parse_time(row.get("start_time"))
        en = _safe_parse_time(row.get("end_time"))
        if not sched_uid or not st or not en:
            continue
        minutes = int((datetime.combine(date.min, en) - datetime.combine(date.min, st)).total_seconds() // 60)
        if minutes > 0:
            breaks_by_schedule[sched_uid] += minutes
            break_ranges_by_schedule[sched_uid].append((st, en))

    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in schedules:
        emp_uid = str(row.get("employee_uid") or "").strip()
        sched_uid = str(row.get("uid") or "").strip()
        if not emp_uid or not sched_uid:
            continue
        s_date = _parse_calendar_date(row.get("start_date")) or date.min
        e_date = _parse_calendar_date(row.get("end_date"))
        # Бессрочное расписание (нет end_date) — действует с start_date вперёд, пока не перекроет другой start_date.
        # С концом: последний день включительно; со следующего календарного дня — следующая запись.
        schedule_last_day = e_date if e_date is not None else date.max
        if schedule_last_day < date_from or s_date > date_to:
            continue
        st = _safe_parse_time(row.get("start_time")) or DAY_START
        en = _safe_parse_time(row.get("end_time")) or DAY_END
        shift_minutes = int((datetime.combine(date.min, en) - datetime.combine(date.min, st)).total_seconds() // 60)
        if shift_minutes <= 0:
            continue
        work_days = int(row.get("work_days") or 0)
        day_offs = int(row.get("day_offs") or 0)
        out[emp_uid].append(
            {
                "start_date": s_date,
                "end_date": e_date,
                "work_days": work_days,
                "day_offs": day_offs,
                "start_time": st,
                "end_time": en,
                "shift_minutes": shift_minutes,
                "lunch_minutes": int(breaks_by_schedule.get(sched_uid, 0)),
                "break_ranges": list(break_ranges_by_schedule.get(sched_uid, [])),
            }
        )
    for emp_uid in out:
        out[emp_uid].sort(key=lambda x: x["start_date"])
    return out


def _schedule_row_covers_day(row: Dict[str, Any], d: date) -> bool:
    if d < row["start_date"]:
        return False
    end = row.get("end_date")
    if end is None:
        return True
    return d <= end


def _employee_active_schedule_for_day(rows: List[Dict[str, Any]], d: date) -> Optional[Dict[str, Any]]:
    candidates = [r for r in rows if _schedule_row_covers_day(r, d)]
    if not candidates:
        return None
    return max(candidates, key=lambda r: r["start_date"])


def _employee_capacity_minutes_by_uid(date_from: date, date_to: date) -> Dict[str, int]:
    schedules_by_emp = _employee_schedule_rows(date_from, date_to)
    if not schedules_by_emp:
        return {}
    out: Dict[str, int] = {}
    days_total = (date_to - date_from).days + 1
    for emp_uid, rows in schedules_by_emp.items():
        total = 0
        for day_idx in range(days_total):
            d = date_from + timedelta(days=day_idx)
            active = _employee_active_schedule_for_day(rows, d)
            if not active:
                continue
            work_days = int(active.get("work_days") or 0)
            day_offs = int(active.get("day_offs") or 0)
            cycle = work_days + day_offs
            if cycle <= 0:
                continue
            cycle_day_idx = (d - active["start_date"]).days % cycle
            if cycle_day_idx >= work_days:
                continue
            shift_minutes = int(active.get("shift_minutes") or 0)
            lunch_minutes = int(active.get("lunch_minutes") or 0)
            total += max(0, shift_minutes - lunch_minutes)
        out[emp_uid] = total
    return out


def _employee_is_work_day(schedule_row: Dict[str, Any], d: date) -> bool:
    work_days = int(schedule_row.get("work_days") or 0)
    day_offs = int(schedule_row.get("day_offs") or 0)
    cycle = work_days + day_offs
    if cycle <= 0:
        return False
    cycle_day_idx = (d - schedule_row["start_date"]).days % cycle
    return cycle_day_idx < work_days


def _compute_load_timeseries(
    date_from: date,
    date_to: date,
    slot_minutes: int,
    sc_set: set,
    resource_kind: str,
    category: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Слотная модель спроса/мощности для выбранного периода и фильтров.
    Возвращает timeseries по слотам и агрегаты (суммы по слотам — корректны для утилизации).
    """
    appointments = {
        str(a.get("uid") or ""): a
        for a in _read_json("appointments.json")
        if isinstance(a, dict)
        and str(a.get("uid") or "")
        and str(a.get("service_center_uid") or "") in sc_set
        and _is_non_cancelled(a)
    }
    appointment_employee = [
        row
        for row in _read_json("appointment_employee.json")
        if isinstance(row, dict) and str(row.get("appointment_uid") or "") in appointments
    ]
    appointment_wz = [
        row
        for row in _read_json("appointment_working_zone.json")
        if isinstance(row, dict) and str(row.get("appointment_uid") or "") in appointments
    ]
    employees = [
        row
        for row in _read_json("employees.json")
        if isinstance(row, dict)
        and str(row.get("service_center_uid") or "") in sc_set
        and bool(row.get("is_available")) is True
        and bool(row.get("is_fired")) is False
    ]
    working_zones = [
        row
        for row in _read_json("working_zones.json")
        if isinstance(row, dict)
        and str(row.get("service_center_uid") or "") in sc_set
        and bool(row.get("is_available")) is True
    ]
    rt_parent: Dict[str, str] = {}
    rt_child: Dict[str, str] = {}
    for r in _read_json("resource_types.json"):
        if not isinstance(r, dict):
            continue
        uid = str(r.get("uid") or "").strip()
        if not uid:
            continue
        rt_parent[uid] = str(r.get("parent_resource_type") or "").strip()
        rt_child[uid] = str(r.get("children_resource_type") or "").strip()
    employee_rt_map: Dict[str, str] = {}
    for row in _read_json("employee_resource_types.json"):
        if not isinstance(row, dict):
            continue
        emp_uid = str(row.get("employee_uid") or "").strip()
        rt_uid = str(row.get("resource_type_uid") or "").strip()
        if emp_uid and rt_uid and emp_uid not in employee_rt_map:
            employee_rt_map[emp_uid] = rt_uid
    employee_schedules_by_uid = _employee_schedule_rows(date_from, date_to)
    employee_bucket_by_uid: Dict[str, str] = {}
    for emp in employees:
        emp_uid = str(emp.get("uid") or "").strip()
        if not emp_uid:
            continue
        rt_uid = employee_rt_map.get(emp_uid, "")
        employee_bucket_by_uid[emp_uid] = _classify_bucket(rt_parent.get(rt_uid, ""), rt_child.get(rt_uid, ""))
    wz_bucket_by_uid: Dict[str, str] = {}
    for wz in working_zones:
        wz_uid = str(wz.get("uid") or "").strip()
        if not wz_uid:
            continue
        rt_uid = str(wz.get("resource_type_uid") or "").strip()
        wz_bucket_by_uid[wz_uid] = _classify_bucket(rt_parent.get(rt_uid, ""), rt_child.get(rt_uid, ""))

    step = timedelta(minutes=slot_minutes)
    slots: List[Tuple[datetime, datetime]] = []
    cur = datetime.combine(date_from, DAY_START)
    end_dt = datetime.combine(date_to, DAY_END)
    while cur < end_dt:
        nxt = cur + step
        slots.append((cur, nxt))
        cur = nxt

    slot_idx_by_start = {s_start: idx for idx, (s_start, _) in enumerate(slots)}

    selected_employee_uids: set[str] = set()
    if resource_kind in {"all", "employee"}:
        for emp in employees:
            emp_uid = str(emp.get("uid") or "").strip()
            if not emp_uid:
                continue
            if category != "all" and employee_bucket_by_uid.get(emp_uid, "other") != category:
                continue
            selected_employee_uids.add(emp_uid)

    selected_wz_uids: set[str] = set()
    if resource_kind in {"all", "working_zone"}:
        for wz in working_zones:
            wz_uid = str(wz.get("uid") or "").strip()
            if not wz_uid:
                continue
            if category != "all" and wz_bucket_by_uid.get(wz_uid, "other") != category:
                continue
            selected_wz_uids.add(wz_uid)

    employee_busy_by_slot: Dict[str, List[int]] = {uid: [0] * len(slots) for uid in selected_employee_uids}
    employee_capacity_by_slot: Dict[str, List[int]] = {uid: [0] * len(slots) for uid in selected_employee_uids}
    wz_busy_by_slot: Dict[str, List[int]] = {uid: [0] * len(slots) for uid in selected_wz_uids}
    wz_capacity_by_slot: Dict[str, List[int]] = {uid: [0] * len(slots) for uid in selected_wz_uids}

    busy_by_slot = defaultdict(int)
    if resource_kind in {"all", "employee"}:
        for rel in appointment_employee:
            emp_uid = str(rel.get("employee_uid") or "").strip()
            if emp_uid not in selected_employee_uids:
                continue
            st = _safe_parse_dt(rel.get("start_time"))
            en = _safe_parse_dt(rel.get("end_time"))
            if not st or not en or en <= st:
                continue
            if en.date() < date_from or st.date() > date_to:
                continue
            for s_start, s_end in slots:
                ov = _minutes_overlap(st, en, s_start, s_end)
                if ov:
                    idx = slot_idx_by_start[s_start]
                    busy_by_slot[s_start] += ov
                    employee_busy_by_slot[emp_uid][idx] += ov

    if resource_kind in {"all", "working_zone"}:
        for rel in appointment_wz:
            wz_uid = str(rel.get("working_zone_uid") or "").strip()
            if wz_uid not in selected_wz_uids:
                continue
            st = _safe_parse_dt(rel.get("start_time"))
            en = _safe_parse_dt(rel.get("end_time"))
            if not st or not en or en <= st:
                continue
            if en.date() < date_from or st.date() > date_to:
                continue
            for s_start, s_end in slots:
                ov = _minutes_overlap(st, en, s_start, s_end)
                if ov:
                    idx = slot_idx_by_start[s_start]
                    busy_by_slot[s_start] += ov
                    wz_busy_by_slot[wz_uid][idx] += ov

    capacity_by_slot = defaultdict(int)
    employee_capacity_minutes_map: Dict[str, int] = defaultdict(int)
    sc_day_offs = {uid: _extract_sc_day_off_dates(uid) for uid in sc_set}
    for s_start, s_end in slots:
        d = s_start.date()
        if resource_kind in {"all", "employee"}:
            for emp in employees:
                emp_uid = str(emp.get("uid") or "").strip()
                if category != "all" and employee_bucket_by_uid.get(emp_uid, "other") != category:
                    continue
                row_sc = str(emp.get("service_center_uid") or "")
                if d in sc_day_offs.get(row_sc, set()):
                    continue
                sched_rows = employee_schedules_by_uid.get(emp_uid, [])
                active_sched = _employee_active_schedule_for_day(sched_rows, d) if sched_rows else None
                if active_sched and _employee_is_work_day(active_sched, d):
                    shift_start = datetime.combine(d, active_sched.get("start_time") or DAY_START)
                    shift_end = datetime.combine(d, active_sched.get("end_time") or DAY_END)
                    slot_capacity = _minutes_overlap(shift_start, shift_end, s_start, s_end)
                    for br_start, br_end in list(active_sched.get("break_ranges") or []):
                        slot_capacity -= _minutes_overlap(
                            datetime.combine(d, br_start),
                            datetime.combine(d, br_end),
                            s_start,
                            s_end,
                        )
                    slot_capacity = max(0, slot_capacity)
                elif active_sched:
                    # Выходной по циклу 2/2, 3/3 и т.д. — мощности нет (не подменяем окном 9–20).
                    slot_capacity = 0
                elif sched_rows:
                    # Есть графики, но на эту дату ни один интервал [start,end] не покрывает — нет плана смены.
                    slot_capacity = 0
                else:
                    # Нет ни одной записи cyclical_schedules — упрощённо считаем доступность по окну дня СЦ.
                    slot_capacity = _minutes_overlap(*_day_bounds(d), s_start, s_end)
                idx = slot_idx_by_start[s_start]
                capacity_by_slot[s_start] += slot_capacity
                employee_capacity_minutes_map[emp_uid] += slot_capacity
                employee_capacity_by_slot[emp_uid][idx] += slot_capacity
        if resource_kind in {"all", "working_zone"}:
            for wz in working_zones:
                wz_uid = str(wz.get("uid") or "").strip()
                if wz_uid not in selected_wz_uids:
                    continue
                row_sc = str(wz.get("service_center_uid") or "")
                if d in sc_day_offs.get(row_sc, set()):
                    continue
                wz_slot_capacity = _minutes_overlap(*_day_bounds(d), s_start, s_end)
                idx = slot_idx_by_start[s_start]
                capacity_by_slot[s_start] += wz_slot_capacity
                wz_capacity_by_slot[wz_uid][idx] += wz_slot_capacity

    timeseries: List[Dict[str, Any]] = []
    deficit_minutes_total = 0
    overloaded_slots = 0
    appointment_uids_period: set[str] = set()
    for s_start, _ in slots:
        busy = int(busy_by_slot.get(s_start, 0))
        cap = int(capacity_by_slot.get(s_start, 0))
        deficit = max(0, busy - cap)
        free_minutes = max(0, cap - busy)
        if deficit > 0:
            overloaded_slots += 1
        deficit_minutes_total += deficit
        timeseries.append(
            {
                "ts": s_start.isoformat(),
                "weekday": s_start.isoweekday(),
                "hour": s_start.hour,
                "busy_minutes": busy,
                "capacity_minutes": cap,
                "free_minutes": free_minutes,
                "deficit_minutes": deficit,
                "load_pct": round((busy / cap * 100), 1) if cap > 0 else 0.0,
            }
        )

    day_vals = [str(t.get("ts") or "")[:10] for t in timeseries]
    free_slots_over_2h = 0
    free_slots_over_4h = 0

    for uid, cap_seq in employee_capacity_by_slot.items():
        busy_seq = employee_busy_by_slot.get(uid, [])
        free_seq = [max(0, cap - (busy_seq[idx] if idx < len(busy_seq) else 0)) for idx, cap in enumerate(cap_seq)]
        free_slots_over_2h += _count_consecutive_booking_starts(free_seq, day_vals, slot_minutes, 120)
        free_slots_over_4h += _count_consecutive_booking_starts(free_seq, day_vals, slot_minutes, 240)

    for uid, cap_seq in wz_capacity_by_slot.items():
        busy_seq = wz_busy_by_slot.get(uid, [])
        free_seq = [max(0, cap - (busy_seq[idx] if idx < len(busy_seq) else 0)) for idx, cap in enumerate(cap_seq)]
        free_slots_over_2h += _count_consecutive_booking_starts(free_seq, day_vals, slot_minutes, 120)
        free_slots_over_4h += _count_consecutive_booking_starts(free_seq, day_vals, slot_minutes, 240)

    if resource_kind in {"all", "employee"}:
        for row in appointment_employee:
            emp_uid = str(row.get("employee_uid") or "").strip()
            if category != "all" and employee_bucket_by_uid.get(emp_uid, "other") != category:
                continue
            st = _safe_parse_dt(row.get("start_time"))
            en = _safe_parse_dt(row.get("end_time"))
            if not st or not en or en <= st:
                continue
            if en.date() < date_from or st.date() > date_to:
                continue
            appointment_uids_period.add(str(row.get("appointment_uid") or ""))

    meta = {
        "sum_busy_minutes": int(sum(int(t.get("busy_minutes") or 0) for t in timeseries)),
        "sum_capacity_minutes": int(sum(int(t.get("capacity_minutes") or 0) for t in timeseries)),
        "deficit_minutes_total": deficit_minutes_total,
        "free_slots_over_2h": free_slots_over_2h,
        "free_slots_over_4h": free_slots_over_4h,
        "slot_count": len(slots),
        "appointments_count": len(appointment_uids_period),
        "deficit_slots_pct": round((overloaded_slots / len(slots) * 100), 1) if slots else 0.0,
        "employee_capacity_minutes_map": dict(employee_capacity_minutes_map),
    }
    sb = meta["sum_busy_minutes"]
    scap = meta["sum_capacity_minutes"]
    meta["utilization_pct"] = round((sb / scap * 100), 1) if scap > 0 else 0.0
    return timeseries, meta


@bp.get("/resource_load")
@require_permission("appointment.read")
def resource_load_page():
    sc_uid = (request.args.get("sc_uid") or "").strip()
    date_from_raw = (request.args.get("date_from") or "").strip()
    date_to_raw = (request.args.get("date_to") or "").strip()
    slot_raw = (request.args.get("slot_minutes") or str(SLOT_MINUTES_DEFAULT)).strip()
    slot_minutes = 30 if slot_raw == "30" else 60
    resource_kind = (request.args.get("resource_kind") or "all").strip().lower()
    if resource_kind not in {"all", "employee", "working_zone"}:
        resource_kind = "all"
    category = (request.args.get("category") or "all").strip().lower()
    if category not in {"all", "mechanic", "washer"}:
        category = "all"

    today = date.today()
    default_from = today
    default_to = today + timedelta(days=13)
    try:
        date_from = date.fromisoformat(date_from_raw) if date_from_raw else default_from
    except ValueError:
        date_from = default_from
    try:
        date_to = date.fromisoformat(date_to_raw) if date_to_raw else default_to
    except ValueError:
        date_to = default_to
    if date_to < date_from:
        date_to = date_from
    if (date_to - date_from).days + 1 > MAX_DAYS:
        date_to = date_from + timedelta(days=MAX_DAYS - 1)

    service_centers = _read_json("service_centers.json")
    sc_options = []
    sc_name_by_uid: Dict[str, str] = {}
    for row in service_centers:
        if not isinstance(row, dict):
            continue
        uid = str(row.get("uid") or "").strip()
        if not uid:
            continue
        label = str(row.get("name") or uid)
        sc_options.append({"uid": uid, "label": label})
        sc_name_by_uid[uid] = label
    sc_options.sort(key=lambda x: str(x["label"]).casefold())
    selected_scs = [sc_uid] if sc_uid else [x["uid"] for x in sc_options]
    sc_set = set(selected_scs)

    appointments = {
        str(a.get("uid") or ""): a
        for a in _read_json("appointments.json")
        if isinstance(a, dict)
        and str(a.get("uid") or "")
        and str(a.get("service_center_uid") or "") in sc_set
        and _is_non_cancelled(a)
    }
    appointment_employee = [
        row
        for row in _read_json("appointment_employee.json")
        if isinstance(row, dict) and str(row.get("appointment_uid") or "") in appointments
    ]
    appointment_wz = [
        row
        for row in _read_json("appointment_working_zone.json")
        if isinstance(row, dict) and str(row.get("appointment_uid") or "") in appointments
    ]

    employees = [
        row
        for row in _read_json("employees.json")
        if isinstance(row, dict)
        and str(row.get("service_center_uid") or "") in sc_set
        and bool(row.get("is_available")) is True
        and bool(row.get("is_fired")) is False
    ]
    working_zones = [
        row
        for row in _read_json("working_zones.json")
        if isinstance(row, dict)
        and str(row.get("service_center_uid") or "") in sc_set
        and bool(row.get("is_available")) is True
    ]
    rt_label: Dict[str, str] = {}
    rt_parent: Dict[str, str] = {}
    rt_child: Dict[str, str] = {}
    for r in _read_json("resource_types.json"):
        if not isinstance(r, dict):
            continue
        uid = str(r.get("uid") or "").strip()
        if not uid:
            continue
        parent = str(r.get("parent_resource_type") or "").strip()
        child = str(r.get("children_resource_type") or "").strip()
        rt_parent[uid] = parent
        rt_child[uid] = child
        if child and parent:
            rt_label[uid] = f"{child} ({parent})"
        else:
            rt_label[uid] = child or parent or uid

    employee_rt_map: Dict[str, str] = {}
    for row in _read_json("employee_resource_types.json"):
        if not isinstance(row, dict):
            continue
        emp_uid = str(row.get("employee_uid") or "").strip()
        rt_uid = str(row.get("resource_type_uid") or "").strip()
        if emp_uid and rt_uid and emp_uid not in employee_rt_map:
            employee_rt_map[emp_uid] = rt_uid
    employee_bucket_by_uid: Dict[str, str] = {}
    for emp in employees:
        emp_uid = str(emp.get("uid") or "").strip()
        if not emp_uid:
            continue
        rt_uid = employee_rt_map.get(emp_uid, "")
        employee_bucket_by_uid[emp_uid] = _classify_bucket(rt_parent.get(rt_uid, ""), rt_child.get(rt_uid, ""))
    wz_bucket_by_uid: Dict[str, str] = {}
    for wz in working_zones:
        wz_uid = str(wz.get("uid") or "").strip()
        if not wz_uid:
            continue
        rt_uid = str(wz.get("resource_type_uid") or "").strip()
        wz_bucket_by_uid[wz_uid] = _classify_bucket(rt_parent.get(rt_uid, ""), rt_child.get(rt_uid, ""))

    timeseries, ts_meta = _compute_load_timeseries(date_from, date_to, slot_minutes, sc_set, resource_kind, category)
    deficit_minutes_total = int(ts_meta.get("deficit_minutes_total") or 0)
    free_slots_over_2h = int(ts_meta.get("free_slots_over_2h") or 0)
    free_slots_over_4h = int(ts_meta.get("free_slots_over_4h") or 0)
    deficit_slots_pct = float(ts_meta.get("deficit_slots_pct") or 0.0)
    employee_capacity_minutes_map: Dict[str, int] = dict(ts_meta.get("employee_capacity_minutes_map") or {})

    employee_rows = []
    busy_emp = defaultdict(int)
    cnt_emp = defaultdict(int)
    for rel in appointment_employee:
        uid = str(rel.get("employee_uid") or "")
        st = _safe_parse_dt(rel.get("start_time"))
        en = _safe_parse_dt(rel.get("end_time"))
        if not uid or not st or not en:
            continue
        minutes = _minutes_overlap(st, en, datetime.combine(date_from, time.min), datetime.combine(date_to + timedelta(days=1), time.min))
        if minutes <= 0:
            continue
        busy_emp[uid] += minutes
        cnt_emp[uid] += 1
    period_days = (date_to - date_from).days + 1
    per_resource_capacity = period_days * int((datetime.combine(date.min, DAY_END) - datetime.combine(date.min, DAY_START)).total_seconds() // 60)
    for emp in employees:
        uid = str(emp.get("uid") or "")
        busy = int(busy_emp.get(uid, 0))
        cap = int(employee_capacity_minutes_map.get(uid, 0))
        rt_uid = employee_rt_map.get(uid, "")
        parent_label = rt_parent.get(rt_uid, "")
        child_label = rt_child.get(rt_uid, "")
        bucket = _classify_bucket(parent_label, child_label)
        employee_rows.append(
            {
                "resource_uid": uid,
                "sso_id": str(emp.get("sso_id") or ""),
                "full_name": "—",
                "resource_type_label": rt_label.get(rt_uid, rt_uid or "—"),
                "bucket": bucket,
                "service_center_uid": str(emp.get("service_center_uid") or ""),
                "service_center_name": sc_name_by_uid.get(str(emp.get("service_center_uid") or ""), "—"),
                "busy_minutes": busy,
                "capacity_minutes": cap,
                "load_pct": round((busy / cap * 100), 1) if cap > 0 else 0.0,
                "appointments_count": int(cnt_emp.get(uid, 0)),
                "avg_appointment_minutes": round((busy / int(cnt_emp.get(uid, 0))), 1) if int(cnt_emp.get(uid, 0)) > 0 else 0.0,
            }
        )
    employee_rows.sort(key=lambda x: x["load_pct"], reverse=True)
    if category != "all":
        employee_rows = [row for row in employee_rows if str(row.get("bucket") or "") == category]

    busy_wz = defaultdict(int)
    cnt_wz = defaultdict(int)
    for rel in appointment_wz:
        uid = str(rel.get("working_zone_uid") or "")
        st = _safe_parse_dt(rel.get("start_time"))
        en = _safe_parse_dt(rel.get("end_time"))
        if not uid or not st or not en:
            continue
        minutes = _minutes_overlap(st, en, datetime.combine(date_from, time.min), datetime.combine(date_to + timedelta(days=1), time.min))
        if minutes <= 0:
            continue
        busy_wz[uid] += minutes
        cnt_wz[uid] += 1
    wz_rows = []
    for wz in working_zones:
        uid = str(wz.get("uid") or "")
        busy = int(busy_wz.get(uid, 0))
        cap = per_resource_capacity
        rt_uid = str(wz.get("resource_type_uid") or "")
        parent_label = rt_parent.get(rt_uid, "")
        child_label = rt_child.get(rt_uid, "")
        bucket = _classify_bucket(parent_label, child_label)
        wz_rows.append(
            {
                "resource_uid": uid,
                "description": str(wz.get("description") or "—"),
                "resource_type_label": rt_label.get(rt_uid, rt_uid or "—"),
                "bucket": bucket,
                "service_center_uid": str(wz.get("service_center_uid") or ""),
                "service_center_name": sc_name_by_uid.get(str(wz.get("service_center_uid") or ""), "—"),
                "busy_minutes": busy,
                "capacity_minutes": cap,
                "load_pct": round((busy / cap * 100), 1) if cap > 0 else 0.0,
                "appointments_count": int(cnt_wz.get(uid, 0)),
                "avg_appointment_minutes": round((busy / int(cnt_wz.get(uid, 0))), 1) if int(cnt_wz.get(uid, 0)) > 0 else 0.0,
            }
        )
    wz_rows.sort(key=lambda x: x["load_pct"], reverse=True)
    if category != "all":
        wz_rows = [row for row in wz_rows if str(row.get("bucket") or "") == category]

    selected_employee_rows = employee_rows if resource_kind in {"all", "employee"} else []
    selected_wz_rows = wz_rows if resource_kind in {"all", "working_zone"} else []
    selected_employee_uids = {str(r.get("resource_uid") or "") for r in selected_employee_rows}
    selected_wz_uids = {str(r.get("resource_uid") or "") for r in selected_wz_rows}
    employee_bucket_stats = _build_bucket_stats(selected_employee_rows)
    wz_bucket_stats = _build_bucket_stats(selected_wz_rows)
    total_resources = len(selected_employee_rows) + len(selected_wz_rows)
    engaged_resources = len([r for r in selected_employee_rows if r["busy_minutes"] > 0]) + len(
        [r for r in selected_wz_rows if r["busy_minutes"] > 0]
    )
    total_busy_minutes_selected = int(
        sum(int(r.get("busy_minutes") or 0) for r in selected_employee_rows)
        + sum(int(r.get("busy_minutes") or 0) for r in selected_wz_rows)
    )
    total_capacity_minutes_selected = int(
        sum(int(r.get("capacity_minutes") or 0) for r in selected_employee_rows)
        + sum(int(r.get("capacity_minutes") or 0) for r in selected_wz_rows)
    )
    engaged_resources_pct = round((engaged_resources / total_resources * 100), 1) if total_resources else 0.0
    active_rows = [r for r in selected_employee_rows + selected_wz_rows if int(r.get("busy_minutes") or 0) > 0]
    active_avg_load = (
        round(sum(float(r.get("load_pct") or 0.0) for r in active_rows) / len(active_rows), 1) if active_rows else 0.0
    )
    utilization_period_pct = (
        round((total_busy_minutes_selected / total_capacity_minutes_selected * 100), 1)
        if total_capacity_minutes_selected > 0
        else 0.0
    )
    period_start = datetime.combine(date_from, time.min)
    period_end = datetime.combine(date_to + timedelta(days=1), time.min)
    booking_minutes_by_uid: Dict[str, int] = {}
    for ap_uid, ap in appointments.items():
        st = _safe_parse_dt(ap.get("start_time"))
        en = _safe_parse_dt(ap.get("end_time"))
        if not st or not en or en <= st:
            continue
        minutes = _minutes_overlap(st, en, period_start, period_end)
        if minutes > 0:
            booking_minutes_by_uid[ap_uid] = minutes

    employee_appointment_uids: set[str] = set()
    for rel in appointment_employee:
        emp_uid = str(rel.get("employee_uid") or "").strip()
        if emp_uid not in selected_employee_uids:
            continue
        if category != "all" and employee_bucket_by_uid.get(emp_uid, "other") != category:
            continue
        ap_uid = str(rel.get("appointment_uid") or "").strip()
        if ap_uid in booking_minutes_by_uid:
            employee_appointment_uids.add(ap_uid)

    wz_appointment_uids: set[str] = set()
    for rel in appointment_wz:
        wz_uid = str(rel.get("working_zone_uid") or "").strip()
        if wz_uid not in selected_wz_uids:
            continue
        if category != "all" and wz_bucket_by_uid.get(wz_uid, "other") != category:
            continue
        ap_uid = str(rel.get("appointment_uid") or "").strip()
        if ap_uid in booking_minutes_by_uid:
            wz_appointment_uids.add(ap_uid)

    if resource_kind == "employee":
        selected_appointment_uids = set(employee_appointment_uids)
    elif resource_kind == "working_zone":
        selected_appointment_uids = set(wz_appointment_uids)
    else:
        selected_appointment_uids = employee_appointment_uids | wz_appointment_uids

    selected_appointments_total = len(selected_appointment_uids)
    employee_appointments_total = len(employee_appointment_uids)
    wz_appointments_total = len(wz_appointment_uids)
    avg_booking_minutes_overall = (
        round(sum(booking_minutes_by_uid[uid] for uid in selected_appointment_uids) / selected_appointments_total, 1)
        if selected_appointments_total > 0
        else 0.0
    )
    avg_booking_minutes_employee = (
        round(sum(booking_minutes_by_uid[uid] for uid in employee_appointment_uids) / employee_appointments_total, 1)
        if employee_appointments_total > 0
        else 0.0
    )
    avg_booking_minutes_wz = (
        round(sum(booking_minutes_by_uid[uid] for uid in wz_appointment_uids) / wz_appointments_total, 1)
        if wz_appointments_total > 0
        else 0.0
    )
    avg_load = round(
        (
            sum(r["load_pct"] for r in selected_employee_rows)
            + sum(r["load_pct"] for r in selected_wz_rows)
        )
        / total_resources,
        1,
    ) if total_resources else 0.0
    peak_load = max([0.0] + [float(ts["load_pct"]) for ts in timeseries])
    fte_needed, headcount_delta, decision = _calc_fte_and_decision(deficit_minutes_total, period_days)

    # Прогноз по механикам: от сегодняшней даты (независимо от выбранного периода в форме).
    forecast_anchor = today
    forecast_end_3 = forecast_anchor + timedelta(days=3)
    forecast_end_7 = forecast_anchor + timedelta(days=7)
    _, mech_3_meta = _compute_load_timeseries(
        forecast_anchor, forecast_end_3, slot_minutes, sc_set, "employee", "mechanic"
    )
    _, mech_7_meta = _compute_load_timeseries(
        forecast_anchor, forecast_end_7, slot_minutes, sc_set, "employee", "mechanic"
    )
    mechanic_forecast = {
        "anchor": forecast_anchor.isoformat(),
        "window_3": {
            "date_to": forecast_end_3.isoformat(),
            "utilization_pct": float(mech_3_meta.get("utilization_pct") or 0.0),
            "busy_hours": round(int(mech_3_meta.get("sum_busy_minutes") or 0) / 60.0, 1),
            "available_hours": round(int(mech_3_meta.get("sum_capacity_minutes") or 0) / 60.0, 1),
            "appointments": int(mech_3_meta.get("appointments_count") or 0),
            "free_slots_over_2h": int(mech_3_meta.get("free_slots_over_2h") or 0),
            "free_slots_over_4h": int(mech_3_meta.get("free_slots_over_4h") or 0),
            "deficit_hours": round(int(mech_3_meta.get("deficit_minutes_total") or 0) / 60.0, 1),
        },
        "window_7": {
            "date_to": forecast_end_7.isoformat(),
            "utilization_pct": float(mech_7_meta.get("utilization_pct") or 0.0),
            "busy_hours": round(int(mech_7_meta.get("sum_busy_minutes") or 0) / 60.0, 1),
            "available_hours": round(int(mech_7_meta.get("sum_capacity_minutes") or 0) / 60.0, 1),
            "appointments": int(mech_7_meta.get("appointments_count") or 0),
            "free_slots_over_2h": int(mech_7_meta.get("free_slots_over_2h") or 0),
            "free_slots_over_4h": int(mech_7_meta.get("free_slots_over_4h") or 0),
            "deficit_hours": round(int(mech_7_meta.get("deficit_minutes_total") or 0) / 60.0, 1),
        },
    }

    # Heatmap: средний дефицит по день-недели/часу
    hm_acc: Dict[Tuple[int, int], Dict[str, int]] = defaultdict(lambda: {"sum": 0, "count": 0})
    for ts in timeseries:
        key = (int(ts.get("weekday") or 0), int(ts.get("hour") or 0))
        hm_acc[key]["sum"] += int(ts.get("deficit_minutes") or 0)
        hm_acc[key]["count"] += 1
    heatmap_rows: List[Dict[str, Any]] = []
    max_deficit_avg = 0.0
    heatmap_matrix: Dict[int, Dict[int, float]] = {}
    heatmap_hours = list(range(int(DAY_START.hour), int(DAY_END.hour)))
    for wd in range(1, 8):
        heatmap_matrix[wd] = {}
        for hour in range(int(DAY_START.hour), int(DAY_END.hour)):
            cell = hm_acc.get((wd, hour), {"sum": 0, "count": 0})
            avg = (cell["sum"] / cell["count"]) if cell["count"] > 0 else 0.0
            max_deficit_avg = max(max_deficit_avg, avg)
            avg_rounded = round(avg, 1)
            heatmap_rows.append({"weekday": wd, "hour": hour, "avg_deficit_minutes": avg_rounded})
            heatmap_matrix[wd][hour] = avg_rounded

    # Для canvas ограничим количество точек, чтобы UI не тормозил.
    chart_step = max(1, len(timeseries) // 220)
    chart_points = [
        {
            "label": str(row.get("ts") or ""),
            "busy": int(row.get("busy_minutes") or 0),
            "capacity": int(row.get("capacity_minutes") or 0),
        }
        for idx, row in enumerate(timeseries)
        if idx % chart_step == 0
    ]
    daily_acc: Dict[str, Dict[str, int]] = defaultdict(lambda: {"busy": 0, "capacity": 0, "deficit": 0})
    for row in timeseries:
        ts_raw = str(row.get("ts") or "")
        day_key = ts_raw[:10] if len(ts_raw) >= 10 else ts_raw
        daily_acc[day_key]["busy"] += int(row.get("busy_minutes") or 0)
        daily_acc[day_key]["capacity"] += int(row.get("capacity_minutes") or 0)
        daily_acc[day_key]["deficit"] += int(row.get("deficit_minutes") or 0)
    daily_chart_points = []
    for day_key in sorted(daily_acc.keys()):
        busy_m = int(daily_acc[day_key]["busy"])
        cap_m = int(daily_acc[day_key]["capacity"])
        deficit_m = int(daily_acc[day_key]["deficit"])
        daily_chart_points.append(
            {
                "day": day_key,
                "busy_hours": round(busy_m / 60.0, 1),
                "capacity_hours": round(cap_m / 60.0, 1),
                "deficit_hours": round(deficit_m / 60.0, 1),
                "utilization_pct": round((busy_m / cap_m * 100), 1) if cap_m > 0 else 0.0,
            }
        )

    return render_template(
        "analytics/resource_load.html",
        filters={
            "sc_uid": sc_uid,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "slot_minutes": slot_minutes,
            "resource_kind": resource_kind,
            "category": category,
        },
        sc_options=sc_options,
        selected_sc_label=sc_name_by_uid.get(sc_uid, "") if sc_uid else "",
        kpi={
            "total_resources": total_resources,
            "engaged_resources": engaged_resources,
            "engaged_resources_pct": engaged_resources_pct,
            "avg_load_pct": avg_load,
            "active_avg_load_pct": active_avg_load,
            "utilization_period_pct": utilization_period_pct,
            "peak_load_pct": round(peak_load, 1),
            "deficit_hours_total": round(deficit_minutes_total / 60.0, 1),
            "deficit_slots_pct": deficit_slots_pct,
            "appointments_total": selected_appointments_total,
            "busy_hours_total": round(total_busy_minutes_selected / 60.0, 1),
            "available_hours_total": round(total_capacity_minutes_selected / 60.0, 1),
            "avg_booking_minutes_overall": avg_booking_minutes_overall,
            "avg_booking_minutes_employee": avg_booking_minutes_employee,
            "avg_booking_minutes_wz": avg_booking_minutes_wz,
            "free_slots_over_2h": free_slots_over_2h,
            "free_slots_over_4h": free_slots_over_4h,
        },
        staff_recommendation={
            "fte_needed": fte_needed,
            "recommended_headcount_delta": headcount_delta,
            "decision": decision,
            "reason": (
                f"Дефицит в {deficit_slots_pct}% слотов, {round(deficit_minutes_total / 60.0, 1)} часов дефицита за период"
            ),
        },
        timeseries=timeseries,
        chart_points=chart_points,
        daily_chart_points=daily_chart_points,
        heatmap_rows=heatmap_rows,
        heatmap_matrix=heatmap_matrix,
        heatmap_hours=heatmap_hours,
        heatmap_max_avg_deficit=round(max_deficit_avg, 1),
        employee_rows=selected_employee_rows[:200],
        wz_rows=selected_wz_rows[:200],
        employee_avg_load=round(
            (sum(r["load_pct"] for r in employee_rows) / len(employee_rows)), 1
        ) if employee_rows else 0.0,
        wz_avg_load=round(
            (sum(r["load_pct"] for r in wz_rows) / len(wz_rows)), 1
        ) if wz_rows else 0.0,
        employee_bucket_stats=employee_bucket_stats,
        wz_bucket_stats=wz_bucket_stats,
        has_data=bool(timeseries),
        mechanic_forecast=mechanic_forecast,
    )
