"""
cache.py — Video tarjima va mazmunlarini kesh qilish moduli.
Kesh /tmp/bot_cache.json faylida saqlanadi.
TTL: 7 kun (604800 soniya)
"""
import json
import os
import time
import logging

logger = logging.getLogger(__name__)

CACHE_FILE = "/tmp/bot_cache.json"
CACHE_TTL = 7 * 24 * 3600  # 7 kun


def _load() -> dict:
    """Kesh faylini o'qiydi."""
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: dict) -> None:
    """Kesh faylini yozadi."""
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Kesh saqlashda xato: {e}")


def get(video_id: str, key: str) -> str | None:
    """
    Keshdan qiymat oladi.
    key: 'translation' yoki 'summary'
    """
    data = _load()
    entry = data.get(f"{video_id}:{key}")
    if not entry:
        return None
    # TTL tekshirish
    if time.time() - entry.get("ts", 0) > CACHE_TTL:
        logger.info(f"Kesh eskirgan: {video_id}:{key}")
        return None
    logger.info(f"Keshdan olindi: {video_id}:{key}")
    return entry.get("value")


def set(video_id: str, key: str, value: str) -> None:
    """
    Keshga qiymat yozadi.
    key: 'translation' yoki 'summary'
    """
    data = _load()
    data[f"{video_id}:{key}"] = {
        "value": value,
        "ts": time.time()
    }
    _save(data)
    logger.info(f"Keshga yozildi: {video_id}:{key} ({len(value)} belgi)")


def clear_expired() -> int:
    """Eskirgan kesh yozuvlarini tozalaydi. O'chirilgan yozuvlar sonini qaytaradi."""
    data = _load()
    now = time.time()
    before = len(data)
    data = {k: v for k, v in data.items() if now - v.get("ts", 0) <= CACHE_TTL}
    _save(data)
    removed = before - len(data)
    if removed:
        logger.info(f"{removed} ta eskirgan kesh yozuvi o'chirildi")
    return removed


def stats() -> dict:
    """Kesh statistikasi."""
    data = _load()
    now = time.time()
    active = sum(1 for v in data.values() if now - v.get("ts", 0) <= CACHE_TTL)
    return {"total": len(data), "active": active, "expired": len(data) - active}
