# Drebolbot Web

Панель управления FunPay автоматизацией. Веб-интерфейс для управления заказами, ценами, демпингом и прибылью.

## Установка

### 1. Клонировать репозиторий

```bash
git clone <url> /root/drebolbot
cd /root/drebolbot
```

### 2. Создать виртуальное окружение

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Настроить переменные окружения

Создать файл `.env`:

```bash
WEB_USERNAME=admin
WEB_PASSWORD=ваш_пароль
OPENROUTER_API_KEY=sk-or-...       # для AI-распознавания фото (опционально)
```

### 4. Создать папку данных

```bash
mkdir -p data
```

### 5. Запустить

```bash
source .venv/bin/activate
nohup .venv/bin/python -m uvicorn webapp.app:app --host 127.0.0.1 --port 8090 > /tmp/drebolbot.log 2>&1 &
```

### 6. Настроить nginx

```nginx
server {
    server_name your.domain.com;

    location / {
        proxy_pass http://127.0.0.1:8090;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    listen 443 ssl;
    ssl_certificate /etc/letsencrypt/live/your.domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your.domain.com/privkey.pem;
}
```

### 7. Открыть в браузере

```
https://your.domain.com
```

Логин и пароль из `.env`.

## Структура проекта

```
drebolbot/
├── webapp/
│   ├── app.py              # FastAPI приложение
│   ├── static/app.css      # Стили (тёмная тема)
│   └── templates/          # HTML шаблоны
├── handlers/
│   ├── funpay_admin.py     # Работа с FunPay API
│   ├── minprice.py         # Минимальные цены
│   ├── demping.py          # Демпинг Cardinal
│   ├── certificates.py     # Сертификаты
│   ├── settings.py         # Настройки
│   └── ...
├── database.py             # База данных
├── config.py               # Конфигурация
├── .env                    # Переменные окружения
└── data/                   # Данные (создаётся автоматически)
    ├── minprice.json
    ├── demping.json
    ├── demping_settings.json
    ├── certificates.json
    ├── certificates_demping.json
    ├── ordersfp.db
    ├── webauth.db
    ├── funpayacc.db
    └── profits.json
```

## Обновление

```bash
cd /root/drebolbot
git pull
pkill -f uvicorn
source .venv/bin/activate
nohup .venv/bin/python -m uvicorn webapp.app:app --host 127.0.0.1 --port 8090 > /tmp/drebolbot.log 2>&1 &
```

## Возможности

- **Заказы** — карточки FunPay, поиск, ввод себестоимости, автоподбор
- **Хвосты** — незаполненные заказы
- **Расчёт** — калькуляторы прибыли FunPay и PlayerOK
- **Минпрайс** — управление играми, товарами, ставками СБП
- **Демпинг** — файл price_optimizer_lots.json, отправка в Cardinal
- **Сертификаты** — управление сертификатами, отправка в Cardinal
- **Прибыль** — журнал с фильтрацией по периодам
- **Настройки** — параметры бота, управление сессиями
