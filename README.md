# Flask Admin Skeleton for RMS

Стартовый каркас админ-панели на Flask по документам:
- `2026-04-13-flask-admin-panel-tz.md`
- `2026-04-13-admin-rbac-matrix.md`
- `2026-04-13-admin-screen-endpoint-map.md`

## Что уже есть

- Flask app factory (`app/__init__.py`)
- Blueprints: `auth`, `dashboard`, `franchisees`
- Базовый RBAC-декоратор (`app/rbac/decorators.py`)
- Обертка RMS API client (`app/rms_client/client.py`)
- Базовые Jinja-шаблоны

## Быстрый запуск

```bash
cd admin-panel-flask
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python run.py
```

После запуска:
- `http://localhost:5001/login`
- при `DEV_AUTH_STUB=true` нажать `Войти (dev stub)`;
- при `DEV_AUTH_STUB=false` произойдет redirect в Keycloak, затем callback обменяет `code` на токен через RMS `/api/v1/auth/code`.
- также доступен ручной вход: вставить access token в форму на `/login`.

## Переменные окружения

- `DEV_AUTH_STUB=true` - локальный вход без Keycloak (по умолчанию).
- `DEV_AUTH_STUB=false` - реальный auth-flow.
- `KEYCLOAK_SERVER_URL`, `KEYCLOAK_REALM`, `KEYCLOAK_CLIENT_ID`, `KEYCLOAK_REDIRECT_URI` - обязательны для реального flow.
- `RMS_API_BASE_URL` - RMS backend URL (для `auth/code` и бизнес-данных).
- `RMS_API_BASE_URL_DEV` и `RMS_API_BASE_URL_PROD` - серверные URL для быстрого переключения сред на `/login`.
- `RMS_API_TIMEOUT_SECONDS` - timeout запросов к RMS (рекомендовано `20` для серверных сред).

Рекомендуемые значения для текущих сред:
- `RMS_API_BASE_URL_DEV=https://rmsv2.dev.tech.o2.pro`
- `RMS_API_BASE_URL_PROD=https://rms2.prod.o2.pro/`

## Переключение DEV/PROD

На странице `/login` есть блок "Окружение RMS":
- выбрать `DEV` или `PROD` (из `.env`);
- либо выбрать `Custom URL` и указать адрес вручную;
- нажать "Применить окружение".

Выбранный URL сохраняется в сессии и используется всеми запросами `RMSClient`.

## Что уже подключено

1. Реальный Keycloak flow + обмен `code` через RMS `/api/v1/auth/code`.
2. Dashboard использует живые запросы к RMS (с безопасным fallback при ошибках).
3. Список `franchisees` загружается из RMS `/api/v1/franchisee`.
4. Список `service_centers` загружается из RMS `/api/v2/service_center` с фильтрами и cursor-next.
5. Детальная СЦ `/service_centers/<uid>` с вкладками: overview, schedule, employees, equipment, day_offs, slots.
6. На вкладках **Расписание** и **Выходные**: CRUD через RMS (`POST/PATCH/DELETE` для расписания, `POST/DELETE` для выходных). Право: `service_center.update`.
7. Модуль `appointments`: список с фильтрами, детали и отмена записи.
8. Модуль **Справочники** (`/dictionaries`): типы ресурсов, техкарты, бренды, рабочие зоны (read-only, с пагинацией где поддерживает API).
9. На вкладке **Сотрудники** СЦ: создание через `POST /api/v2/service_center/{uid}/employee`. **Оборудование**: быстрое обновление имени/доступности через `PATCH /api/v1/service_center/.../equipment/...`.

## Следующие шаги

1. Курсорная пагинация для выходных и записей в UI.
2. Полная матрица RBAC по ТЗ и audit trail.
3. Bulk-операции и аудит.
