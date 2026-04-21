# Загруженность ресурсов: источники данных и расчёты

Документ описывает, **из каких JSON-таблиц** и **в каком порядке** собираются данные для страницы:

- `GET /analytics/resource_load`
- код: `admin-panel-flask/app/modules/analytics/routes.py`

---

## 1) Какие таблицы участвуют

Базовый каталог данных: `admin-panel-flask/JSON`.

### 1.1. Основные таблицы загрузки

- `service_centers.json`  
  Используется для списка СЦ в фильтре и отображения названия СЦ.

- `appointments.json`  
  Базовый список записей; берутся только записи выбранных СЦ и не отменённые.

- `appointment_employee.json`  
  Связь запись -> сотрудник, используется для занятости сотрудников.

- `appointment_working_zone.json`  
  Связь запись -> ворк-зона, используется для занятости зон.

- `employees.json`  
  Сотрудники выбранных СЦ (только `is_available = true`, `is_fired = false`).

- `working_zones.json`  
  Ворк-зоны выбранных СЦ (только `is_available = true`).

- `resource_types.json`  
  Справочник типов ресурсов (parent/child), нужен для категоризации и подписей.

- `employee_resource_types.json`  
  Связь сотрудник -> тип ресурса.

### 1.2. Таблицы расписания/доступности сотрудников

- `cyclical_schedules.json`  
  Циклические графики сотрудников (`work_days/day_offs`, смена, период действия).

- `cyclical_breaks.json`  
  Перерывы в рамках графиков (обеды/паузы), вычитаются из мощности.

### 1.3. Таблицы «выходных СЦ»

- `sc_off_days.json`
- `off_days.json`
- `service_center_holidays.json`
- `hollidays.json` (legacy fallback)

Важно: в расчёт «выходных СЦ» попадают **только строки с явным `service_center_uid`/`sc_uid`**, совпадающим с выбранным СЦ.

---

## 2) Какие фильтры применяются

Из query-параметров:

- `sc_uid` — конкретный СЦ или все СЦ
- `date_from`, `date_to` — период (ограничен `MAX_DAYS = 31`)
- `slot_minutes` — шаг слота (`60` или `30`)
- `resource_kind` — `all` / `employee` / `working_zone`
- `category` — `all` / `mechanic` / `washer`

Категория считается не по имени сотрудника/зоны, а по типу ресурса:

- `washer`: если в parent/child есть `мойк`
- `mechanic`: если есть `механ`, `ремонт`, `подъ`, `пост`
- иначе `other`

---

## 3) Как формируется базовая выборка записей

1. Из `appointments.json` выбираются записи:
   - `service_center_uid in sc_set`
   - запись не отменена (`status not in cancelled/canceled/отменен...` и `is_deleted != true`)

2. Из таблиц связей берутся только строки, где `appointment_uid` есть в выбранных записях:
   - `appointment_employee`
   - `appointment_working_zone`

Это общий источник и для slot-модели, и для KPI/таблиц ниже.

---

## 4) Как строится slot-модель (timeseries)

Ключевая функция: `_compute_load_timeseries(...)`.

### 4.1. Сетка слотов

Слоты строятся от `date_from 09:00` до `date_to 20:00` c шагом `slot_minutes`.

### 4.2. Busy (занятость) по слоту

По каждой связи запись-ресурс считается пересечение интервала записи со слотом:

- для сотрудников: из `appointment_employee`
- для ворк-зон: из `appointment_working_zone`

Минуты суммируются:

- в общий `busy_by_slot`
- и отдельно по каждому ресурсу (`employee_busy_by_slot`, `wz_busy_by_slot`)

### 4.3. Capacity (мощность) по слоту

#### Сотрудники

Для каждого сотрудника и слота:

1. Если день в выходных СЦ -> `0`.
2. Иначе ищется активный график на дату (`cyclical_schedules`):
   - при пересечениях берётся график с **максимальным `start_date`**.
3. Если график найден и день рабочий по циклу:
   - мощность = пересечение смены со слотом
   - минус пересечения перерывов (`cyclical_breaks`) со слотом
4. Если график найден, но день нерабочий по циклу -> `0`.
5. Если у сотрудника есть графики, но ни один не покрывает дату -> `0`.
6. Если у сотрудника вообще нет графиков -> fallback по окну дня СЦ (`09:00-20:00`).

#### Ворк-зоны

Для каждой ворк-зоны и слота:

1. Если день в выходных СЦ -> `0`
2. Иначе мощность = пересечение слота с рабочим окном `09:00-20:00`.

### 4.4. Метрики слота

Для каждого слота:

- `busy_minutes`
- `capacity_minutes`
- `free_minutes = max(0, capacity - busy)`
- `deficit_minutes = max(0, busy - capacity)`
- `load_pct = busy / capacity * 100` (если capacity > 0)

---

## 5) KPI «окна записи >=2ч/>=4ч подряд»

Текущая логика:

- считается **по каждому ресурсу отдельно** (сотрудник/ворк-зона),
- внутри дня без перекрытия окон (greedy non-overlap),
- для окна длительности `need_minutes` требуется `ceil(need_minutes / slot_minutes)` подряд слотов,
- в каждом слоте окна должно быть `free_minutes >= slot_minutes`.

После этого результаты суммируются по ресурсам:

- `free_slots_over_2h`
- `free_slots_over_4h`

Это оценка «сколько записей помещается» в пуле ресурсов.

---

## 6) Как собираются KPI верхнего блока

Из `selected_employee_rows` и/или `selected_wz_rows` (в зависимости от `resource_kind`) считаются:

- `total_resources`
- `engaged_resources`
- `busy_hours_total`
- `available_hours_total`
- `utilization_period_pct`
- `avg_load_pct`
- `active_avg_load_pct`
- `peak_load_pct` (по slot timeseries)
- `deficit_hours_total`
- `deficit_slots_pct`
- окна `>=2ч` / `>=4ч`

---

## 7) Как считается «Бронирований всего» и средняя длительность

Логика переведена на **уникальные бронирования (`appointment_uid`)**.

### 7.1. Минуты бронирования

Для каждого `appointment_uid` считается пересечение его интервала с выбранным периодом:

- `booking_minutes_by_uid[appointment_uid] = overlap_minutes`

### 7.2. Множества UID по типу ресурсов

- `employee_appointment_uids` — уникальные UID, которые связаны с выбранными сотрудниками
- `wz_appointment_uids` — уникальные UID, связанные с выбранными ворк-зонами
- итоговое множество зависит от `resource_kind`:
  - `employee`: только employee UID
  - `working_zone`: только wz UID
  - `all`: объединение множеств

### 7.3. Показатели

- `appointments_total` = количество уникальных UID в итоговом множестве
- `avg_booking_minutes_overall` = сумма минут этих UID / их количество
- `avg_booking_minutes_employee` и `avg_booking_minutes_wz` — аналогично по соответствующим множествам

---

## 8) Дневной график, heatmap, прогноз

### 8.1. Спрос vs Мощность (по дням)

Из `timeseries` агрегируется по дню:

- `busy`
- `capacity`
- `deficit`
- `utilization_pct`

### 8.2. Heatmap дефицита

Из `timeseries` агрегируется средний `deficit_minutes` по ключу:

- `(weekday, hour)`

### 8.3. Прогноз по механикам

Независим от дат формы:

- якорь: `today`
- окна: `today..+3`, `today..+7` (включительно)
- ресурс: только `employee`
- категория: `mechanic`
- СЦ: как в выбранном фильтре

Для каждого окна вызывается `_compute_load_timeseries` и берутся:

- `utilization_pct`
- `busy_hours`
- `available_hours`
- `appointments`
- окна `>=2ч`, `>=4ч`
- `deficit_hours`

---

## 9) Важные нюансы модели

- Модель работает на уровне минут пересечения интервалов, а не «целых записей по календарю».
- Для сотрудников без графиков есть fallback `09:00-20:00`.
- Для сотрудников с графиком, но вне рабочего дня — мощность `0`.
- Выходные СЦ учитываются отдельно и обнуляют мощность.
- Окна 2/4 часа считаются **по ресурсам и без перекрытия внутри дня**, затем суммируются.

---

## 10) Где смотреть код

- Основная логика:
  - `admin-panel-flask/app/modules/analytics/routes.py`
- Отрисовка страницы:
  - `admin-panel-flask/app/templates/analytics/resource_load.html`

