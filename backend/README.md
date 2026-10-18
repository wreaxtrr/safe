# Сейф — бэкенд

FastAPI + PostgreSQL. Сделки, резерв денег, подтверждение кодом, отказ, спор и автовозврат.

## Запуск

```
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Скопировать `.env.example` в `.env` и вписать пароль от базы и любой ADMIN_KEY.

```
uvicorn app.main:app --reload
```

Документация и проверка API: http://127.0.0.1:8000/docs

## Файлы

- `app/models.py` — таблицы: users, deals, events
- `app/logic.py` — правила сделки
- `app/main.py` — команды API
- `app/auth.py` — вход по токену и ключ администратора
- `app/schemas.py` — формат запросов и ответов
- `tests/` — тесты сценариев (`pytest`)

## Статусы сделки

created → reserved → done, а также refusal, dispute, refunded, cancelled.
