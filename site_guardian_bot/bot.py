"""Site Guardian — saytni kuzatuvchi va Claude orqali boshqaruvchi Telegram bot."""
import asyncio
import logging
import os
import uuid
from functools import wraps
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

import monitor
import tools
from agent import Agent
from config import CFG

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("site-guardian")

AGENT = Agent()
PENDING: dict[str, asyncio.Future] = {}   # tasdiq kutayotgan amallar
APPROVAL_TIMEOUT = 600                     # 10 daqiqada javob bo'lmasa — rad etilgan hisoblanadi
TG_LIMIT = 4000


def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or user.id not in CFG.admin_ids:
            log.warning("Ruxsatsiz urinish: %s (%s)", user.id if user else "?", user.username if user else "?")
            if update.callback_query:
                await update.callback_query.answer("Ruxsat yo'q", show_alert=True)
            elif update.effective_message:
                await update.effective_message.reply_text(f"⛔ Ruxsat yo'q. Sizning ID: {user.id if user else '?'}")
            return
        return await func(update, context)
    return wrapper


async def send_long(bot, chat_id: int, text: str, **kw) -> None:
    text = text or "(bo'sh)"
    for i in range(0, len(text), TG_LIMIT):
        await bot.send_message(chat_id, text[i:i + TG_LIMIT], **kw)


def make_callbacks(app: Application, chat_id: int):
    """Agent uchun Telegram bilan bog'langan funksiyalar."""

    async def approve(title: str, details: str) -> bool:
        if CFG.confirm_mode == "none":
            return True
        key = uuid.uuid4().hex[:12]
        fut = asyncio.get_running_loop().create_future()
        PENDING[key] = fut
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Ruxsat", callback_data=f"ok:{key}"),
            InlineKeyboardButton("❌ Rad etish", callback_data=f"no:{key}"),
        ]])
        await app.bot.send_message(chat_id, f"{title}\n\n{details[:3500]}", reply_markup=kb)
        try:
            return await asyncio.wait_for(fut, timeout=APPROVAL_TIMEOUT)
        except asyncio.TimeoutError:
            await app.bot.send_message(chat_id, "⌛ Javob bo'lmadi — amal rad etildi.")
            return False
        finally:
            PENDING.pop(key, None)

    async def notify(text: str) -> None:
        await send_long(app.bot, chat_id, text, disable_notification=True)
        await app.bot.send_chat_action(chat_id, ChatAction.TYPING)

    async def send_file(path: str, caption: str) -> str:
        p = Path(path).expanduser()
        if not p.is_file():
            return f"Xato: fayl topilmadi: {p}"
        if p.stat().st_size > 49 * 1024 * 1024:
            return "Xato: fayl 50 MB dan katta — Telegram orqali yuborib bo'lmaydi."
        with p.open("rb") as f:
            await app.bot.send_document(chat_id, f, filename=p.name, caption=caption[:1000] or None)
        return f"OK: {p.name} yuborildi"

    return approve, notify, send_file


async def run_agent(app: Application, chat_id: int, text: str) -> None:
    approve, notify, send_file = make_callbacks(app, chat_id)
    await app.bot.send_chat_action(chat_id, ChatAction.TYPING)
    try:
        answer = await AGENT.run(chat_id, text, approve, notify, send_file)
    except Exception as e:
        log.exception("Agent xatosi")
        answer = f"❗ Ichki xato: {type(e).__name__}: {e}"
    await send_long(app.bot, chat_id, answer)


# ---------------- Buyruqlar ----------------

HELP = f"""🤖 Site Guardian

Menga oddiy matn bilan yozing — Claude tahlil qilib, serverda kerakli ishlarni bajaradi.
Masalan:
• "Saytga aloqa formasi qo'sh, xabarlar emailga kelsin"
• "Nega sayt sekin ishlayapti? Tekshirib tuzat"
• "nginx loglarida xatolar bormi?"
• "Ma'lumotlar bazasidan zaxira olib menga yubor"

Buyruqlar:
/status — sayt va server holati
/run <buyruq> — terminal buyrug'ini to'g'ridan-to'g'ri bajarish
/ls [papka] — papka ichini ko'rish
/cat <fayl> — faylni o'qish
/logs [n] — log fayllarning oxirgi qatorlari
/monitor on|off — monitoringni yoqish/o'chirish
/diagnose — Claude saytni to'liq tekshirsin
/new — Claude bilan yangi suhbat (kontekstni tozalash)
/id — Telegram ID'ingiz

Fayl yuborsangiz — u {CFG.site_dir}/uploads/ ga saqlanadi.
Tasdiq rejimi: {CFG.confirm_mode}"""


@admin_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP)


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Sizning Telegram ID: {update.effective_user.id}")


@admin_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Tekshirilmoqda...")
    text = await tools.site_status()
    st = monitor.STATE
    text += (f"\n\n📡 Monitoring: {'yoqilgan' if st.enabled else 'o`chirilgan'}, har {CFG.check_interval}s"
             f"\nJami tekshiruv: {st.checks_total}, xatolar: {st.failures_total}")
    await msg.edit_text(text[:TG_LIMIT])


@admin_only
async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    command = update.message.text.partition(" ")[2].strip()
    if not command:
        await update.message.reply_text("Foydalanish: /run <buyruq>\nMasalan: /run systemctl status nginx")
        return
    chat_id = update.effective_chat.id

    async def job():
        approve, _, _ = make_callbacks(context.application, chat_id)
        if CFG.confirm_mode == "all" or (CFG.confirm_mode == "dangerous" and tools.is_dangerous_command(command)):
            if not await approve("⚠️ Xavfli buyruq. Bajarilsinmi?", command):
                await context.bot.send_message(chat_id, "❌ Bekor qilindi.")
                return
        out = await tools.run_command(command)
        await send_long(context.bot, chat_id, out)

    context.application.create_task(job())


@admin_only
async def cmd_ls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    path = " ".join(context.args) or CFG.site_dir
    await send_long(context.bot, update.effective_chat.id, tools.list_dir(path))


@admin_only
async def cmd_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Foydalanish: /cat <fayl yo'li>")
        return
    await send_long(context.bot, update.effective_chat.id, tools.read_file(" ".join(context.args), 1, 200))


@admin_only
async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    n = int(context.args[0]) if context.args and context.args[0].isdigit() else 30
    await send_long(context.bot, update.effective_chat.id, tools.tail_logs(n))


@admin_only
async def cmd_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    arg = (context.args[0].lower() if context.args else "")
    if arg in ("on", "off"):
        monitor.STATE.enabled = arg == "on"
    await update.message.reply_text(f"📡 Monitoring: {'yoqilgan ✅' if monitor.STATE.enabled else 'o`chirilgan ⏸'}")


@admin_only
async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    AGENT.reset(update.effective_chat.id)
    await update.message.reply_text("🧹 Yangi suhbat boshlandi.")


@admin_only
async def cmd_diagnose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = ("Saytni va serverni to'liq tekshir: sayt holati, loglardagi xatolar, servislar (nginx/apache, php-fpm, "
              "ma'lumotlar bazasi va h.k.), resurslar, SSL, xavfsizlik bo'yicha asosiy muammolar. "
              "Hech narsani o'zgartirma — faqat hisobot va tavsiyalar ber.")
    context.application.create_task(run_agent(context.application, update.effective_chat.id, prompt))


@admin_only
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.application.create_task(
        run_agent(context.application, update.effective_chat.id, update.message.text)
    )


@admin_only
async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    dest_dir = Path(CFG.site_dir) / "uploads"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / os.path.basename(doc.file_name or f"file_{doc.file_unique_id}")
    tg_file = await doc.get_file()
    await tg_file.download_to_drive(dest)
    await update.message.reply_text(f"📥 Saqlandi: {dest}")
    if update.message.caption:
        context.application.create_task(run_agent(
            context.application, update.effective_chat.id,
            f"Men {dest} faylini yukladim. {update.message.caption}"))


@admin_only
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    action, _, key = (q.data or "").partition(":")
    fut = PENDING.get(key)
    if not fut or fut.done():
        await q.answer("Bu so'rov eskirgan")
        await q.edit_message_reply_markup(None)
        return
    ok = action == "ok"
    fut.set_result(ok)
    await q.answer("Ruxsat berildi" if ok else "Rad etildi")
    await q.edit_message_text(f"{q.message.text}\n\n{'✅ RUXSAT BERILDI' if ok else '❌ RAD ETILDI'}")


# ---------------- Monitoring ----------------

async def monitor_job(context: ContextTypes.DEFAULT_TYPE):
    app = context.application

    async def alert(text: str, diagnose: bool) -> None:
        for admin in CFG.admin_ids:
            await app.bot.send_message(admin, text)
        if diagnose and CFG.auto_diagnose:
            task = ("Monitoring quyidagi muammoni aniqladi:\n" + text + "\n\nSababini aniqla (loglar, servislar, "
                    "resurslar, oxirgi o'zgarishlar).")
            task += (" Keyin muammoni tuzat va sayt qayta ishlayotganini tekshir." if CFG.auto_fix else
                     " Hech narsani o'zgartirma — faqat sababni va tuzatish rejasini yoz.")
            for admin in CFG.admin_ids:
                app.create_task(run_agent(app, admin, task))

    try:
        await monitor.run_checks(alert)
    except Exception:
        log.exception("Monitoring xatosi")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("Xato: %s", context.error, exc_info=context.error)


async def post_init(app: Application):
    for admin in CFG.admin_ids:
        try:
            await app.bot.send_message(admin, "🚀 Site Guardian ishga tushdi. /start — yordam")
        except Exception as e:
            log.warning("Admin %s ga xabar yuborib bo'lmadi: %s (avval botga /start yozing)", admin, e)


def main():
    CFG.validate()
    app = (Application.builder().token(CFG.telegram_token)
           .concurrent_updates(True).post_init(post_init).build())

    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("ls", cmd_ls))
    app.add_handler(CommandHandler("cat", cmd_cat))
    app.add_handler(CommandHandler("logs", cmd_logs))
    app.add_handler(CommandHandler("monitor", cmd_monitor))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("diagnose", cmd_diagnose))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)

    if CFG.site_urls:
        app.job_queue.run_repeating(monitor_job, interval=CFG.check_interval, first=10)
    else:
        log.warning("SITE_URLS bo'sh — monitoring o'chirilgan")

    log.info("Bot ishga tushdi. Adminlar: %s", sorted(CFG.admin_ids))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
