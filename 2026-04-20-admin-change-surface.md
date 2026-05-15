# Карта изменяемых сущностей в текущей админке

Документ фиксирует, какие сущности изменяются через текущую Flask-админку, на каких страницах это происходит и какими действиями (create/update/delete/disable).  
Цель: использовать как основу для внедрения истории изменений (audit trail).

## 1. Сервисные центры

### 1.1 Страницы
- `app/templates/service_centers/create.html`
- `app/templates/service_centers/detail.html`

### 1.2 Изменяемые сущности и действия
- **Service Center**
  - `POST /service_centers/create`
  - `POST /service_centers/<uid>/update`
- **Franchise link**
  - `POST /service_centers/<uid>/franchise/attach`
  - `POST /service_centers/<uid>/franchise/detach`
- **Local schedule**
  - `POST /service_centers/<uid>/schedule/create`
  - `POST /service_centers/<uid>/schedule/update`
  - `POST /service_centers/<uid>/schedule/delete`
- **Day off**
  - `POST /service_centers/<uid>/day_off/create`
  - `POST /service_centers/<uid>/day_off/<off_day_uid>/delete`
- **Working zone**
  - `POST /service_centers/<uid>/working_zones/create`
  - `POST /service_centers/<uid>/working_zones/<wz_uid>/delete`
- **Department inside SC**
  - `POST /service_centers/<uid>/departments/create`
  - `POST /service_centers/<uid>/departments/<department_uid>/patch`
  - `POST /service_centers/<uid>/departments/<department_uid>/delete`
- **Employee (internal)**
  - `POST /service_centers/<uid>/employee/create`
- **External employee (SC context)**
  - `POST /service_centers/<uid>/external_employee/create`
  - `POST /service_centers/<uid>/external_employee/<employee_uid>/patch`
  - `POST /service_centers/<uid>/external_employee/<employee_uid>/disable`
- **Equipment**
  - `POST /service_centers/<uid>/equipment/create`
  - `POST /service_centers/<uid>/equipment/<eq_uid>/patch`
  - `POST /service_centers/<uid>/equipment/<eq_uid>/delete`
- **Brand restrictions**
  - `POST /service_centers/<uid>/brand_restrictions/create`
  - `POST /service_centers/<uid>/brand_restrictions/<restriction_uid>/delete`

## 2. Склады и межскладские перемещения

### 2.1 Страницы
- `app/templates/warehouses/new.html`
- `app/templates/warehouses/detail.html`
- `app/templates/warehouses/delivery_routes.html`

### 2.2 Изменяемые сущности и действия
- **Warehouse**
  - `POST /warehouses/new`
  - `POST /warehouses/<uid>/patch`
  - `POST /warehouses/<uid>/delete`
- **Warehouse external system IDs** (`ROSSKO`, `BERG`, `1C`, `FORUM_AUTO`, `ALFA_AUTO`)
  - `POST /warehouses/<uid>/external_system/create`
  - `POST /warehouses/<uid>/external_system/patch`
- **Delivery route (межскладское перемещение)**
  - `POST /warehouses/delivery_routes/create`
  - `POST /warehouses/delivery_routes/update`
  - `POST /warehouses/delivery_routes/delete` (в текущей реализации это soft-disable)

## 3. Справочники

### 3.1 Страницы
- `app/templates/dictionaries/tech_cards.html`
- `app/templates/dictionaries/tech_cards_new.html`
- `app/templates/dictionaries/external_employees.html`
- `app/templates/dictionaries/replacement_types.html`
- `app/templates/dictionaries/global_schedule.html`
- `app/templates/dictionaries/resource_types.html`

### 3.2 Изменяемые сущности и действия
- **Tech cards**
  - `POST /dictionaries/tech_cards/create`
  - `POST /dictionaries/tech_cards/delete`
- **External employees (global dictionaries context)**
  - `POST /dictionaries/external_employees/create`
  - `POST /dictionaries/external_employees/<employee_uid>/patch`
  - `POST /dictionaries/external_employees/<employee_uid>/disable`
- **Replacement types**
  - `POST /dictionaries/replacement_types/create`
  - `POST /dictionaries/replacement_types/<uid>/toggle`
  - `POST /dictionaries/replacement_types/<uid>/delete`
- **Global schedule**
  - `POST /dictionaries/global_schedule/create`
  - `POST /dictionaries/global_schedule/<uid>/patch`
  - `POST /dictionaries/global_schedule/<uid>/delete` (поддержка зависит от API)
- **Global holidays**
  - `POST /dictionaries/global_schedule/holiday/create`
- **Resource types**
  - `POST /dictionaries/resource_types/create`

## 4. Франчайзи

### 4.1 Страницы
- `app/templates/franchisees/new.html`
- `app/templates/franchisees/detail.html`

### 4.2 Изменяемые сущности и действия
- **Franchisee**
  - `POST /franchisees/new`
  - `POST /franchisees/<uid>/update`
  - `POST /franchisees/<uid>/delete`
- **Binding SCs to franchisee**
  - `POST /franchisees/<uid>/attach_service_centers`

## 5. Отделы (глобальный модуль)

### 5.1 Страницы
- `app/templates/departments/new.html`
- `app/templates/departments/edit.html`
- `app/templates/departments/list.html`

### 5.2 Изменяемые сущности и действия
- **Department**
  - `POST /departments/new`
  - `POST /departments/<uid>/edit`
  - `POST /departments/<uid>/delete`

## 6. Записи на обслуживание

### 6.1 Страница
- `app/templates/appointments/detail.html`

### 6.2 Изменяемая сущность и действие
- **Appointment**
  - `POST /appointments/<uid>/cancel`

## 7. Read-only зоны (без мутаций через UI)

- `app/templates/analytics/resource_load.html` (аналитика)
- `app/templates/warehouses/provider_map.html` (просмотр карты провайдеров)
- листинги с фильтрами (`service_centers/list`, `warehouses/list`, `appointments/list`, и т.д.)

## 8. Рекомендации для истории изменений (audit trail)

Минимально необходимое покрытие аудита:
- все `POST` маршруты из разделов 1-6;
- отдельно высокий приоритет:
  - `service_centers/*` (расписания, выходные, бренд-ограничения, оборудование, сотрудники),
  - `warehouses/*` и `delivery_routes/*`,
  - `dictionaries/*` (tech cards, global schedule/holidays, external employees, replacement types).

Минимальные поля audit-записи:
- `entity_type`, `entity_uid`, `action`;
- `before`, `after` (JSON);
- `changed_by_user_id` (или sso_id), `roles`;
- `source_ip`, `user_agent`;
- `endpoint`, `method`, `request_id`, `created_at`.

Такой реестр можно использовать как чек-лист: при включении аудита каждый mutating endpoint должен писать событие в единый журнал.
