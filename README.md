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
| **Настройки** | Ежедневный отчёт, сессии, аккаунты, бэкап базы данных |
| **Уведомления** | Автоматические отчёты, мониторинг СБП-ставок, контроль хвостов |

## Установка

Один скрипт ставит всё: Telegram-бота, веб-панель, Nginx и SSL-сертификат. Нужен чистый сервер Ubuntu 22.04+ с root-доступом и домен, A-запись которого уже указывает на IP сервера.

```bash
curl -fsSL https://raw.githubusercontent.com/pratokwau/drebol-bot/main/install.sh | bash
```

Или в два шага:

```bash
git clone https://github.com/pratokwau/drebol-bot.git /root/drebol-bot
bash /root/drebol-bot/install.sh
```

Скрипт спросит по порядку:

1. **Домен сайта** — например `panel.example.com`
2. **Логин и пароль веб-панели** — пароль можно не вводить, тогда сгенерируется автоматически
3. **Email для SSL** — по умолчанию `admin@домен`
4. **Токен Telegram-бота** (от [@BotFather](https://t.me/BotFather)) и твой **Telegram ID**

Дальше — без вопросов: ставит системные пакеты, клонирует и настраивает проект, поднимает venv и зависимости, создаёт два systemd-сервиса (`drebol-bot` — телеграм-бот, `drebol-web` — веб-панель), настраивает Nginx под домен и получает SSL-сертификат Let's Encrypt. В конце выводит адрес сайта и логин/пароль.

### Обслуживание

```bash
# Обновление (код + рестарт обоих сервисов)
cd /root/drebol-bot && git pull && systemctl restart drebol-bot drebol-web
# То же самое можно сделать кнопкой "Обновить с GitHub" в Настройках сайта

# Логи
journalctl -u drebol-bot -f      # телеграм-бот
journalctl -u drebol-web -f      # веб-панель

# Статус
systemctl status drebol-bot drebol-web
```

### Резервное копирование

В **Настройки → База данных** можно скачать zip-архив со всеми данными (заказы, цены, сертификаты, демпинг, уведомления, прибыль, сессии) и загрузить его обратно — например при переезде на другой сервер. После импорта сервисы перезапускаются автоматически.

### Повторная настройка

Изменить домен, логин, пароль или токен бота без переустановки:

```bash
cd /root/drebol-bot
python3 install.py --domain новый-домен.ru --web-username admin --web-password новый_пароль --token НОВЫЙ_ТОКЕН --admin-id ID
systemctl restart drebol-bot drebol-web
```
Аргументы можно опустить — тогда скрипт спросит их в терминале.

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
├── install.sh                  # Установщик: бот + веб + Nginx + SSL
├── install.py                  # Пишет .env, вызывается из install.sh
├── requirements.txt
├── .env                        # Секреты (не в git)
└── data/                       # Данные (создаётся автоматически, бэкапится из Настроек)
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
