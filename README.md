# Drebolbot Web

Панель управления FunPay автоматизацией. Веб-интерфейс для управления заказами, ценами, демпингом и прибылью.

Репозиторий: https://github.com/pratokwau/drebol-bot.git

## Установка с нуля

### 1. Подготовка сервера

Нужен сервер Ubuntu 22.04+ с root-доступом и доменом, направленным на IP сервера (A-запись).

```bash
apt update && apt upgrade -y
apt install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx git
```

### 2. Клонировать репозиторий

```bash
git clone https://github.com/pratokwau/drebol-bot.git /root/drebolbot
cd /root/drebolbot
```

### 3. Создать виртуальное окружение и зависимости

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 4. Настроить переменные окружения

```bash
cat > .env << 'EOF'
WEB_USERNAME=admin
WEB_PASSWORD=ваш_секретный_пароль
OPENROUTER_API_KEY=sk-or-...
EOF
```

- `WEB_USERNAME` — логин для входа на сайт
- `WEB_PASSWORD` — пароль для входа
- `OPENROUTER_API_KEY` — ключ OpenRouter для AI-распознавания фото (опционально)

### 5. Создать папку данных

```bash
mkdir -p data
```

### 6. Проверить запуск

```bash
source .venv/bin/activate
.venv/bin/python -m uvicorn webapp.app:app --host 127.0.0.1 --port 8090
```

Если запустилось без ошибок — Ctrl+C и дальше.

### 7. Настроить systemd-сервис

```bash
cat > /etc/systemd/system/drebolbot.service << 'EOF'
[Unit]
Description=Drebolbot Web
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/drebolbot
ExecStart=/root/drebolbot/.venv/bin/python -m uvicorn webapp.app:app --host 127.0.0.1 --port 8090
Restart=always
RestartSec=5
EnvironmentFile=/root/drebolbot/.env

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable drebolbot
systemctl start drebolbot
systemctl status drebolbot
```

### 8. Настроить nginx

```bash
cat > /etc/nginx/sites-available/drebolbot << 'EOF'
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

ln -sf /etc/nginx/sites-available/drebolbot /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
```

### 9. Получить SSL-сертификат (HTTPS)

```bash
certbot --nginx -d ваш-домен.ru --non-interactive --agree-tos --email ваш@email.ru
```

Certbot автоматически:
- Получит сертификат от Let's Encrypt
- Обновит nginx-конфиг для HTTPS
- Настроит автоматическое продление

Проверить:
```bash
certbot certificates
```

### 10. Открыть в браузере

```
https://ваш-домен.ru
```

Логин и пароль из `.env`.

---

## Автозапуск при перезагрузке сервера

```bash
systemctl enable drebolbot
```

Уже сделано на шаге 7.

## Обновление

```bash
cd /root/drebolbot
git pull
systemctl restart drebolbot
```

## Просмотр логов

```bash
journalctl -u drebolbot -f
```

## Структура проекта

```
drebolbot/
├── webapp/
│   ├── app.py              # FastAPI приложение (роуты)
│   ├── static/app.css      # Стили (тёмная тема)
│   └── templates/          # HTML шаблоны
│       ├── base.html       # Базовый шаблон (sidebar)
│       ├── login.html      # Страница входа
│       ├── dashboard.html  # Дашборд
│       ├── orders.html     # Заказы
│       ├── tasks.html      # Хвосты
│       ├── calc.html       # Калькуляторы
│       ├── minprice.html   # Минпрайс (игры)
│       ├── minprice_game.html  # Минпрайс (товары)
│       ├── demping.html    # Демпинг
│       ├── certs.html      # Сертификаты (игры)
│       ├── certs_game.html # Сертификаты (товары)
│       ├── profits.html    # Журнал прибыли
│       └── settings.html   # Настройки
├── handlers/
│   ├── funpay_admin.py     # FunPay API
│   ├── minprice.py         # Минимальные цены
│   ├── demping.py          # Демпинг Cardinal
│   ├── certificates.py     # Сертификаты
│   ├── settings.py         # Настройки бота
│   ├── utils.py            # Утилиты
│   ├── ai_runtime.py       # AI runtime
│   ├── ai_settings.py      # AI настройки
│   └── inventory.py        # Инвентарь
├── database.py             # База данных
├── config.py               # Конфигурация
├── base_store.py           # Хранилище
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

## Возможности

- **Дашборд** — статистика прибыли, быстрые переходы
- **Заказы** — карточки FunPay, поиск по ID/ссылке, ввод себестоимости, автоподбор
- **Хвосты** — незаполненные заказы по периодам
- **Расчёт** — калькуляторы прибыли FunPay и PlayerOK
- **Минпрайс** — управление играми, товарами, ставками СБП
- **Демпинг** — файл price_optimizer_lots.json, отправка в Cardinal
- **Сертификаты** — управление сертификатами, отправка в Cardinal
- **Прибыль** — журнал с фильтрацией по периодам (день/неделя/месяц/всё)
- **Настройки** — параметры уведомлений, админ-отчёт, управление сессиями
