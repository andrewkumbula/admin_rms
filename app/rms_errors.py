"""Эвристики по текстам ошибок RMS API для UI (например, обновление токена на месте)."""

from __future__ import annotations

from typing import Optional


def error_suggests_token_refresh(message: Optional[str]) -> bool:
    """Показать поле ввода нового access token рядом с ошибкой."""
    if not message or not isinstance(message, str):
        return False
    s = message.strip().lower()
    if "не валидный" in s or "невалидн" in s:
        return True
    if "401" in s:
        return True
    if "unauthorized" in s:
        return True
    if "invalid" in s and "token" in s:
        return True
    if "jwt expired" in s or "token expired" in s:
        return True
    return False


def safe_next_path(next_raw: Optional[str]) -> Optional[str]:
    """Только относительный путь на том же сайте (без open redirect)."""
    if not next_raw or not isinstance(next_raw, str):
        return None
    u = next_raw.strip()
    if not u.startswith("/") or u.startswith("//"):
        return None
    if "\n" in u or "\r" in u or "\x00" in u:
        return None
    if len(u) > 2048:
        return None
    return u
