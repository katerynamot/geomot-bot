# Geomot Telegram Bot

Шукає тендери Prozorro за останні 24 години по заданим ключовим словам.  
Стек: Python, FastAPI, aiogram 3, вебхуки (Render).

## Локальный запуск (опционально)
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt

# Задай токен и URL вебхука (если тестируешь через ngrok/Cloudflare Tunnel):
export TELEGRAM_TOKEN=XXX
export WEBHOOK_URL=https://<твой-туннель>.ngrok.app

uvicorn main:app --host 0.0.0.0 --port 8000
