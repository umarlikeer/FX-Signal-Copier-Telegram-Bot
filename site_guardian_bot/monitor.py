"""Doimiy monitoring: sayt holati, tezligi, SSL, server resurslari."""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import tools
from config import CFG

log = logging.getLogger(__name__)

AlertFn = Callable[[str, bool], Awaitable[None]]  # (matn, diagnoz_kerakmi)


@dataclass
class UrlState:
    up: bool = True
    fail_count: int = 0
    down_since: float | None = None
    slow: bool = False


@dataclass
class MonitorState:
    enabled: bool = True
    urls: dict[str, UrlState] = field(default_factory=dict)
    resource_alerted: set[str] = field(default_factory=set)
    last_ssl_check: float = 0.0
    last_check: float = 0.0
    checks_total: int = 0
    failures_total: int = 0


STATE = MonitorState()
FAILS_BEFORE_ALERT = 2  # ketma-ket 2 ta xatodan keyin ogohlantirish (yolg'on signalni kamaytirish)


async def run_checks(alert: AlertFn) -> None:
    if not STATE.enabled:
        return
    STATE.last_check = time.time()

    for url in CFG.site_urls:
        st = STATE.urls.setdefault(url, UrlState())
        r = await tools.check_url(url)
        STATE.checks_total += 1

        if not r["ok"]:
            STATE.failures_total += 1
            st.fail_count += 1
            if st.up and st.fail_count >= FAILS_BEFORE_ALERT:
                st.up = False
                st.down_since = time.time()
                reason = r["error"] or f"HTTP {r['status']}"
                if r["keyword_ok"] is False:
                    reason += f" (sahifada '{CFG.site_keyword}' topilmadi)"
                await alert(f"🔴 SAYT ISHLAMAYAPTI\n{url}\nSabab: {reason}\nJavob vaqti: {r['ms']} ms", True)
            continue

        if not st.up:
            downtime = int(time.time() - (st.down_since or time.time()))
            await alert(f"🟢 Sayt qayta ishlayapti\n{url}\nIshlamagan vaqt: {downtime // 60} daq {downtime % 60} s", False)
        st.up, st.fail_count, st.down_since = True, 0, None

        is_slow = r["ms"] > CFG.slow_threshold_ms
        if is_slow and not st.slow:
            await alert(f"🐢 Sayt sekin ishlayapti: {url}\nJavob vaqti: {r['ms']} ms (chegara {CFG.slow_threshold_ms} ms)", False)
        st.slow = is_slow

    # Server resurslari
    stats = await asyncio.to_thread(tools.server_stats)
    for key, label in (("disk_percent", "Disk"), ("ram_percent", "RAM"), ("cpu_percent", "CPU")):
        high = stats[key] >= CFG.resource_alert_percent
        if high and key not in STATE.resource_alerted:
            STATE.resource_alerted.add(key)
            await alert(f"⚠️ {label} band: {stats[key]}% (chegara {CFG.resource_alert_percent}%)", key == "disk_percent")
        elif not high:
            STATE.resource_alerted.discard(key)

    # SSL — kuniga bir marta
    if time.time() - STATE.last_ssl_check > 86400:
        STATE.last_ssl_check = time.time()
        for url in CFG.site_urls:
            try:
                days = await asyncio.to_thread(tools.ssl_days_left, url)
            except Exception as e:
                await alert(f"🔐 SSL tekshiruvida xato: {url}\n{e}", False)
                continue
            if days is not None and days <= 14:
                await alert(f"🔐 SSL sertifikat {days} kundan keyin tugaydi: {url}\n"
                            f"Yangilash: certbot renew", False)
