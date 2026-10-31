"""Claude agenti: foydalanuvchi so'rovini tahlil qiladi va asboblar orqali serverda ishlaydi."""
import asyncio
import json
import logging
import time
from typing import Awaitable, Callable

import anthropic

import tools
from config import CFG

log = logging.getLogger(__name__)

# approve(title, details) -> True/False  (Telegram tugmalari orqali so'raladi)
ApproveFn = Callable[[str, str], Awaitable[bool]]
# notify(text) -> progress xabari yuborish
NotifyFn = Callable[[str], Awaitable[None]]
# send_file(path, caption)
SendFileFn = Callable[[str, str], Awaitable[str]]

MAX_STEPS = 40
# Server-side fallback qo'llab-quvvatlanadigan modellar (Haiku uchun yo'q)
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-5", "claude-fable-5-1"}
BILLING_URL = "https://console.anthropic.com/settings/billing"


class CreditError(Exception):
    """API hisobida kredit tugagan — model almashtirish yordam bermaydi."""

SYSTEM_PROMPT = f"""Siz "Site Guardian" — VPS serverda ishlaydigan DevOps/veb-dasturchi agentsiz.
Egasi sizga Telegram orqali yozadi. Sizning vazifangiz: saytni kuzatish, muammolarni topish va tuzatish,
egasi so'ragan yangi funksiyalarni saytga qo'shish, serverni boshqarish.

Muhit:
- Sayt fayllari: {CFG.site_dir}
- Kuzatiladigan URL'lar: {", ".join(CFG.site_urls) or "(sozlanmagan)"}
- Loglar: {", ".join(CFG.log_files) or "(sozlanmagan)"}
- Siz serverda shell buyruqlarini, fayl o'qish/yozish/tahrirlashni bajara olasiz.

Ish tartibi:
1. Avval o'rganing: kod tuzilmasini, konfiguratsiyani, loglarni ko'ring. Taxmin qilmang — tekshiring.
2. O'zgartirishdan oldin qisqa reja tuzing. Kichik, qaytariladigan qadamlar bilan ishlang.
3. Fayllarni tahrirlash uchun edit_file'ni afzal ko'ring; write_file avtomatik zaxira nusxa oladi.
4. Har bir o'zgarishdan keyin natijani tekshiring (sintaksis tekshiruvi, `nginx -t`, servis holati, saytga so'rov).
5. Ma'lumotlar bazasi yoki muhim konfiguratsiyani o'zgartirishdan oldin zaxira oling.
6. Xavfli amallar (o'chirish, servisni to'xtatish, qayta yuklash va h.k.) egasidan tasdiq so'raydi —
   rad etilsa, boshqa xavfsizroq yo'l taklif qiling.
7. Interaktiv buyruqlardan foydalanmang (nano, vim, top, less). `-y` kabi flaglardan foydalaning.
8. Maxfiy ma'lumotlarni (parollar, API kalitlar, .env mazmuni) javobda to'liq ko'rsatmang.

Javob: o'zbek tilida, qisqa va aniq. Oxirida nima qilinganini, nima tekshirilganini va
qolgan tavsiyalarni ro'yxat qilib bering. Telegram uchun oddiy matn yozing (Markdown jadvallarsiz).
"""

TOOLS = [
    {
        "name": "run_command",
        "description": "Serverda bash buyrug'ini bajaradi va stdout+stderr ni qaytaradi. "
                       "Paket o'rnatish, git, servislar, testlar, build va boshqalar uchun.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Bajariladigan bash buyrug'i"},
                "cwd": {"type": "string", "description": f"Ishchi papka (standart: {CFG.site_dir})"},
                "timeout": {"type": "integer", "description": "Sekundlarda (standart 300)"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "Faylni qator raqamlari bilan o'qiydi.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer", "description": "Boshlang'ich qator (1 dan)"},
                "max_lines": {"type": "integer", "description": "Ko'pi bilan nechta qator (standart 500)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Yangi fayl yaratadi yoki to'liq qayta yozadi (papkalar avtomatik yaratiladi, "
                       "eski fayl zaxiralanadi). append=true bo'lsa oxiriga qo'shadi.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "append": {"type": "boolean"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Fayldagi aniq bir matn bo'lagini (old_text, faylda yagona bo'lishi shart) "
                       "new_text bilan almashtiradi. Eski fayl zaxiralanadi.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
    {
        "name": "list_dir",
        "description": "Papka ichidagi fayl va papkalar ro'yxati.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "site_status",
        "description": "Barcha kuzatiladigan URL'larning holati, javob vaqti, SSL muddati va server resurslari.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "tail_logs",
        "description": "Sozlangan log fayllarning oxirgi qatorlari.",
        "input_schema": {
            "type": "object",
            "properties": {"lines": {"type": "integer", "description": "Har bir fayldan qator soni (standart 50)"}},
        },
    },
    {
        "name": "send_file_to_owner",
        "description": "Serverdagi faylni egasiga Telegram orqali yuboradi (hisobot, zaxira, log va h.k.).",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "caption": {"type": "string"}},
            "required": ["path"],
        },
    },
]


class Agent:
    """Har bir chat uchun alohida suhbat tarixi saqlanadi."""

    def __init__(self) -> None:
        # Kam qayta urinish: limitga yetsa tezda keyingi modelga o'tamiz
        self.client = anthropic.AsyncAnthropic(api_key=CFG.anthropic_api_key, max_retries=1)
        self.histories: dict[int, list] = {}
        self.cooldown_until: dict[str, float] = {}   # model -> qachongacha ishlatilmaydi
        self.last_model: str | None = None
        self.locks: dict[int, asyncio.Lock] = {}

    def reset(self, chat_id: int) -> None:
        self.histories.pop(chat_id, None)

    def model_status(self) -> str:
        now = time.time()
        rows = []
        for i, m in enumerate(CFG.claude_models, 1):
            left = int(self.cooldown_until.get(m, 0) - now)
            state = f"⏸ limit, {left}s dan keyin qaytadi" if left > 0 else "✅ tayyor"
            mark = " ← oxirgi ishlatilgan" if m == self.last_model else ""
            rows.append(f"{i}. {m} — {state}{mark}")
        return "🧠 Modellar zanjiri (yaxshisidan boshlab):\n" + "\n".join(rows)

    def _available_models(self) -> list[str]:
        now = time.time()
        ready = [m for m in CFG.claude_models if self.cooldown_until.get(m, 0) <= now]
        # Hammasi limitda bo'lsa — eng tez bo'shaydiganini sinab ko'ramiz
        return ready or sorted(CFG.claude_models, key=lambda m: self.cooldown_until.get(m, 0))[:1]

    @staticmethod
    def _is_credit_error(e: anthropic.APIStatusError) -> bool:
        return "credit balance" in str(e.message).lower()

    @staticmethod
    def _retry_after(e: anthropic.APIStatusError) -> int | None:
        try:
            return int(float(e.response.headers.get("retry-after", "")))
        except (ValueError, AttributeError):
            return None

    async def _create(self, history: list, notify: NotifyFn):
        """Eng yaxshi bo'sh model bilan so'rov yuboradi; limit/yuklama bo'lsa keyingisiga o'tadi."""
        last_error: Exception | None = None
        for model in self._available_models() + [None]:
            if model is None:
                break
            params = dict(
                model=model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=history,
                output_config={"effort": CFG.claude_effort},
                cache_control={"type": "ephemeral"},
            )
            if model in FALLBACK_MODELS:
                params.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            try:
                response = await self.client.beta.messages.create(**params)
            except anthropic.APIStatusError as e:
                if self._is_credit_error(e):
                    raise CreditError() from e
                # 429 limit, 529/5xx yuklama, 404 model mavjud emas -> keyingi model
                if e.status_code in (404, 429) or e.status_code >= 500:
                    wait = self._retry_after(e) or CFG.model_cooldown
                    self.cooldown_until[model] = time.time() + wait
                    last_error = e
                    log.warning("Model %s ishlamadi (%s), %ss ga to'xtatildi", model, e.status_code, wait)
                    await notify(f"⚠️ {model}: {'limitga yetdi' if e.status_code == 429 else f'xato {e.status_code}'}"
                                 f" — keyingi modelga o'tyapman "
                                 f"({f'{wait // 60} daqiqa' if wait >= 60 else f'{wait} sekund'}dan keyin qaytaman)")
                    continue
                raise
            if model != self.last_model and self.last_model is not None:
                better = CFG.claude_models.index(model) < CFG.claude_models.index(self.last_model) \
                    if self.last_model in CFG.claude_models else False
                if better:
                    await notify(f"⬆️ Limit tiklandi — yana yaxshiroq modelga qaytdim: {model}")
            self.last_model = model
            return response
        raise last_error or RuntimeError("Hech qanday model mavjud emas")

    async def _needs_approval(self, name: str, args: dict) -> tuple[bool, str]:
        mode = CFG.confirm_mode
        if mode == "none":
            return False, ""
        if name == "run_command":
            cmd = args.get("command", "")
            if mode == "all" or tools.is_dangerous_command(cmd):
                return True, f"💻 Buyruq:\n{cmd}\n\n📂 cwd: {args.get('cwd') or CFG.site_dir}"
        elif name in ("write_file", "edit_file"):
            if mode == "all":
                preview = args.get("content") or args.get("new_text") or ""
                return True, f"✏️ {name}: {args.get('path')}\n\n{preview[:1500]}"
        return False, ""

    async def _execute(self, name: str, args: dict, approve: ApproveFn, send_file: SendFileFn) -> tuple[str, bool]:
        need, details = await self._needs_approval(name, args)
        if need and not await approve("⚠️ Claude quyidagi amalni bajarmoqchi:", details):
            return "Egasi bu amalni RAD ETDI. Uni bajarmang; xavfsizroq muqobil taklif qiling yoki so'rang.", True
        try:
            if name == "run_command":
                return await tools.run_command(args["command"], args.get("cwd"), args.get("timeout")), False
            if name == "read_file":
                return await asyncio.to_thread(tools.read_file, args["path"],
                                               args.get("start_line", 1), args.get("max_lines", 500)), False
            if name == "write_file":
                return await asyncio.to_thread(tools.write_file, args["path"], args["content"],
                                               args.get("append", False)), False
            if name == "edit_file":
                return await asyncio.to_thread(tools.edit_file, args["path"], args["old_text"],
                                               args["new_text"]), False
            if name == "list_dir":
                return await asyncio.to_thread(tools.list_dir, args["path"]), False
            if name == "site_status":
                return await tools.site_status(), False
            if name == "tail_logs":
                return await asyncio.to_thread(tools.tail_logs, args.get("lines", 50)), False
            if name == "send_file_to_owner":
                return await send_file(args["path"], args.get("caption", "")), False
            return f"Noma'lum asbob: {name}", True
        except KeyError as e:
            return f"Xato: majburiy parametr yo'q: {e}", True
        except Exception as e:
            log.exception("Tool %s xatosi", name)
            return f"Xato: {type(e).__name__}: {e}", True

    async def run(self, chat_id: int, user_text: str, approve: ApproveFn,
                  notify: NotifyFn, send_file: SendFileFn) -> str:
        lock = self.locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            history = self.histories.setdefault(chat_id, [])
            turn_start = len(history)
            history.append({"role": "user", "content": user_text})

            for _step in range(MAX_STEPS):
                try:
                    response = await self._create(history, notify)
                except CreditError:
                    del history[turn_start:]
                    return ("💳 Anthropic API hisobingizda kredit tugagan.\n"
                            f"To'ldirish: {BILLING_URL}\n"
                            "(claude.ai obunasi API uchun ishlamaydi — API krediti alohida sotib olinadi.)")
                except anthropic.APIStatusError as e:
                    del history[turn_start:]  # yarim qolgan navbatni tarixdan olib tashlash
                    return f"Claude API xatosi ({e.status_code}): {e.message}"
                except anthropic.APIConnectionError:
                    del history[turn_start:]
                    return "Claude API bilan aloqa yo'q. Internetni tekshiring."

                # Javobni to'liq (thinking bloklari bilan) tarixga qo'shamiz — append-only.
                history.append({"role": "assistant", "content": response.content})

                if response.stop_reason == "refusal":
                    return "Claude bu so'rovni bajarishdan bosh tortdi. So'rovni boshqacha ifodalab ko'ring."

                tool_uses = [b for b in response.content if b.type == "tool_use"]
                texts = [b.text for b in response.content if b.type == "text" and b.text.strip()]

                if response.stop_reason == "pause_turn":
                    continue
                if not tool_uses:
                    if response.stop_reason == "max_tokens":
                        texts.append("\n(Javob uzunlik chegarasiga yetdi — 'davom et' deb yozing.)")
                    return "\n".join(texts) or "✅ Tayyor."

                if texts:
                    await notify("💭 " + "\n".join(texts))

                results = []
                for block in tool_uses:
                    args = block.input if isinstance(block.input, dict) else json.loads(block.input)
                    short = args.get("command") or args.get("path") or ""
                    await notify(f"🔧 {block.name}: {str(short)[:300]}")
                    output, is_error = await self._execute(block.name, args, approve, send_file)
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": output,
                        "is_error": is_error,
                    })
                history.append({"role": "user", "content": results})

            return f"⏹ {MAX_STEPS} qadamdan keyin to'xtadim. Davom ettirish uchun 'davom et' deb yozing."
