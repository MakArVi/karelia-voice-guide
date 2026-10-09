"""Проверка подписи данных мини-приложения (Telegram.WebApp.initData)."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


def validate_init_data(init_data: str, bot_token: str, max_age: int = 24 * 3600) -> dict | None:
    """Возвращает пользователя Telegram, если подпись верна и данные не устарели, иначе None."""
    try:
        fields = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    received = fields.pop("hash", None)
    if not received:
        return None
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return None
    if time.time() - int(fields.get("auth_date", "0")) > max_age:
        return None
    try:
        user = json.loads(fields.get("user", "{}"))
    except json.JSONDecodeError:
        return None
    return user if isinstance(user, dict) and "id" in user else None
