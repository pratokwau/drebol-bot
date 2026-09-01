# Drebol-bot

Панель управления FunPay — веб-дашборд для автоматизации заказов, ценообразования, демпинга и учёта прибыли.

**Стек:** Python 3.12 · FastAPI · Jinja2 · SQLite · aiogram 3.x

## Возможности

| Раздел | Описание |
|--------|----------|
| **Дашборд** | Статистика прибыли за день / неделю / месяц, быстрые переходы |
| **Заказы** | Карточки FunPay с поиском по ID/ссылке, ввод себестоимости, AI-автоподбор цен |
| **Хвосты** | Незаполненные заказы по периодам — контроль "хвостов" |
| **Калькулятор** | Расчёт прибыли FunPay и PlayerOK с учётом комиссий |
| **Мин лоты** | Управление играми, товарами, привязка лотов, ставки СБП, AI-автолинк |
| **Демпинг** | Файл `price_optimizer_lots.json` для Cardinal, выборочная отправка по кэшбеку |
| **Сертификаты** | Подарочные сертификаты — цены, коэффициенты, интеграция с Cardinal |
| **Прибыль** | Журнал с фильтрацией, сортировкой, пагинацией |
| **API Ключи** | FunPay Golden Key, User-Agent, Groq, OpenRouter |
| **Настройки** | Ежедневный отчёт, управление сессиями и аккаунтами |
| **Уведомления** | Автоматические отчёты, мониторинг СБП-ставок, контроль хвостов |

## Установка

### 1. Подготовка сервера (Ubuntu 22.04+)

```bash
apt update && apt upgrade -y
apt install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx git
```

### 2. Клонировать и настроить

```bash
git clone https://github.com/pratokwau/drebol-bot.git /root/drebol-bot
cd /root/drebol-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
mkdir -p data
```

### 3. Переменные окружения

```bash
cat > .env << 'EOF'
WEB_USERNAME=admin
WEB_PASSWORD=ваш_секретный_пароль
OPENROUTER_API_KEY=sk-or-...
EOF
```

| Переменная | Описание |
|-----------|----------|
| `WEB_USERNAME` | Логин для входа на сайт |
| `WEB_PASSWORD` | Пароль для входа |
| `OPENROUTER_API_KEY` | Ключ OpenRouter для AI-сопоставления лотов (опционально) |

### 4. Проверить запуск

```bash
source .venv/bin/activate
python -m uvicorn webapp.app:app --host 127.0.0.1 --port 8090
```

### 5. Systemd-сервис

```bash
cat > /etc/systemd/system/drebol-bot.service << 'EOF'
[Unit]
Description=Drebolbot Web
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/drebol-bot
ExecStart=/root/drebol-bot/.venv/bin/python -m uvicorn webapp.app:app --host 127.0.0.1 --port 8090
Restart=always
RestartSec=5
EnvironmentFile=/root/drebol-bot/.env

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable drebol-bot
systemctl start drebol-bot
```

### 6. Nginx + SSL

```bash
cat > /etc/nginx/sites-available/drebol-bot << 'EOF'
server {
    server_name ваш-домен.ru;

    location / {
        proxy_pass http://127.0.0.1:8090;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

ln -sf /etc/nginx/sites-available/drebol-bot /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
certbot --nginx -d ваш-домен.ru --non-interactive --agree-tos --email ваш@email.ru
```

## Обслуживание

```bash
# Обновление
cd /root/drebol-bot && git pull && systemctl restart drebol-bot

# Логи
journalctl -u drebol-bot -f

# Статус
systemctl status drebol-bot
```

## Структура проекта

```
drebol-bot/
├── webapp/
│   ├── app.py                  # FastAPI — точка входа, фоновые задачи
│   ├── routers/                # Модульные роутеры
│   │   ├── shared.py           # Общие утилиты, аутентификация, шаблоны
│   │   ├── auth.py             # Вход / выход
│   │   ├── dashboard.py        # Главная страница
│   │   ├── orders.py           # Заказы FunPay
│   │   ├── profits.py          # Журнал прибыли
│   │   ├── minprice.py         # Минимальные цены и лоты
│   │   ├── demping.py          # Демпинг Cardinal
│   │   ├── certs.py            # Сертификаты
│   │   ├── keys.py             # API-ключи
│   │   ├── settings.py         # Настройки
│   │   ├── notifications.py    # Уведомления
│   │   └── tasks.py            # Хвосты (незаполненные заказы)
│   ├── static/
│   │   └── app.css             # Дизайн-система (тёмная тема, адаптив)
│   └── templates/              # Jinja2-шаблоны
│       ├── base.html           # Базовый layout с sidebar
│       ├── login.html
│       ├── dashboard.html
│       ├── orders.html
│       ├── profits.html
│       ├── minprice.html
│       ├── minprice_game.html
│       ├── minprice_import.html
│       ├── demping.html
│       ├── demping_selective.html
│       ├── certs.html
│       ├── certs_game.html
│       ├── certs_import.html
│       ├── calc.html
│       ├── tasks.html
│       ├── notifications.html
│       ├── keys.html
│       └── settings.html
├── handlers/
│   ├── funpay_admin.py         # FunPay API (скрейпинг, заказы)
│   ├── minprice.py             # Минимальные цены, СБП-ставки
│   ├── demping.py              # Демпинг — генерация файла Cardinal
│   ├── certificates.py         # Подарочные сертификаты
│   ├── settings.py             # Пользовательские настройки
│   ├── ai_runtime.py           # AI-сервис (Groq, OpenRouter)
│   ├── ai_settings.py          # AI-ключи
│   ├── inventory.py            # Инвентарь
│   └── utils.py                # Утилиты
├── FunPayAPI/                  # Обёртка FunPay API
├── database.py                 # SQLite базы данных
├── config.py                   # Конфигурация (ADMIN_ID)
├── base_store.py               # Хранилище файлов
├── requirements.txt
├── .env                        # Секреты (не в git)
└── data/                       # Данные (создаётся автоматически)
    ├── minprice.json
    ├── demping.json
    ├── demping_settings.json
    ├── certificates.json
    ├── certificates_demping.json
    ├── notifications.json
    ├── profits.json
    ├── ordersfp.db
    ├── webauth.db
    └── funpayacc.db
```

## Интеграции

- **FunPay** — заказы, профиль, лоты через Golden Key + скрейпинг
- **FunPayCardinal** — price_optimizer_lots.json для автодемпинга
- **СБП** — автоматическое обнаружение ставок кэшбека с FunPay
- **AI** — сопоставление лотов с товарами через Groq / OpenRouter
- **Telegram** — aiogram 3.x бот (отдельный модуль)

## Лицензия

Приватный проект.
