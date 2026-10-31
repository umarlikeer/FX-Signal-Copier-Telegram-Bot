"""Claude ishlatadigan asboblar: terminal, fayllar, sayt tekshiruvi."""
import asyncio
import os
import re
import shutil
import socket
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psutil

from config import CFG

MAX_OUTPUT = 20_000  # Claude'ga qaytariladigan maksimal belgilar

# Tasdiq talab qiladigan xavfli buyruq namunalari
DANGEROUS_PATTERNS = [
    r"\brm\s+(-\w*[rf]\w*\s+)+",          # rm -rf / rm -r
    r"\brm\b.*\s/(\s|$)",
    r"\bmkfs", r"\bdd\s+", r"\bfdisk", r"\bparted",
    r"\bshutdown\b", r"\breboot\b", r"\bhalt\b", r"\bpoweroff\b", r"\binit\s+[06]\b",
    r"\bsystemctl\s+(stop|disable|mask|kill)", r"\bservice\s+\S+\s+stop",
    r"\bkill(all)?\b", r"\bpkill\b",
    r"\bchmod\s+(-R\s+)?[0-7]*777", r"\bchown\s+-R",
    r"\b(apt|apt-get|yum|dnf)\s+(remove|purge|autoremove)",
    r"\buserdel\b", r"\bpasswd\b", r"\bvisudo\b",
    r"\biptables\s+-(F|X|P)", r"\bufw\s+(disable|reset|deny)",
    r"\bDROP\s+(TABLE|DATABASE)\b", r"\bTRUNCATE\b", r"\bDELETE\s+FROM\b",
    r"\bgit\s+(reset\s+--hard|clean\s+-\w*f|push\s+.*--force)",
    r">\s*/dev/sd", r">\s*/etc/", r":\(\)\s*\{",  # fork bomb
    r"\bcrontab\s+-r", r"\bmv\s+\S+\s+/dev/null",
    r"curl[^|]*\|\s*(ba)?sh", r"wget[^|]*\|\s*(ba)?sh",
]
_DANGER_RE = re.compile("|".join(DANGEROUS_PATTERNS), re.IGNORECASE)


def is_dangerous_command(cmd: str) -> bool:
    return bool(_DANGER_RE.search(cmd))


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    half = MAX_OUTPUT // 2
    return f"{text[:half]}\n\n... [{len(text) - MAX_OUTPUT} belgi qisqartirildi] ...\n\n{text[-half:]}"


async def run_command(command: str, cwd: str | None = None, timeout: int | None = None) -> str:
    cwd = cwd or CFG.site_dir
    if not os.path.isdir(cwd):
        cwd = "/"
    timeout = min(timeout or CFG.command_timeout, 3600)
    start = time.monotonic()
    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return f"[TIMEOUT] Buyruq {timeout}s ichida tugamadi va to'xtatildi."
    took = time.monotonic() - start
    text = out.decode("utf-8", errors="replace")
    return _clip(f"[exit={proc.returncode}, {took:.1f}s, cwd={cwd}]\n{text}")


def read_file(path: str, start_line: int = 1, max_lines: int = 500) -> str:
    p = Path(path).expanduser()
    if not p.is_file():
        return f"Xato: fayl topilmadi: {p}"
    with p.open("r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    start = max(start_line, 1) - 1
    chunk = lines[start:start + max_lines]
    body = "".join(f"{i}\t{l}" for i, l in enumerate(chunk, start=start + 1))
    return _clip(f"[{p} — jami {len(lines)} qator]\n{body}")


def backup_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = Path(CFG.backup_dir) / stamp / str(path).lstrip("/")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)
    return str(dest)


def write_file(path: str, content: str, append: bool = False) -> str:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    backup = backup_file(p)
    with p.open("a" if append else "w", encoding="utf-8") as f:
        f.write(content)
    action = "qo'shildi" if append else "yozildi"
    msg = f"OK: {action} {p} ({len(content)} belgi)"
    if backup:
        msg += f"\nEski nusxa zaxiralandi: {backup}"
    return msg


def edit_file(path: str, old_text: str, new_text: str) -> str:
    p = Path(path).expanduser()
    if not p.is_file():
        return f"Xato: fayl topilmadi: {p}"
    text = p.read_text(encoding="utf-8", errors="replace")
    count = text.count(old_text)
    if count == 0:
        return "Xato: old_text faylda topilmadi. Avval read_file bilan aniq matnni ko'ring."
    if count > 1:
        return f"Xato: old_text faylda {count} marta uchradi — kontekst qo'shib, uni yagona qiling."
    backup = backup_file(p)
    p.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
    return f"OK: {p} tahrirlandi. Zaxira: {backup}"


def list_dir(path: str) -> str:
    p = Path(path).expanduser()
    if not p.is_dir():
        return f"Xato: papka topilmadi: {p}"
    rows = []
    for entry in sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name))[:500]:
        try:
            st = entry.stat()
            kind = "DIR " if entry.is_dir() else "FILE"
            mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
            rows.append(f"{kind} {st.st_size:>10}  {mtime}  {entry.name}")
        except OSError as e:
            rows.append(f"?    {entry.name} ({e})")
    return _clip(f"[{p}]\n" + "\n".join(rows))


async def check_url(url: str) -> dict:
    """Saytga so'rov yuborib holatini qaytaradi."""
    result = {"url": url, "ok": False, "status": None, "ms": None, "error": None, "keyword_ok": None}
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                     headers={"User-Agent": "SiteGuardianBot/1.0"}) as client:
            r = await client.get(url)
        result["ms"] = int((time.monotonic() - start) * 1000)
        result["status"] = r.status_code
        result["ok"] = r.status_code < 400
        if CFG.site_keyword:
            result["keyword_ok"] = CFG.site_keyword in r.text
            result["ok"] = result["ok"] and result["keyword_ok"]
    except Exception as e:  # tarmoq, DNS, SSL xatolari
        result["ms"] = int((time.monotonic() - start) * 1000)
        result["error"] = f"{type(e).__name__}: {e}"
    return result


def ssl_days_left(url: str) -> int | None:
    host = httpx.URL(url).host
    if not url.startswith("https"):
        return None
    ctx = ssl.create_default_context()
    with socket.create_connection((host, 443), timeout=10) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as s:
            cert = s.getpeercert()
    expires = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    return (expires - datetime.now(timezone.utc)).days


def server_stats() -> dict:
    disk = psutil.disk_usage("/")
    mem = psutil.virtual_memory()
    load = os.getloadavg()
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.5),
        "load_avg": [round(x, 2) for x in load],
        "ram_percent": mem.percent,
        "ram_used_gb": round(mem.used / 1e9, 2),
        "ram_total_gb": round(mem.total / 1e9, 2),
        "disk_percent": disk.percent,
        "disk_free_gb": round(disk.free / 1e9, 2),
        "uptime_hours": round((time.time() - psutil.boot_time()) / 3600, 1),
    }


async def site_status() -> str:
    lines = []
    for url in CFG.site_urls:
        r = await check_url(url)
        try:
            days = await asyncio.to_thread(ssl_days_left, url)
        except Exception as e:
            days = f"xato ({e})"
        lines.append(
            f"{'✅' if r['ok'] else '❌'} {url} — status={r['status']}, {r['ms']} ms"
            + ((", keyword=bor" if r["keyword_ok"] else ", keyword=yo'q") if r["keyword_ok"] is not None else "")
            + (f", xato={r['error']}" if r["error"] else "")
            + (f", SSL tugashiga {days} kun" if days is not None else "")
        )
    s = server_stats()
    lines.append(
        f"🖥 CPU {s['cpu_percent']}% (load {s['load_avg']}), RAM {s['ram_percent']}% "
        f"({s['ram_used_gb']}/{s['ram_total_gb']} GB), Disk {s['disk_percent']}% "
        f"(bo'sh {s['disk_free_gb']} GB), uptime {s['uptime_hours']} soat"
    )
    return "\n".join(lines)


def tail_logs(lines: int = 50) -> str:
    out = []
    for path in CFG.log_files:
        p = Path(path)
        if not p.is_file():
            out.append(f"--- {path}: topilmadi")
            continue
        with p.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 200_000))
            data = f.read().decode("utf-8", errors="replace").splitlines()[-lines:]
        out.append(f"--- {path} (oxirgi {len(data)} qator)\n" + "\n".join(data))
    return _clip("\n\n".join(out) or "LOG_FILES sozlanmagan.")
