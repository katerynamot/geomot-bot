import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Request, HTTPException
from aiogram import Bot, Dispatcher, types, Router, F
from aiogram.filters import CommandStart
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

# ===== Настройки из переменных окружения =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")           # зададим в Render
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret") # уникальная строка для пути вебхука
BASE_URL = (os.getenv("RENDER_EXTERNAL_URL") or        # Render сам проставит
            os.getenv("WEBHOOK_URL"))                  # если локально тестируешь — укажи сама
PROZORRO_API_BASE = os.getenv("PROZORRO_API_BASE", "https://public.api.openprocurement.org/api/2.5")

# ===== Кнопки меню (можно менять) =====
BUTTONS = [
    "геотекстиль",
    "бентоніт",
    "георешітка",
    "геомембрана",
    "геосітка",
    "Протирадіаційного укриття нове будівництво",
    "Будівництво захисної споруди",
]

# ===== Инициализация бота/приложения =====
if not TELEGRAM_TOKEN:
    print("⚠️ TELEGRAM_TOKEN не задан. Задай его в переменных окружения на Render.")

bot = Bot(token=TELEGRAM_TOKEN) if TELEGRAM_TOKEN else None
dp = Dispatcher()
router = Router()
dp.include_router(router)
app = FastAPI()

# ===== Клавиатура =====
def make_keyboard() -> ReplyKeyboardMarkup:
    rows, row = [], []
    for text in BUTTONS:
        row.append(KeyboardButton(text=text))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

# ===== Хэндлеры сообщений =====
@router.message(CommandStart())
async def start_handler(message: types.Message):
    await message.answer(
        "Привіт! Я шукаю тендери в Prozorro за останні 24 години по вибраним ключовим словам.\n"
        "Натисніть кнопку або напишіть слово.",
        reply_markup=make_keyboard()
    )

@router.message(F.text.in_(BUTTONS))
async def keyword_handler(message: types.Message):
    kw = message.text.strip()
    tenders = await get_tenders_for_keyword(kw)
    if not tenders:
        await message.answer(f"За останні 24 години по запиту «{kw}» тендерів не знайдено.")
        return

    chunks = format_tenders_message(kw, tenders)
    for part in chunks:
        await message.answer(part, disable_web_page_preview=True)

# Фоллбек: если пользователь написал произвольный текст — ищем по нему
@router.message(F.text)
async def any_text_handler(message: types.Message):
    kw = message.text.strip()
    if not kw:
        return
    tenders = await get_tenders_for_keyword(kw)
    if not tenders:
        await message.answer(f"За останні 24 години по запиту «{kw}» тендерів не знайдено.")
        return
    chunks = format_tenders_message(kw, tenders)
    for part in chunks:
        await message.answer(part, disable_web_page_preview=True)

# ===== Вебхук и сервисные маршруты =====
@app.get("/")
async def root():
    return {"status": "ok"}

@app.post("/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="forbidden")
    data = await request.json()
    update = types.Update(**data)
    await dp.feed_update(bot, update)
    return {"ok": True}

@app.on_event("startup")
async def on_startup():
    if not (bot and BASE_URL):
        print("⚠️ Не могу установить вебхук: нет токена или BASE_URL.")
        return
    url = f"{BASE_URL.rstrip('/')}/webhook/{WEBHOOK_SECRET}"
    try:
        await bot.set_webhook(url)
        print(f"✅ Webhook set to {url}")
    except Exception as e:
        print(f"❌ set_webhook failed: {e}")

@app.on_event("shutdown")
async def on_shutdown():
    if bot:
        await bot.delete_webhook()

# ===== Логика: запросы к Prozorro =====
async def fetch_tenders_since(cutoff_dt_utc: datetime):
    """
    Забираем изменения тендеров, начиная с самых свежих, и останавливаемся,
    когда дошли до записей старше cutoff.
    """
    results = []
    offset = None
    params_base = {
        "opt_fields": "id,tenderID,title,description,dateModified,procuringEntity",
        "limit": 100,
        "descending": 1,  # берём свежие первыми, чтобы быстро остановиться
    }
    MAX_PAGES = 50  # предохранитель

    async with httpx.AsyncClient(timeout=20) as client:
        for _ in range(MAX_PAGES):
            params = dict(params_base)
            if offset:
                params["offset"] = offset

            r = await client.get(f"{PROZORRO_API_BASE}/tenders", params=params)
            r.raise_for_status()
            payload = r.json()
            data = payload.get("data", [])
            if not data:
                break

            stop = False
            for t in data:
                dm = t.get("dateModified")
                if not dm:
                    continue
                dm_dt = datetime.fromisoformat(dm.replace("Z", "+00:00"))
                if dm_dt < cutoff_dt_utc:
                    stop = True
                    break
                results.append(t)

            if stop:
                break

            offset = payload.get("next_page", {}).get("offset")
            if not offset:
                break

    return results

async def get_tenders_for_keyword(keyword: str):
    """
    Фильтруем тендеры за 24 часа по вхождению слова в title/description (без учёта регистра).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    all_recent = await fetch_tenders_since(cutoff)

    kw = keyword.lower()
    matched = []
    for t in all_recent:
        title = (t.get("title") or "").lower()
        descr = (t.get("description") or "").lower()
        if kw in title or kw in descr:
            matched.append(t)

    matched.sort(key=lambda x: x.get("dateModified", ""), reverse=True)
    return matched

def format_tenders_message(kw: str, tenders: list, limit: int = 10):
    """
    Формируем текст, нарезая на части до ~3800 символов под лимит Telegram.
    """
    kyiv = ZoneInfo("Europe/Kyiv")
    header = f"🧾 Тендери за 24 години по запиту «{kw}»: показую {min(len(tenders), limit)} з {len(tenders)}\n\n"

    blocks = [header]
    for t in tenders[:limit]:
        dm = t.get("dateModified")
        dm_dt = datetime.fromisoformat(dm.replace("Z", "+00:00")).astimezone(kyiv) if dm else None
        tender_id = t.get("tenderID")
        portal_link = f"https://prozorro.gov.ua/tender/{tender_id}" if tender_id else f"{PROZORRO_API_BASE}/tenders/{t.get('id')}"
        title = t.get("title") or "Без назви"
        org = (t.get("procuringEntity") or {}).get("name") or "—"
        when = dm_dt.strftime("%Y-%m-%d %H:%M") if dm_dt else "—"

        block = (
            f"• {title}\n"
            f"Замовник: {org}\n"
            f"Змінено: {when} (Kyiv)\n"
            f"Посилання: {portal_link}\n\n"
        )
        blocks.append(block)

    chunks, buf = [], ""
    for b in blocks:
        if len(buf) + len(b) > 3800:
            chunks.append(buf)
            buf = b
        else:
            buf += b
    if buf:
        chunks.append(buf)
    return chunks
