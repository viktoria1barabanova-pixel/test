# Support portal (MVP + Bitrix integration)

Портал техподдержки клиентов, готовый для дальнейшей связки с Telegram-ботом.

## Что реализовано

- Вход по номеру телефона (MVP-заглушка OTP: `0000`)
- Создание заявки с полями:
  - критичность
  - тег
  - отдел
  - название задачи
  - описание
- Просмотр списка заявок и их статусов
- Карточка заявки с диалогом клиент ↔ менеджер
- API для менеджера:
  - `POST /manager/tickets/<id>/comment`
  - `POST /manager/tickets/<id>/status`
- Интеграция с Bitrix24:
  - при создании заявки создаётся лид (`crm.lead.add`)
  - комментарий клиента уходит в Bitrix timeline (`crm.timeline.comment.add`)
  - смена статуса в локальном API пробрасывается в лид (`crm.lead.update`)
  - входящий webhook из Bitrix может создать комментарий или поменять статус заявки
- Аналитика:
  - среднее время первичного ответа
  - среднее время решения
  - количество заявок по тегам и отделам
  - средняя оценка

## Быстрый запуск

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -c "from app import init_db; init_db()"
python app.py
```

## Переменные окружения

- `SECRET_KEY` — ключ Flask-сессий
- `BITRIX_WEBHOOK_URL` — базовый URL входящего webhook Bitrix24 без метода,
  например: `https://your-bitrix/rest/1/abc123`
- `BITRIX_INBOUND_KEY` — ключ для входящего endpoint от Bitrix в этот портал
- `MANAGER_API_KEY` — API-ключ менеджерского API

## Как работает Bitrix интеграция

### 1) Создание заявки

При создании заявки вызывается:

- `crm.lead.add`

В локальной базе сохраняются:
- `bitrix_entity_type=LEAD`
- `bitrix_entity_id=<ID лида>`

### 2) Комментарий клиента

Комментарий из портала отправляется в Bitrix через:

- `crm.timeline.comment.add`

### 3) Смена статуса

Смена статуса через локальный менеджерский API отправляется в Bitrix:

- `crm.lead.update` (поле `STATUS_DESCRIPTION`)

### 4) Входящий webhook из Bitrix в портал

Endpoint:

- `POST /integrations/bitrix/inbound`
- Header: `X-Bitrix-Key: <BITRIX_INBOUND_KEY>`

Тело запроса:

```json
{
  "action": "comment",
  "local_ticket_id": 12,
  "author": "Ирина",
  "text": "Уточните номер договора"
}
```

или

```json
{
  "action": "status",
  "bitrix_entity_id": 215,
  "status": "В работе"
}
```

## Примеры запросов

```bash
curl -X POST http://127.0.0.1:5000/manager/tickets/1/comment \
  -H 'Content-Type: application/json' \
  -H 'X-Manager-Key: manager-demo-key' \
  -d '{"author":"Ирина","text":"Уточните, пожалуйста, номер договора"}'
```

```bash
curl -X POST http://127.0.0.1:5000/integrations/bitrix/inbound \
  -H 'Content-Type: application/json' \
  -H 'X-Bitrix-Key: bitrix-demo-key' \
  -d '{"action":"status","local_ticket_id":1,"status":"Решена"}'
```

## Что добавить следующим шагом

1. Реальную OTP-авторизацию через SMS
2. Маппинг статусов в воронку Bitrix (`STATUS_ID`) под ваш pipeline
3. Привязку к открытым линиям и двустороннюю синхронизацию сообщений без промежуточного API
4. Уведомления в Telegram для клиента о новых комментариях
5. SLA-эскалации и дашборды по менеджерам
