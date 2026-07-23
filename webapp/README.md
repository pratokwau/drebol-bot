# Drebolbot Web

Веб-панель работает поверх тех же файлов данных, что и бот:

- `data/funpayacc.db` — Golden Key и User-Agent.
- `data/ordersfp.db` — себестоимость заказов и журнал SaveProfit.

## Запуск вручную

```bash
cd /root/drebolbot
source .venv/bin/activate
export WEB_USERNAME=admin
export WEB_PASSWORD='your-strong-password'
uvicorn webapp.app:app --host 127.0.0.1 --port 8080
```

## Nginx для work.drebol.ru

```nginx
server {
    listen 80;
    server_name work.drebol.ru;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

После certbot конфиг сам добавит HTTPS-блок или обновит этот server.
