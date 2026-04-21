# [RMS] Админка: страница "Загруженность ресурсов" (Сотрудники + Working Zones)

## 1) Зачем нужна страница

Сейчас в админке есть списки сотрудников и рабочих зон, но нет единого ответа на вопрос:
- кто и какие ресурсы перегружены;
- где есть недозагрузка;
- как выглядит загрузка по СЦ / по дню / по ресурсу.

Новая страница должна дать операционный срез по загрузке ресурсов на основе фактических данных записей.

---

## 2) Какие данные уже есть (по примерам из `admin-panel-flask/JSON`)

Минимально нужные таблицы/сущности:

- `appointments.json`
  - `uid`, `service_center_uid`, `start_time`, `end_time`, `is_deleted`, ...
- `appointment_employee.json`
  - связь запись -> сотрудник: `appointment_uid`, `employee_uid`, `start_time`, `end_time`, ...
- `appointment_working_zone.json`
  - связь запись -> рабочая зона: `appointment_uid`, `working_zone_uid`, `start_time`, `end_time`, ...
- `employees.json`
  - `uid`, `service_center_uid`, `sso_id`, `is_available`, `is_fired`, `is_external`, ...
- `working_zones.json`
  - `uid`, `service_center_uid`, `resource_type_uid`, `is_available`, `description`, ...
- `resource_types.json`
  - для человекочитаемых названий типов зон/ресурсов.

Это уже позволяет считать загрузку:
- по сотрудникам;
- по рабочим зонам;
- в разрезе СЦ, дат и времени.

---

## 3) Предложение по UX/структуре страницы

Маршрут (предложение):
- `/analytics/resource_load` или `/service_centers/resource_load`

Название страницы:
- `Загруженность ресурсов`

### 3.1 Верхний блок фильтров

Фильтры:
- СЦ (обязательный или "Все СЦ")
- Период: `Дата от`, `Дата до` (обязательно)
- Режим агрегации:
  - `По дням`
  - `По часам`
- Тип ресурса:
  - `Сотрудники`
  - `Рабочие зоны`
  - `Оба`
- Тумблеры:
  - `Только доступные` (is_available=true)
  - `Исключить уволенных` (для сотрудников)

Кнопки:
- `Построить`
- `Сбросить`

### 3.2 KPI-блок (карточки)

Карточки сверху:
- `Всего ресурсов`
- `Задействовано ресурсов`
- `Средняя загрузка, %`
- `Пиковая загрузка, %`
- `Ресурсов с перегрузкой (>90%)`

### 3.3 Таб "Сотрудники"

Таблица:
- Ресурс (ФИО из SSO, fallback SSO_ID)
- СЦ
- Занято часов за период
- Доступно часов за период
- Загрузка, %
- Кол-во записей
- Последняя запись

Фичи:
- сортировка по загрузке (по убыванию);
- подсветка:
  - красный >90%
  - желтый 70-90%
  - зеленый <70%

### 3.4 Таб "Рабочие зоны"

Таблица:
- Зона (uid + description)
- Тип зоны (из `resource_types`)
- СЦ
- Занято часов за период
- Доступно часов за период
- Загрузка, %
- Кол-во записей

### 3.5 Доп. визуализация (опционально, v2)

- Heatmap по часам дня (ось X = часы, Y = ресурс)
- "Топ-10 перегруженных ресурсов"
- "Топ-10 недозагруженных ресурсов"

---

## 4) Как считать загрузку (базовая формула)

Для каждого ресурса в периоде:

- `busy_minutes` = сумма пересечений интервалов записи и интервала фильтра.
- `available_minutes`:
  - v1 (упрощенно): фиксированная дневная норма * число дней (например 11 часов/день по расписанию СЦ).
  - v2 (точно): по расписанию СЦ/ресурса, исключая off-days/maintenance.
- `load_pct = busy_minutes / available_minutes * 100`.

Важно:
- если у одной записи у ресурса несколько кусков времени, учитывать каждый интервал отдельно;
- корректно обрабатывать пересечения интервалов;
- исключать удаленные/неактуальные записи (`is_deleted=true`).

---

## 5) Модель данных для backend-агрегации (предложение)

Единая промежуточная структура:

- `resource_kind`: `employee` | `working_zone`
- `resource_uid`
- `service_center_uid`
- `start_time`
- `end_time`
- `appointment_uid`

Откуда берем:
- employee-слой из `appointment_employee`;
- zone-слой из `appointment_working_zone`.

После этого агрегация одинаковая для обоих типов.

---

## 6) План реализации (итерациями)

## Этап 1 (быстрый MVP)
- Новая страница + фильтры.
- Агрегация загрузки за период.
- 2 таблицы (сотрудники/зоны) + KPI карточки.
- Без сложных графиков.

## Этап 2
- Учет расписаний и off-days в `available_minutes`.
- Детализация по дням/часам.
- Экспорт CSV.

## Этап 3
- Heatmap + тренды.
- Алерты по порогам загрузки.

---

## 7) RBAC

Предложение:
- Просмотр страницы: новое permission `analytics.resource_load.read`
- Если не хотим новое право:
  - fallback: `service_center.read` + `appointment.read`.

---

## 8) Потенциальные риски

- Тяжелые выборки за большие периоды.
- Неполные связи (запись есть, а employee/working_zone link отсутствует).
- Погрешность, если не учитывать расписание и выходные.
- Зависимость от SSO для ФИО сотрудников.

Митигации:
- ограничение периода (например максимум 31 день);
- пагинация и/или async построение отчета;
- кеш на 1-5 минут для одинаковых фильтров.

---

## 9) Зафиксированные решения (по итогам обсуждения)

1. Режим нужен для всех СЦ, но первый релиз можно запускать на одном СЦ как reference.
2. Визуализация нужна в формате графика с учетом day on / day off.
3. В загрузке учитываем только неотмененные бронирования.
4. Drill-down до списка записей по клику по ресурсу на этом этапе не нужен.
5. Экспорт (CSV/XLSX) на этом этапе не нужен.

---

## 10) Как показать "нужен ли еще сотрудник в СЦ"

Ниже конкретный, понятный для бизнеса сценарий визуализации.

### 10.1 Главная идея

Считать не только факт занятости, но и "дефицит мощности":
- сколько минут спроса пришло в конкретный час;
- сколько минут мощности у доступных сотрудников есть в этот же час;
- где спрос > мощность.

Если дефицит повторяется в одни и те же часы/дни -> в этот СЦ вероятно нужен дополнительный сотрудник.

### 10.2 Блоки на странице

1) **График "Спрос vs Мощность" (основной)**
- Ось X: день/часы (например, по 30-мин или 60-мин слотам).
- Линия 1: `Загруженные минуты` (неотмененные бронирования).
- Линия 2: `Доступные минуты` (сотрудники в day on, исключая day off).
- Заливка красным между линиями там, где `загруженные > доступные`.

2) **Heatmap дефицита по времени**
- Ось Y: дни недели, ось X: часы.
- Цвет = средний дефицит минут.
- Сразу видно "узкие места" (например, Пн-Пт 10:00-13:00).

3) **KPI для решения о найме**
- `% слотов с дефицитом`
- `Суммарный дефицит часов за период`
- `Максимальный непрерывный дефицит (часы)`
- `Рекомендуемая добавка FTE` (см. формулу ниже)

4) **Простой вывод-вердикт**
- "Доп. сотрудник требуется / не требуется"
- плюс текст "почему" (например: дефицит в 23% часов, 46 часов дефицита за 30 дней).

### 10.3 Формула рекомендации по FTE (упрощенно)

- `deficit_minutes_total = sum(max(0, busy_minutes_slot - capacity_minutes_slot))`
- `one_fte_minutes_period = рабочие_минуты_1_сотрудника_за_период`
- `fte_needed = deficit_minutes_total / one_fte_minutes_period`

Порог рекомендации:
- `fte_needed >= 0.7` -> показываем "нужен +1 сотрудник" (можно настроить).

### 10.4 Учет day on / day off

В `capacity_minutes_slot` включаем только сотрудников:
- `is_available = true`
- `is_fired = false`
- у кого слот попадает в рабочее время (day on/schedule)
- и не попадает в day off.

### 10.5 Что считаем "неотмененным бронированием"

Для расчета спроса использовать записи со статусами, которые не равны отмене.
Технически: фильтр по статусам, согласованный с RMS (например `active`, `completed`, возможно `in_progress`; исключить `cancelled`).

---

## 11) Открытые вопросы (после фиксации)

1. Какие точные статусы RMS считаем неотмененными (перечень enum)?
2. Какой шаг агрегации по времени берем в v1: 30 или 60 минут?
3. Какой горизонт анализа по умолчанию: 14 или 30 дней?
4. Какой порог для "рекомендовать +1 сотрудника": 0.6 / 0.7 / 0.8 FTE?
5. Нужна ли отдельно рекомендация по типу ресурса сотрудника (механик/мойщик)?

---

## 12) Критерии готовности (DoD, предложение)

- Страница открывается без таймаута на периоде до 14 дней.
- Есть фильтры, KPI и таблицы по сотрудникам/зонам.
- Значения загрузки пересчитываются корректно при смене фильтров.
- Ошибки API показываются пользователю, страница не падает.
- Доступ контролируется через RBAC.

---

## 13) API-контракт (v2, proposal)

Ниже предлагаем единый endpoint для страницы загрузки ресурсов.

### 13.1 Endpoint

- `POST /api/v1/analytics/resource_load`

### 13.2 Request JSON

```json
{
  "service_center_uids": ["01987b1c-18d3-7567-83b1-614772ba0e1f"],
  "date_from": "2026-04-01",
  "date_to": "2026-04-30",
  "slot_minutes": 60,
  "include_resource_types": ["employee", "working_zone"],
  "exclude_cancelled": true,
  "exclude_fired": true,
  "only_available": true,
  "timezone": "Europe/Moscow"
}
```

Пояснения:
- `service_center_uids`: можно один СЦ (MVP) или несколько/все.
- `slot_minutes`: 30 или 60.
- `include_resource_types`: `employee`, `working_zone`.
- `exclude_cancelled=true`: учитывать только неотмененные бронирования.
- `exclude_fired=true`: не учитывать уволенных сотрудников в capacity.
- `only_available=true`: только доступные ресурсы.

### 13.3 Response JSON (предложение)

```json
{
  "filters": {
    "service_center_uids": ["01987b1c-18d3-7567-83b1-614772ba0e1f"],
    "date_from": "2026-04-01",
    "date_to": "2026-04-30",
    "slot_minutes": 60,
    "timezone": "Europe/Moscow"
  },
  "kpi": {
    "total_resources": 42,
    "engaged_resources": 37,
    "avg_load_pct": 74.3,
    "peak_load_pct": 118.0,
    "overloaded_resources_count": 9,
    "deficit_minutes_total": 2760,
    "deficit_hours_total": 46.0
  },
  "timeseries": [
    {
      "ts": "2026-04-01T09:00:00+03:00",
      "busy_minutes": 780,
      "capacity_minutes": 720,
      "deficit_minutes": 60
    },
    {
      "ts": "2026-04-01T10:00:00+03:00",
      "busy_minutes": 690,
      "capacity_minutes": 720,
      "deficit_minutes": 0
    }
  ],
  "heatmap": [
    {
      "weekday": 1,
      "hour": 9,
      "avg_deficit_minutes": 22
    },
    {
      "weekday": 1,
      "hour": 10,
      "avg_deficit_minutes": 9
    }
  ],
  "employees": [
    {
      "resource_uid": "0198a2c3-ffaa-741c-97ad-393f9274946f",
      "sso_id": "3440dbfe-fbd9-4185-b7b3-443d974d6e72",
      "full_name": "Иванов Иван Иванович",
      "service_center_uid": "01987b1c-18d3-7567-83b1-614772ba0e1f",
      "busy_minutes": 7810,
      "capacity_minutes": 9240,
      "load_pct": 84.5,
      "appointments_count": 126,
      "peak_slot_load_pct": 100
    }
  ],
  "working_zones": [
    {
      "resource_uid": "01987f59-1731-7f72-954c-76b23b499099",
      "description": "Пост 1",
      "resource_type_uid": "01987f58-c5c5-7f07-a342-2b18ce36ec33",
      "resource_type_label": "Пост (Зона ремонта)",
      "service_center_uid": "01987b1c-18d3-7567-83b1-614772ba0e1f",
      "busy_minutes": 6420,
      "capacity_minutes": 9240,
      "load_pct": 69.5,
      "appointments_count": 101
    }
  ],
  "staff_recommendation": {
    "fte_needed": 0.73,
    "recommended_headcount_delta": 1,
    "decision": "add_staff",
    "reason": "Дефицит в 23% слотов, 46.0 часов дефицита за период"
  }
}
```

### 13.4 Ошибки API (предложение)

- `400`: невалидный диапазон дат, неподдерживаемый `slot_minutes`.
- `403`: нет прав на аналитику.
- `422`: неконсистентные фильтры.
- `500`: ошибка агрегации.

Формат ошибки:

```json
{
  "error": {
    "message": "Ошибка валидации параметров",
    "details": [
      {"field": "date_to", "message": "Дата должна быть >= date_from"}
    ]
  }
}
```

### 13.5 Производительность (ожидания к endpoint)

- Период по умолчанию: 14 дней.
- Максимум в одном запросе: 31 день.
- Ответ для одного СЦ должен укладываться в ~1-3 секунды на прогретом кеше.
- Кеш ключа фильтров: 1-5 минут.

---

## 14) V3: Алгоритм расчета capacity и дефицита (day on / day off)

Ниже прикладной план реализации backend-агрегации, чтобы сократить время проектирования.

### 14.1 Базовые определения

- `slot`: временной сегмент фиксированного размера (30/60 минут).
- `busy_minutes_slot`: сколько минут в слоте занято неотмененными записями.
- `capacity_minutes_slot`: сколько минут в слоте доступно по ресурсам.
- `deficit_minutes_slot = max(0, busy_minutes_slot - capacity_minutes_slot)`.

### 14.2 Источники для capacity

Для сотрудников:
- `employees` (`is_available`, `is_fired`, `service_center_uid`)
- расписания (таблицы `schedules`, `staff_schedules`, либо актуальные API-источники)
- выходные (off-days/holidays/sc_off_days, в зависимости от фактической модели)

Для working zones:
- `working_zones` (`is_available`, `service_center_uid`)
- расписание СЦ/зоны (если зона наследует расписание СЦ — использовать его)
- `wz_maintenance_days` и иные исключающие интервалы

Для спроса (busy):
- `appointments` (фильтр неотмененных)
- `appointment_employee`
- `appointment_working_zone`

### 14.3 Логика расчета capacity на слот

1. Собрать набор активных ресурсов на период:
   - employee: `is_available=true`, `is_fired=false`, `service_center_uid in filter`.
   - working_zone: `is_available=true`, `service_center_uid in filter`.

2. Для каждого ресурса построить "рабочие интервалы":
   - day on: интервалы по расписанию;
   - вычесть day off / maintenance интервалы.

3. Разбить период на слоты (`generate_series`).

4. Для каждого слота посчитать:
   - сколько минут этого слота перекрыто рабочими интервалами ресурса;
   - суммировать по ресурсам -> `capacity_minutes_slot`.

5. В параллель посчитать `busy_minutes_slot`:
   - взять интервалы из `appointment_employee` / `appointment_working_zone`;
   - пересечь их со слотом;
   - суммировать минуты.

6. Получить `deficit_minutes_slot` и агрегировать в KPI.

### 14.4 Псевдо-SQL (PostgreSQL, концепт)

```sql
-- 1) Слоты периода
WITH slots AS (
  SELECT
    gs AS slot_start,
    gs + (:slot_minutes || ' minutes')::interval AS slot_end
  FROM generate_series(:date_from::timestamp, :date_to::timestamp, (:slot_minutes || ' minutes')::interval) gs
),

-- 2) Интервалы занятости (пример employee)
busy AS (
  SELECT
    ae.employee_uid AS resource_uid,
    ae.start_time,
    ae.end_time
  FROM appointment_employee ae
  JOIN appointments a ON a.uid = ae.appointment_uid
  WHERE a.is_deleted = false
    AND a.service_center_uid = ANY(:service_center_uids)
    AND a.status <> 'cancelled'
),

-- 3) Интервалы доступности (после вычитания day off)
capacity AS (
  SELECT
    e.uid AS resource_uid,
    wi.start_time,
    wi.end_time
  FROM employees e
  JOIN working_intervals wi ON wi.resource_uid = e.uid
  WHERE e.is_available = true
    AND e.is_fired = false
    AND e.service_center_uid = ANY(:service_center_uids)
),

-- 4) Минуты занятости по слотам
busy_slots AS (
  SELECT
    s.slot_start,
    SUM(
      GREATEST(
        0,
        EXTRACT(EPOCH FROM LEAST(b.end_time, s.slot_end) - GREATEST(b.start_time, s.slot_start)) / 60
      )
    ) AS busy_minutes
  FROM slots s
  LEFT JOIN busy b ON b.start_time < s.slot_end AND b.end_time > s.slot_start
  GROUP BY s.slot_start
),

-- 5) Минуты доступности по слотам
capacity_slots AS (
  SELECT
    s.slot_start,
    SUM(
      GREATEST(
        0,
        EXTRACT(EPOCH FROM LEAST(c.end_time, s.slot_end) - GREATEST(c.start_time, s.slot_start)) / 60
      )
    ) AS capacity_minutes
  FROM slots s
  LEFT JOIN capacity c ON c.start_time < s.slot_end AND c.end_time > s.slot_start
  GROUP BY s.slot_start
)

SELECT
  s.slot_start,
  COALESCE(b.busy_minutes, 0) AS busy_minutes,
  COALESCE(c.capacity_minutes, 0) AS capacity_minutes,
  GREATEST(0, COALESCE(b.busy_minutes, 0) - COALESCE(c.capacity_minutes, 0)) AS deficit_minutes
FROM slots s
LEFT JOIN busy_slots b USING(slot_start)
LEFT JOIN capacity_slots c USING(slot_start)
ORDER BY s.slot_start;
```

Примечание: `working_intervals` в примере — промежуточное представление/CTE, где уже применены day on/day off правила.

### 14.5 Практические правила на MVP

Чтобы быстро запустить первую версию:
- шаг слота = 60 минут;
- период по умолчанию = 14 дней;
- ограничение = 31 день;
- timezone фиксировать на уровне запроса (`Europe/Moscow`);
- агрегация отдельно для `employee` и `working_zone`.

### 14.6 Формирование вердикта "нужен сотрудник"

Правило (предложение):
- `deficit_hours_total >= 40` за 30 дней ИЛИ
- `deficit_slots_pct >= 20%`
-> флаг `add_staff`.

`recommended_headcount_delta`:
- `ceil(fte_needed)` при `fte_needed >= 0.7`,
- иначе `0`.

### 14.7 Проверки качества расчета

1. Слот без ресурсов -> `capacity=0`, дефицит равен busy.
2. День с day off всех сотрудников -> capacity падает до 0.
3. Отмененные записи не влияют на busy.
4. Дублирующие связи appointment-resource не должны умножать busy.
5. Сумма slot busy должна совпадать с контрольным расчетом за период (с допустимой погрешностью округления).

