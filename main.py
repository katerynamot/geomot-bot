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

# ===== Кнопки меню =====
BUTTONS = [
    "геотекстиль",
    "бентоніт",
    "георешітка",
    "геомембрана",
    "геосітка",
    "Протирадіаційного укриття нове будівництво",
    "Будівництво захисної споруди",
]

# ===== Инициализация =====
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

# ===== Старт =====
@router.message(CommandStart())
async def start_handler(message: types.Message):
    await message.answer(
        "Привіт! Я шукаю тендери в Prozorro за останні 24 години по вибраним ключовим словам.\n"
        "Натисніть кнопку або напишіть слово.\n\n"
        "Також доступні команди: /geotextile, /geomembrane, /geogrid, /geonet, /bentonite",
        reply_markup=make_keyboard()
    )

# ===== Команды-справка =====
@router.message(F.text == "/geotextile")
async def geotextile_cmd(message: types.Message):
    await message.answer(
        "🧵 Геотекстиль — це нетканий синтетичний матеріал, який використовується для:\n"
        "• розділення шарів ґрунту;\n"
        "• армування;\n"
        "• дренажу;\n"
        "• захисту від ерозії.\n\n"
        "Він широко застосовується у дорожньому будівництві, гідротехнічних спорудах та фільтраційних системах."
    )

@router.message(F.text == "/geomembrane")
async def geomembrane_cmd(message: types.Message):
    await message.answer(
        "📑 Геомембрана — це водонепроникний синтетичний матеріал у вигляді гнучких листів.\n"
        "Основні сфери застосування:\n"
        "• ізоляція водойм, дамб і каналів;\n"
        "• захист від просочування рідин;\n"
        "• полігони твердих побутових відходів;\n"
        "• захист від радіації та хімічних забруднень."
    )

@router.message(F.text == "/geogrid")
async def geogrid_cmd(message: types.Message):
    await message.answer(
        "🪢 Георешітка — це полімерний матеріал із сітчастою структурою.\n"
        "Використовується для:\n"
        "• армування основ доріг та укосів;\n"
        "• стабілізації ґрунтів;\n"
        "• запобігання розповзанню шарів.\n\n"
        "Особливо ефективна при будівництві на слабких ґрунтах."
    )

@router.message(F.text == "/geonet")
async def geonet_cmd(message: types.Message):
    await message.answer(
        "🕸 Геосітка — це сітчастий матеріал із полімерів або скловолокна.\n"
        "Призначення:\n"
        "• армування асфальтового покриття;\n"
        "• зменшення тріщиноутворення;\n"
        "• зміцнення укосів і дамб."
    )

@router.message(F.text == "/bentonite")
async def bentonite_cmd(message: types.Message):
    await message.answer(
        "🪨 Бентоніт — це природна глина з високим вмістом монтморилоніту.\n"
        "Властивості:\n"
        "• набухає у воді, утворюючи водонепроникний шар;\n"
        "• застосовується у гідроізоляції споруд;\n"
        "• використовується у бентонітових матах для фундаментів та сховищ."
    )

# ===== Поиск по кнопкам =====
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

# ===== Фоллбек (любой текст) =====
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

# ===== Вебхук =====
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

# ===== Логика Prozorro =====
async def fetch_tenders_since(cutoff_dt_utc: datetime):
    results = []
    offset = None
    params_base = {
        "opt_fields": "id,tenderID,title,description,dateModified,procuringEntity",
        "limit": 100,
        "descending": 1,
    }
    MAX_PAGES = 50

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
