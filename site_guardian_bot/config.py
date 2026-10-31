"""Konfiguratsiya: .env fayldan o'qiladi."""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    admin_ids: frozenset[int] = field(
        default_factory=lambda: frozenset(int(x) for x in _list("ADMIN_IDS"))
    )
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    claude_model: str = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
    claude_effort: str = os.getenv("CLAUDE_EFFORT", "high")

    site_urls: list[str] = field(default_factory=lambda: _list("SITE_URLS"))
    site_dir: str = os.getenv("SITE_DIR", "/var/www")
    site_keyword: str = os.getenv("SITE_KEYWORD", "")
    check_interval: int = int(os.getenv("CHECK_INTERVAL", "60"))
    slow_threshold_ms: int = int(os.getenv("SLOW_THRESHOLD_MS", "3000"))
    resource_alert_percent: float = float(os.getenv("RESOURCE_ALERT_PERCENT", "90"))
    log_files: list[str] = field(default_factory=lambda: _list("LOG_FILES"))

    confirm_mode: str = os.getenv("CONFIRM_MODE", "dangerous").lower()
    auto_diagnose: bool = _bool("AUTO_DIAGNOSE", True)
    auto_fix: bool = _bool("AUTO_FIX", False)
    command_timeout: int = int(os.getenv("COMMAND_TIMEOUT", "300"))
    backup_dir: str = os.getenv("BACKUP_DIR", "/var/backups/site-guardian")

    def validate(self) -> None:
        missing = [
            n for n, v in (
                ("TELEGRAM_BOT_TOKEN", self.telegram_token),
                ("ADMIN_IDS", self.admin_ids),
                ("ANTHROPIC_API_KEY", self.anthropic_api_key),
            ) if not v
        ]
        if missing:
            raise SystemExit(f".env da quyidagilar to'ldirilmagan: {', '.join(missing)}")
        if self.confirm_mode not in ("dangerous", "all", "none"):
            raise SystemExit("CONFIRM_MODE faqat dangerous | all | none bo'lishi mumkin")


CFG = Config()
