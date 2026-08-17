import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from functools import wraps
from pathlib import Path
from urllib.parse import parse_qsl

import requests
from dotenv import load_dotenv
from flask import Flask, Response, g, jsonify, render_template, request, send_from_directory
from telebot import TeleBot, types
from werkzeug.utils import secure_filename


load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / "static" / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


def get_env(name: str, default: str = "", required: bool = False) -> str:
    value = os.getenv(name, default).strip()
    if required and not value:
        raise RuntimeError(f"{name} is not set")
    return value


BOT_TOKEN = get_env("BOT_TOKEN", required=True)
WEBHOOK_BASE_URL = get_env("WEBHOOK_BASE_URL").rstrip("/")
WEBAPP_URL = get_env("WEBAPP_URL", required=True).rstrip("/")
WEBHOOK_SECRET_TOKEN = get_env("WEBHOOK_SECRET_TOKEN")
PORT = int(get_env("PORT", "8080"))
BOT_PARSE_MODE = get_env("BOT_PARSE_MODE", "HTML") or "HTML"
BOT_WEBAPP_BUTTON_TEXT = get_env("BOT_WEBAPP_BUTTON_TEXT", "Open Roulette")
BOT_START_TEXT = get_env("BOT_START_TEXT", "Привет! Открывай Mini App, пополняй баланс и играй в рулетку.")
BOT_FALLBACK_TEXT = get_env("BOT_FALLBACK_TEXT", "Напиши /start или открой Mini App кнопкой ниже.")
BOT_WEBAPP_RESPONSE_PREFIX = get_env("BOT_WEBAPP_RESPONSE_PREFIX", "Данные из Mini App:")
LANDING_MESSAGE = get_env("LANDING_MESSAGE", "Roulette bot server is running")
APP_TITLE = get_env("APP_TITLE", "Roulette Mini App")
APP_SUBTITLE = get_env("APP_SUBTITLE", "Рулетка, баланс, бонусы и выплаты")
ADMIN_IDS = {
    int(item.strip())
    for item in get_env("ADMIN_IDS", "").split(",")
    if item.strip().isdigit()
}
BET_LOG_CHAT_ID = get_env("BET_LOG_CHAT_ID")
PAYOUT_REVIEW_CHAT_ID = get_env("PAYOUT_REVIEW_CHAT_ID")
DB_PATH = BASE_DIR / get_env("DB_PATH", "roulette.sqlite3")
BALANCE_CURRENCY = get_env("BALANCE_CURRENCY", "USDT")
BALANCE_DECIMALS = int(get_env("BALANCE_DECIMALS", "2"))
MIN_BET_AMOUNT = Decimal(get_env("MIN_BET_AMOUNT", "1"))
MIN_DEPOSIT_AMOUNT = Decimal(get_env("MIN_DEPOSIT_AMOUNT", "1"))
MIN_WITHDRAW_AMOUNT = Decimal(get_env("MIN_WITHDRAW_AMOUNT", "1"))
MAX_WITHDRAW_AMOUNT = Decimal(get_env("MAX_WITHDRAW_AMOUNT", "1000"))
MAX_DEPOSIT_AMOUNT = Decimal(get_env("MAX_DEPOSIT_AMOUNT", "5000"))
WITHDRAW_FEE_PERCENT = Decimal(get_env("WITHDRAW_FEE_PERCENT", "0"))
DEFAULT_DEPOSITS_ENABLED = get_env("DEFAULT_DEPOSITS_ENABLED", "true").lower() == "true"
DEFAULT_WITHDRAWALS_ENABLED = get_env("DEFAULT_WITHDRAWALS_ENABLED", "true").lower() == "true"
DEFAULT_AUTO_PAYOUTS = get_env("DEFAULT_AUTO_PAYOUTS", "false").lower() == "true"
DEFAULT_PROMO_DAILY_LIMIT = int(get_env("DEFAULT_PROMO_DAILY_LIMIT", "3"))
INIT_DATA_TTL = int(get_env("INIT_DATA_TTL", "86400"))
CRYPTO_PAY_API_TOKEN = get_env("CRYPTO_PAY_API_TOKEN")
CRYPTO_PAY_BASE_URL = get_env("CRYPTO_PAY_BASE_URL", "https://pay.crypt.bot/api").rstrip("/")
CRYPTO_PAY_ASSET = get_env("CRYPTO_PAY_ASSET", "USDT")
CRYPTO_PAY_INVOICE_DESCRIPTION = get_env("CRYPTO_PAY_INVOICE_DESCRIPTION", "Пополнение игрового баланса")
CRYPTO_PAY_TRANSFER_COMMENT = get_env("CRYPTO_PAY_TRANSFER_COMMENT", "Вывод игрового баланса")
REQUESTS_TIMEOUT = int(get_env("REQUESTS_TIMEOUT", "20"))
ROULETTE_RED_MULTIPLIER = Decimal(get_env("ROULETTE_RED_MULTIPLIER", "2"))
ROULETTE_BLACK_MULTIPLIER = Decimal(get_env("ROULETTE_BLACK_MULTIPLIER", "4"))
ROULETTE_GREEN_MULTIPLIER = Decimal(get_env("ROULETTE_GREEN_MULTIPLIER", "14"))

COLOR_MULTIPLIERS = {
    "red": ROULETTE_RED_MULTIPLIER,
    "black": ROULETTE_BLACK_MULTIPLIER,
    "green": ROULETTE_GREEN_MULTIPLIER,
}
WHEEL = {
    1: "red",
    2: "black",
    3: "red",
    4: "black",
    5: "red",
    6: "black",
    7: "red",
    8: "black",
    9: "red",
    10: "black",
    11: "red",
    12: "black",
    13: "red",
    14: "black",
    15: "green",
}
COLOR_LABELS = {
    "red": "Красный",
    "black": "Черный",
    "green": "Зеленый",
}
UPLOAD_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


bot = TeleBot(BOT_TOKEN, parse_mode=BOT_PARSE_MODE)
app = Flask(__name__)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().replace(microsecond=0).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def quant(value: Decimal) -> Decimal:
    step = Decimal("1").scaleb(-BALANCE_DECIMALS)
    return value.quantize(step, rounding=ROUND_DOWN)


def format_amount(value: Decimal | str | int | float) -> str:
    decimal_value = quant(Decimal(str(value)))
    text = format(decimal_value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def parse_amount(raw_value: str, *, minimum: Decimal | None = None, maximum: Decimal | None = None) -> Decimal:
    if raw_value is None:
        raise ValueError("Сумма не указана")
    try:
        value = quant(Decimal(str(raw_value).replace(",", ".")))
    except (InvalidOperation, ValueError):
        raise ValueError("Некорректная сумма") from None
    if value <= 0:
        raise ValueError("Сумма должна быть больше нуля")
    if minimum is not None and value < minimum:
        raise ValueError(f"Минимальная сумма: {format_amount(minimum)} {BALANCE_CURRENCY}")
    if maximum is not None and value > maximum:
        raise ValueError(f"Максимальная сумма: {format_amount(maximum)} {BALANCE_CURRENCY}")
    return value


def parse_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def parse_int(value: object, default: int = 0, minimum: int | None = None) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    return parsed


def parse_decimal_setting(value: object, default: Decimal) -> Decimal:
    try:
        return quant(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError):
        return quant(default)


def normalize_color(value: str, fallback: str) -> str:
    raw = str(value or "").strip()
    if not raw.startswith("#"):
        raw = f"#{raw}"
    if len(raw) not in {4, 7}:
        return fallback
    allowed = set("#0123456789abcdefABCDEF")
    return raw if set(raw) <= allowed else fallback


def get_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                balance TEXT NOT NULL DEFAULT '0',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                amount TEXT NOT NULL,
                color TEXT NOT NULL,
                multiplier TEXT NOT NULL,
                winning_number INTEGER NOT NULL,
                winning_color TEXT NOT NULL,
                payout TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS invoices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                crypto_invoice_id INTEGER NOT NULL UNIQUE,
                invoice_hash TEXT NOT NULL,
                amount TEXT NOT NULL,
                asset TEXT NOT NULL,
                pay_url TEXT NOT NULL,
                status TEXT NOT NULL,
                credited_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                amount TEXT NOT NULL,
                asset TEXT NOT NULL,
                status TEXT NOT NULL,
                spend_id TEXT NOT NULL UNIQUE,
                transfer_id INTEGER,
                review_chat_id TEXT,
                review_message_id INTEGER,
                reviewed_by INTEGER,
                review_note TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bonus_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                channel_ref TEXT NOT NULL,
                reward_amount TEXT NOT NULL,
                image_path TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bonus_claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bonus_id INTEGER NOT NULL,
                telegram_id INTEGER NOT NULL,
                amount TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(bonus_id, telegram_id)
            );

            CREATE TABLE IF NOT EXISTS promo_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                reward_amount TEXT NOT NULL,
                max_activations INTEGER NOT NULL,
                current_activations INTEGER NOT NULL DEFAULT 0,
                deposit_required INTEGER NOT NULL DEFAULT 0,
                deposit_days INTEGER NOT NULL DEFAULT 0,
                deposit_amount TEXT NOT NULL DEFAULT '0',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS promo_claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                promo_id INTEGER NOT NULL,
                telegram_id INTEGER NOT NULL,
                amount TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(promo_id, telegram_id)
            );

            CREATE TABLE IF NOT EXISTS admin_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER NOT NULL,
                action_type TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT,
                details TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS broadcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                image_path TEXT,
                text_content TEXT NOT NULL,
                buttons_json TEXT NOT NULL,
                recipient_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                sent_at TEXT
            );
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
            ("auto_payouts", "true" if DEFAULT_AUTO_PAYOUTS else "false"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
            ("promo_daily_limit", str(DEFAULT_PROMO_DAILY_LIMIT)),
        )
        conn.executemany(
            "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
            [
                ("payment_min_deposit", format_amount(MIN_DEPOSIT_AMOUNT)),
                ("payment_max_deposit", format_amount(MAX_DEPOSIT_AMOUNT)),
                ("payment_min_withdraw", format_amount(MIN_WITHDRAW_AMOUNT)),
                ("payment_max_withdraw", format_amount(MAX_WITHDRAW_AMOUNT)),
                ("payment_withdraw_fee_percent", format_amount(WITHDRAW_FEE_PERCENT)),
                ("payment_deposits_enabled", "true" if DEFAULT_DEPOSITS_ENABLED else "false"),
                ("payment_withdrawals_enabled", "true" if DEFAULT_WITHDRAWALS_ENABLED else "false"),
                ("payment_method_title", "CryptoBot"),
                ("payment_method_icon_path", ""),
                ("app_title_override", APP_TITLE),
                ("app_subtitle_override", APP_SUBTITLE),
                ("app_logo_path", ""),
                ("app_background_path", ""),
                ("app_primary_color", "#9d63ff"),
                ("app_secondary_color", "#5f35c7"),
                ("app_accent_color", "#7ec8ff"),
                ("app_support_channel", ""),
                ("app_telegram_link", ""),
                ("app_crypto_bot_link", "https://t.me/CryptoBot"),
                ("app_landing_text", LANDING_MESSAGE),
                ("app_notifications_enabled", "true"),
            ],
        )


def get_setting(key: str, default: str = "") -> str:
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO settings(key, value) VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def get_settings_map(prefix: str) -> dict[str, str]:
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM settings WHERE key LIKE ?", (f"{prefix}%",)).fetchall()
    return {row["key"]: row["value"] for row in rows}


def get_auto_payouts() -> bool:
    return get_setting("auto_payouts", "true" if DEFAULT_AUTO_PAYOUTS else "false") == "true"


def get_promo_daily_limit() -> int:
    raw = get_setting("promo_daily_limit", str(DEFAULT_PROMO_DAILY_LIMIT))
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_PROMO_DAILY_LIMIT


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def get_payment_settings() -> dict:
    return {
        "method_title": get_setting("payment_method_title", "CryptoBot"),
        "method_icon_path": get_setting("payment_method_icon_path", ""),
        "min_deposit_amount": format_amount(
            parse_decimal_setting(get_setting("payment_min_deposit", format_amount(MIN_DEPOSIT_AMOUNT)), MIN_DEPOSIT_AMOUNT)
        ),
        "max_deposit_amount": format_amount(
            parse_decimal_setting(get_setting("payment_max_deposit", format_amount(MAX_DEPOSIT_AMOUNT)), MAX_DEPOSIT_AMOUNT)
        ),
        "min_withdraw_amount": format_amount(
            parse_decimal_setting(get_setting("payment_min_withdraw", format_amount(MIN_WITHDRAW_AMOUNT)), MIN_WITHDRAW_AMOUNT)
        ),
        "max_withdraw_amount": format_amount(
            parse_decimal_setting(get_setting("payment_max_withdraw", format_amount(MAX_WITHDRAW_AMOUNT)), MAX_WITHDRAW_AMOUNT)
        ),
        "withdraw_fee_percent": format_amount(
            parse_decimal_setting(get_setting("payment_withdraw_fee_percent", format_amount(WITHDRAW_FEE_PERCENT)), WITHDRAW_FEE_PERCENT)
        ),
        "deposits_enabled": parse_bool(
            get_setting("payment_deposits_enabled", "true" if DEFAULT_DEPOSITS_ENABLED else "false"),
            DEFAULT_DEPOSITS_ENABLED,
        ),
        "withdrawals_enabled": parse_bool(
            get_setting("payment_withdrawals_enabled", "true" if DEFAULT_WITHDRAWALS_ENABLED else "false"),
            DEFAULT_WITHDRAWALS_ENABLED,
        ),
        "crypto_configured": bool(CRYPTO_PAY_API_TOKEN),
        "asset": CRYPTO_PAY_ASSET,
    }


def get_app_visual_settings() -> dict:
    return {
        "app_title": get_setting("app_title_override", APP_TITLE),
        "app_subtitle": get_setting("app_subtitle_override", APP_SUBTITLE),
        "logo_path": get_setting("app_logo_path", ""),
        "background_path": get_setting("app_background_path", ""),
        "primary_color": normalize_color(get_setting("app_primary_color", "#9d63ff"), "#9d63ff"),
        "secondary_color": normalize_color(get_setting("app_secondary_color", "#5f35c7"), "#5f35c7"),
        "accent_color": normalize_color(get_setting("app_accent_color", "#7ec8ff"), "#7ec8ff"),
        "support_channel": get_setting("app_support_channel", ""),
        "telegram_link": get_setting("app_telegram_link", ""),
        "crypto_bot_link": get_setting("app_crypto_bot_link", "https://t.me/CryptoBot"),
        "landing_text": get_setting("app_landing_text", LANDING_MESSAGE),
        "notifications_enabled": parse_bool(get_setting("app_notifications_enabled", "true"), True),
    }


def save_uploaded_asset(upload, category: str) -> str:
    suffix = Path(upload.filename).suffix.lower()
    if suffix not in UPLOAD_EXTENSIONS:
        raise ValueError("Недопустимый формат изображения")
    filename = secure_filename(f"{category}_{uuid.uuid4().hex}{suffix}")
    target = UPLOADS_DIR / filename
    upload.save(target)
    return f"/uploads/{filename}"


def log_admin_action(admin_id: int, action_type: str, target_type: str, target_id: str = "", details: dict | None = None) -> None:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO admin_logs(admin_id, action_type, target_type, target_id, details, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (admin_id, action_type, target_type, target_id, json.dumps(details or {}, ensure_ascii=False), now_iso()),
        )


def get_admin_logs(limit: int = 30) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT l.*, u.username, u.first_name
            FROM admin_logs l
            LEFT JOIN users u ON u.telegram_id = l.admin_id
            ORDER BY l.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    items = []
    for row in rows:
        try:
            details = json.loads(row["details"])
        except json.JSONDecodeError:
            details = {"raw": row["details"]}
        items.append(
            {
                "id": row["id"],
                "admin_id": row["admin_id"],
                "admin_label": row["username"] or row["first_name"] or f"ID {row['admin_id']}",
                "action_type": row["action_type"],
                "target_type": row["target_type"],
                "target_id": row["target_id"],
                "details": details,
                "created_at": row["created_at"],
            }
        )
    return items


def get_recent_broadcasts(limit: int = 10) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT b.*, u.username, u.first_name
            FROM broadcasts b
            LEFT JOIN users u ON u.telegram_id = b.created_by
            ORDER BY b.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    items = []
    for row in rows:
        try:
            buttons = json.loads(row["buttons_json"])
        except json.JSONDecodeError:
            buttons = []
        items.append(
            {
                "id": row["id"],
                "image_path": row["image_path"],
                "text_content": row["text_content"],
                "buttons": buttons,
                "recipient_count": row["recipient_count"],
                "status": row["status"],
                "created_by_label": row["username"] or row["first_name"] or f"ID {row['created_by']}",
                "created_at": row["created_at"],
                "sent_at": row["sent_at"],
            }
        )
    return items


def get_admin_notifications() -> list[dict]:
    cutoff = (now_utc() - timedelta(days=1)).isoformat()
    with get_db() as conn:
        pending_withdrawals = conn.execute("SELECT COUNT(*) AS total FROM withdrawals WHERE status = 'pending_review'").fetchone()["total"]
        pending_deposits = conn.execute("SELECT COUNT(*) AS total FROM invoices WHERE status IN ('active', 'paid')").fetchone()["total"]
        new_users = conn.execute("SELECT COUNT(*) AS total FROM users WHERE created_at >= ?", (cutoff,)).fetchone()["total"]
        bonus_activations = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM bonus_claims WHERE created_at >= ?)
                + (SELECT COUNT(*) FROM promo_claims WHERE created_at >= ?) AS total
            """,
            (cutoff, cutoff),
        ).fetchone()["total"]
    notices = []
    if pending_withdrawals:
        notices.append({"level": "danger", "text": f"{pending_withdrawals} новых заявок на вывод"})
    if pending_deposits:
        notices.append({"level": "success", "text": f"{pending_deposits} новых пополнений"})
    if new_users:
        notices.append({"level": "info", "text": f"{new_users} новых пользователей"})
    if bonus_activations:
        notices.append({"level": "bonus", "text": f"{bonus_activations} активаций бонусов"})
    return notices


def get_admin_stats() -> dict:
    with get_db() as conn:
        users_total = conn.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"]
        bet_count = conn.execute("SELECT COUNT(*) AS total FROM bets").fetchone()["total"]
        deposit_sum = conn.execute("SELECT COALESCE(SUM(CAST(amount AS REAL)), 0) AS total FROM invoices WHERE status = 'paid'").fetchone()["total"]
        withdrawal_sum = conn.execute("SELECT COALESCE(SUM(CAST(amount AS REAL)), 0) AS total FROM withdrawals WHERE status = 'completed'").fetchone()["total"]
    return {
        "users_total": users_total,
        "bet_count": bet_count,
        "deposit_sum": format_amount(deposit_sum),
        "withdrawal_sum": format_amount(withdrawal_sum),
        "broadcast_recipients": users_total,
    }


def parse_broadcast_buttons(raw_buttons: object) -> list[dict]:
    if raw_buttons in (None, ""):
        return []
    if isinstance(raw_buttons, str):
        try:
            raw_buttons = json.loads(raw_buttons)
        except json.JSONDecodeError as exc:
            raise ValueError("Кнопки должны быть JSON-массивом") from exc
    if not isinstance(raw_buttons, list):
        raise ValueError("Кнопки должны быть списком")
    buttons = []
    for item in raw_buttons[:8]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        url = str(item.get("url", "")).strip()
        if text and url:
            buttons.append({"text": text[:32], "url": url[:256]})
    return buttons


def build_inline_markup(buttons: list[dict]) -> types.InlineKeyboardMarkup | None:
    if not buttons:
        return None
    markup = types.InlineKeyboardMarkup()
    for button in buttons:
        markup.add(types.InlineKeyboardButton(text=button["text"], url=button["url"]))
    return markup


def send_broadcast(admin_id: int, text_content: str, buttons: list[dict], image_path: str | None = None) -> dict:
    with get_db() as conn:
        users = conn.execute("SELECT telegram_id FROM users").fetchall()
        recipient_ids = [int(row["telegram_id"]) for row in users]
        cursor = conn.execute(
            """
            INSERT INTO broadcasts(image_path, text_content, buttons_json, recipient_count, status, created_by, created_at)
            VALUES(?, ?, ?, ?, 'sending', ?, ?)
            """,
            (image_path, text_content, json.dumps(buttons, ensure_ascii=False), len(recipient_ids), admin_id, now_iso()),
        )
        broadcast_id = cursor.lastrowid
    sent = 0
    failed = 0
    reply_markup = build_inline_markup(buttons)
    for recipient_id in recipient_ids:
        try:
            if image_path:
                full_image_url = image_path if image_path.startswith("http") else f"{WEBHOOK_BASE_URL}{image_path}"
                bot.send_photo(recipient_id, full_image_url, caption=text_content, reply_markup=reply_markup)
            else:
                bot.send_message(recipient_id, text_content, reply_markup=reply_markup, disable_web_page_preview=False)
            sent += 1
        except Exception:
            failed += 1
            logger.exception("Failed to send broadcast to %s", recipient_id)
    status = "sent" if failed == 0 else "partial"
    with get_db() as conn:
        conn.execute("UPDATE broadcasts SET status = ?, sent_at = ? WHERE id = ?", (status, now_iso(), broadcast_id))
    log_admin_action(admin_id, "broadcast_send", "broadcast", str(broadcast_id), {"sent": sent, "failed": failed})
    return {"id": broadcast_id, "recipient_count": len(recipient_ids), "sent": sent, "failed": failed, "status": status}


def build_main_keyboard() -> types.ReplyKeyboardMarkup:
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(
        types.KeyboardButton(
            text=BOT_WEBAPP_BUTTON_TEXT,
            web_app=types.WebAppInfo(url=WEBAPP_URL),
        )
    )
    return keyboard


def verify_init_data(init_data_raw: str) -> dict:
    if not init_data_raw:
        raise ValueError("Пустой initData")

    parsed = dict(parse_qsl(init_data_raw, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise ValueError("hash отсутствует")

    auth_date = int(parsed.get("auth_date", "0"))
    if not auth_date:
        raise ValueError("auth_date отсутствует")
    if time.time() - auth_date > INIT_DATA_TTL:
        raise ValueError("initData устарели")

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode("utf-8"), hashlib.sha256).digest()
    calculated = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        raise ValueError("initData не прошли проверку")

    user_payload = parsed.get("user")
    if not user_payload:
        raise ValueError("Данные пользователя отсутствуют")

    user = json.loads(user_payload)
    if "id" not in user:
        raise ValueError("ID пользователя отсутствует")
    return user


def get_request_init_data() -> str:
    if request.method == "GET":
        return request.headers.get("X-Telegram-Init-Data", "")
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        if payload.get("initData"):
            return str(payload["initData"])
    return request.headers.get("X-Telegram-Init-Data", "") or request.form.get("initData", "")


def auth_required(admin_only: bool = False):
    def decorator(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            try:
                user = verify_init_data(get_request_init_data())
            except Exception as exc:
                return jsonify({"ok": False, "error": str(exc)}), 401

            g.tg_user = user
            upsert_user(user)
            if admin_only and not is_admin(int(user["id"])):
                return jsonify({"ok": False, "error": "Нет доступа"}), 403
            process_pending_invoices()
            return func(*args, **kwargs)

        return wrapped

    return decorator


def upsert_user(user: dict) -> None:
    timestamp = now_iso()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO users(telegram_id, username, first_name, last_name, balance, created_at, updated_at)
            VALUES(?, ?, ?, ?, '0', ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                updated_at = excluded.updated_at
            """,
            (
                int(user["id"]),
                user.get("username"),
                user.get("first_name"),
                user.get("last_name"),
                timestamp,
                timestamp,
            ),
        )


def get_user_record(telegram_id: int) -> sqlite3.Row | None:
    with get_db() as conn:
        return conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()


def get_balance(telegram_id: int) -> Decimal:
    row = get_user_record(telegram_id)
    return Decimal(row["balance"]) if row else Decimal("0")


def send_bot_message(chat_id: str | int, text: str, **kwargs) -> None:
    try:
        bot.send_message(chat_id, text, **kwargs)
    except Exception:
        logger.exception("Failed to send bot message to %s", chat_id)


def send_bet_log(message: str) -> None:
    if BET_LOG_CHAT_ID:
        send_bot_message(BET_LOG_CHAT_ID, message)


def crypto_api_request(method: str, payload: dict | None = None) -> dict:
    if not CRYPTO_PAY_API_TOKEN:
        raise RuntimeError("CRYPTO_PAY_API_TOKEN is not configured")

    response = requests.post(
        f"{CRYPTO_PAY_BASE_URL}/{method}",
        json=payload or {},
        headers={"Crypto-Pay-API-Token": CRYPTO_PAY_API_TOKEN},
        timeout=REQUESTS_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("error", "Crypto Pay API error"))
    return data["result"]


def create_crypto_invoice(telegram_id: int, amount: Decimal) -> dict:
    payment_settings = get_payment_settings()
    if not payment_settings["deposits_enabled"]:
        raise ValueError("Пополнения временно отключены")
    payload = {
        "asset": CRYPTO_PAY_ASSET,
        "amount": format_amount(amount),
        "description": CRYPTO_PAY_INVOICE_DESCRIPTION,
        "payload": f"deposit:{telegram_id}:{uuid.uuid4().hex}",
        "allow_comments": False,
        "allow_anonymous": False,
    }
    result = crypto_api_request("createInvoice", payload)
    pay_url = (
        result.get("mini_app_invoice_url")
        or result.get("bot_invoice_url")
        or result.get("web_app_invoice_url")
        or ""
    )
    timestamp = now_iso()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO invoices(
                telegram_id, crypto_invoice_id, invoice_hash, amount, asset, pay_url, status, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                telegram_id,
                int(result["invoice_id"]),
                result["hash"],
                format_amount(amount),
                CRYPTO_PAY_ASSET,
                pay_url,
                result["status"],
                timestamp,
                timestamp,
            ),
        )
    return result


def fetch_pending_invoices() -> list[sqlite3.Row]:
    with get_db() as conn:
        return conn.execute(
            """
            SELECT * FROM invoices
            WHERE status IN ('active', 'created')
            ORDER BY id ASC
            """
        ).fetchall()


def credit_invoice(invoice_row: sqlite3.Row, remote_invoice: dict) -> None:
    telegram_id = int(invoice_row["telegram_id"])
    amount = Decimal(invoice_row["amount"])
    credited_at = now_iso()
    new_balance = Decimal("0")

    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        fresh = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_row["id"],)).fetchone()
        if not fresh or fresh["credited_at"]:
            conn.commit()
            return

        user = conn.execute("SELECT balance FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        current_balance = Decimal(user["balance"]) if user else Decimal("0")
        new_balance = quant(current_balance + amount)
        conn.execute(
            "UPDATE users SET balance = ?, updated_at = ? WHERE telegram_id = ?",
            (format_amount(new_balance), credited_at, telegram_id),
        )
        conn.execute(
            "UPDATE invoices SET status = ?, credited_at = ?, updated_at = ? WHERE id = ?",
            (remote_invoice["status"], credited_at, credited_at, invoice_row["id"]),
        )
        conn.commit()

    send_bot_message(
        telegram_id,
        f"Пополнение подтверждено: +{format_amount(amount)} {BALANCE_CURRENCY}. "
        f"Баланс: {format_amount(new_balance)} {BALANCE_CURRENCY}.",
    )


def process_pending_invoices() -> None:
    pending = fetch_pending_invoices()
    if not pending or not CRYPTO_PAY_API_TOKEN:
        return

    invoice_ids = ",".join(str(row["crypto_invoice_id"]) for row in pending)
    try:
        remote_invoices = crypto_api_request("getInvoices", {"invoice_ids": invoice_ids})
    except Exception:
        logger.exception("Failed to sync CryptoBot invoices")
        return

    invoice_items = remote_invoices.get("items", []) if isinstance(remote_invoices, dict) else remote_invoices
    indexed = {int(item["invoice_id"]): item for item in invoice_items}
    for row in pending:
        remote = indexed.get(int(row["crypto_invoice_id"]))
        if not remote:
            continue
        if remote["status"] == "paid" and not row["credited_at"]:
            credit_invoice(row, remote)
        elif remote["status"] != row["status"]:
            with get_db() as conn:
                conn.execute(
                    "UPDATE invoices SET status = ?, updated_at = ? WHERE id = ?",
                    (remote["status"], now_iso(), row["id"]),
                )


def get_recent_bets(telegram_id: int, limit: int = 10) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT amount, color, multiplier, winning_number, winning_color, payout, status, created_at
            FROM bets
            WHERE telegram_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (telegram_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def get_bonus_list(telegram_id: int) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                b.id,
                b.title,
                b.channel_ref,
                b.reward_amount,
                b.image_path,
                b.is_active,
                EXISTS(
                    SELECT 1 FROM bonus_claims c
                    WHERE c.bonus_id = b.id AND c.telegram_id = ?
                ) AS claimed
            FROM bonus_channels b
            ORDER BY b.id DESC
            """,
            (telegram_id,),
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["claimed"] = bool(item["claimed"])
        item["is_active"] = bool(item["is_active"])
        items.append(item)
    return items


def get_admin_bonus_list() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, title, channel_ref, reward_amount, image_path, is_active, created_at
            FROM bonus_channels
            ORDER BY id DESC
            """
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["is_active"] = bool(item["is_active"])
        items.append(item)
    return items


def get_promo_daily_stats(telegram_id: int) -> dict:
    limit = get_promo_daily_limit()
    day_start = now_utc().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    with get_db() as conn:
        count = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM promo_claims
            WHERE telegram_id = ? AND created_at >= ?
            """,
            (telegram_id, day_start),
        ).fetchone()["total"]
    count = int(count)
    return {
        "daily_limit": limit,
        "claimed_today": count,
        "remaining_today": max(0, limit - count),
    }


def has_required_deposit(telegram_id: int, deposit_days: int, deposit_amount: Decimal) -> bool:
    since = (now_utc() - timedelta(days=max(0, deposit_days))).replace(microsecond=0).isoformat()
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM invoices
            WHERE telegram_id = ?
              AND credited_at IS NOT NULL
              AND credited_at >= ?
              AND CAST(amount AS REAL) >= CAST(? AS REAL)
            LIMIT 1
            """,
            (telegram_id, since, format_amount(deposit_amount)),
        ).fetchone()
    return bool(row)


def get_promo_list(telegram_id: int) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                p.*,
                EXISTS(
                    SELECT 1 FROM promo_claims c
                    WHERE c.promo_id = p.id AND c.telegram_id = ?
                ) AS claimed
            FROM promo_codes p
            ORDER BY p.id DESC
            """,
            (telegram_id,),
        ).fetchall()

    items = []
    for row in rows:
        item = dict(row)
        item["claimed"] = bool(item["claimed"])
        item["is_active"] = bool(item["is_active"])
        item["deposit_required"] = bool(item["deposit_required"])
        item["available_activations"] = max(0, int(item["max_activations"]) - int(item["current_activations"]))
        items.append(item)
    return items


def get_admin_promo_list() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM promo_codes
            ORDER BY id DESC
            """
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["is_active"] = bool(item["is_active"])
        item["deposit_required"] = bool(item["deposit_required"])
        item["available_activations"] = max(0, int(item["max_activations"]) - int(item["current_activations"]))
        items.append(item)
    return items


def claim_bonus(telegram_id: int, bonus_id: int) -> Decimal:
    with get_db() as conn:
        bonus = conn.execute(
            "SELECT * FROM bonus_channels WHERE id = ? AND is_active = 1",
            (bonus_id,),
        ).fetchone()
    if not bonus:
        raise ValueError("Бонус не найден")

    try:
        member = bot.get_chat_member(bonus["channel_ref"], telegram_id)
    except Exception as exc:
        raise ValueError("Не удалось проверить подписку. Добавь бота админом в канал.") from exc

    if member.status in {"left", "kicked"}:
        raise ValueError("Подписка не найдена")

    reward = Decimal(bonus["reward_amount"])
    timestamp = now_iso()
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT 1 FROM bonus_claims WHERE bonus_id = ? AND telegram_id = ?",
            (bonus_id, telegram_id),
        ).fetchone()
        if existing:
            conn.rollback()
            raise ValueError("Этот бонус уже получен")

        balance_row = conn.execute("SELECT balance FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        current_balance = Decimal(balance_row["balance"]) if balance_row else Decimal("0")
        new_balance = quant(current_balance + reward)

        conn.execute(
            "INSERT INTO bonus_claims(bonus_id, telegram_id, amount, created_at) VALUES(?, ?, ?, ?)",
            (bonus_id, telegram_id, format_amount(reward), timestamp),
        )
        conn.execute(
            "UPDATE users SET balance = ?, updated_at = ? WHERE telegram_id = ?",
            (format_amount(new_balance), timestamp, telegram_id),
        )
        conn.commit()
    log_admin_action(telegram_id, "bonus_claim", "bonus", str(bonus_id), {"reward": format_amount(reward)})
    return reward


def redeem_promo_code(telegram_id: int, code: str) -> Decimal:
    normalized = code.strip().upper()
    if not normalized:
        raise ValueError("Введи промокод")

    stats = get_promo_daily_stats(telegram_id)
    if stats["claimed_today"] >= stats["daily_limit"]:
        raise ValueError(f"Лимит промокодов на сегодня исчерпан: {stats['daily_limit']}")

    timestamp = now_iso()
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        promo = conn.execute(
            "SELECT * FROM promo_codes WHERE code = ?",
            (normalized,),
        ).fetchone()
        if not promo or not promo["is_active"]:
            conn.rollback()
            raise ValueError("Промокод не найден или отключен")
        if int(promo["current_activations"]) >= int(promo["max_activations"]):
            conn.rollback()
            raise ValueError("У промокода закончились активации")
        already = conn.execute(
            "SELECT 1 FROM promo_claims WHERE promo_id = ? AND telegram_id = ?",
            (promo["id"], telegram_id),
        ).fetchone()
        if already:
            conn.rollback()
            raise ValueError("Ты уже активировал этот промокод")

        deposit_required = bool(promo["deposit_required"])
        deposit_days = int(promo["deposit_days"])
        deposit_amount = Decimal(promo["deposit_amount"])
        if deposit_required and not has_required_deposit(telegram_id, deposit_days, deposit_amount):
            conn.rollback()
            raise ValueError(
                f"Нужен депозит от {format_amount(deposit_amount)} {BALANCE_CURRENCY} за последние {deposit_days} дн."
            )

        reward = Decimal(promo["reward_amount"])
        balance_row = conn.execute("SELECT balance FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        current_balance = Decimal(balance_row["balance"]) if balance_row else Decimal("0")
        new_balance = quant(current_balance + reward)

        conn.execute(
            "INSERT INTO promo_claims(promo_id, telegram_id, amount, created_at) VALUES(?, ?, ?, ?)",
            (promo["id"], telegram_id, format_amount(reward), timestamp),
        )
        conn.execute(
            """
            UPDATE promo_codes
            SET current_activations = current_activations + 1
            WHERE id = ?
            """,
            (promo["id"],),
        )
        conn.execute(
            "UPDATE users SET balance = ?, updated_at = ? WHERE telegram_id = ?",
            (format_amount(new_balance), timestamp, telegram_id),
        )
        conn.commit()
    log_admin_action(telegram_id, "promo_redeem", "promo", str(promo["id"]), {"code": normalized, "reward": format_amount(reward)})
    return reward


def spin_roulette() -> tuple[int, str]:
    winning_number = secrets.randbelow(15) + 1
    return winning_number, WHEEL[winning_number]


def place_bet(telegram_id: int, color: str, amount: Decimal) -> dict:
    if color not in COLOR_MULTIPLIERS:
        raise ValueError("Неизвестный цвет ставки")

    winning_number, winning_color = spin_roulette()
    multiplier = COLOR_MULTIPLIERS[color]
    won = color == winning_color
    payout = quant(amount * multiplier) if won else Decimal("0")
    status = "win" if won else "lose"
    timestamp = now_iso()
    final_balance = Decimal("0")

    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT balance FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        current_balance = Decimal(row["balance"]) if row else Decimal("0")
        if current_balance < amount:
            conn.rollback()
            raise ValueError("Недостаточно средств на балансе")

        balance_after_bet = quant(current_balance - amount)
        final_balance = quant(balance_after_bet + payout)

        conn.execute(
            "UPDATE users SET balance = ?, updated_at = ? WHERE telegram_id = ?",
            (format_amount(final_balance), timestamp, telegram_id),
        )
        conn.execute(
            """
            INSERT INTO bets(
                telegram_id, amount, color, multiplier, winning_number, winning_color, payout, status, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                telegram_id,
                format_amount(amount),
                color,
                format_amount(multiplier),
                winning_number,
                winning_color,
                format_amount(payout),
                status,
                timestamp,
            ),
        )
        conn.commit()

    user = get_user_record(telegram_id)
    username = user["username"] or user["first_name"] or str(telegram_id)
    send_bet_log(
        f"Ставка на рулетку\n"
        f"Игрок: @{username}\n"
        f"Поставил: {format_amount(amount)} {BALANCE_CURRENCY} на {COLOR_LABELS[color]}\n"
        f"Выпало: {COLOR_LABELS[winning_color]} ({winning_number})\n"
        f"Результат: {'выигрыш' if won else 'проигрыш'}\n"
        f"Выплата: {format_amount(payout)} {BALANCE_CURRENCY}"
    )

    return {
        "winning_number": winning_number,
        "winning_color": winning_color,
        "selected_color": color,
        "amount": format_amount(amount),
        "payout": format_amount(payout),
        "won": won,
        "balance": format_amount(final_balance),
    }


def create_review_keyboard(withdrawal_id: int) -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton("Подтвердить", callback_data=f"wd:approve:{withdrawal_id}"),
        types.InlineKeyboardButton("Отклонить", callback_data=f"wd:reject:{withdrawal_id}"),
    )
    return keyboard


def perform_transfer(withdrawal_row: sqlite3.Row) -> int:
    result = crypto_api_request(
        "transfer",
        {
            "user_id": int(withdrawal_row["telegram_id"]),
            "asset": withdrawal_row["asset"],
            "amount": withdrawal_row["amount"],
            "spend_id": withdrawal_row["spend_id"],
            "comment": CRYPTO_PAY_TRANSFER_COMMENT,
        },
    )
    return int(result["transfer_id"])


def mark_withdrawal_completed(withdrawal_id: int, transfer_id: int, reviewer: int | None = None) -> None:
    with get_db() as conn:
        conn.execute(
            """
            UPDATE withdrawals
            SET status = 'completed', transfer_id = ?, reviewed_by = ?, updated_at = ?
            WHERE id = ?
            """,
            (transfer_id, reviewer, now_iso(), withdrawal_id),
        )


def refund_withdrawal(withdrawal_id: int, note: str, reviewer: int | None = None) -> None:
    timestamp = now_iso()
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        withdrawal = conn.execute("SELECT * FROM withdrawals WHERE id = ?", (withdrawal_id,)).fetchone()
        if not withdrawal or withdrawal["status"] in {"completed", "rejected"}:
            conn.rollback()
            return

        balance_row = conn.execute(
            "SELECT balance FROM users WHERE telegram_id = ?",
            (withdrawal["telegram_id"],),
        ).fetchone()
        current_balance = Decimal(balance_row["balance"]) if balance_row else Decimal("0")
        new_balance = quant(current_balance + Decimal(withdrawal["amount"]))

        conn.execute(
            "UPDATE users SET balance = ?, updated_at = ? WHERE telegram_id = ?",
            (format_amount(new_balance), timestamp, withdrawal["telegram_id"]),
        )
        conn.execute(
            """
            UPDATE withdrawals
            SET status = 'rejected', reviewed_by = ?, review_note = ?, updated_at = ?
            WHERE id = ?
            """,
            (reviewer, note, timestamp, withdrawal_id),
        )
        conn.commit()

    send_bot_message(
        int(withdrawal["telegram_id"]),
        f"Вывод #{withdrawal_id} отклонен. Сумма возвращена на баланс.",
    )


def request_withdrawal(telegram_id: int, amount: Decimal) -> dict:
    payment_settings = get_payment_settings()
    if not CRYPTO_PAY_API_TOKEN:
        raise ValueError("Интеграция CryptoBot не настроена")
    if not payment_settings["withdrawals_enabled"]:
        raise ValueError("Выводы временно отключены")

    timestamp = now_iso()
    spend_id = uuid.uuid4().hex
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        balance_row = conn.execute("SELECT balance FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        current_balance = Decimal(balance_row["balance"]) if balance_row else Decimal("0")
        if current_balance < amount:
            conn.rollback()
            raise ValueError("Недостаточно средств на балансе")

        new_balance = quant(current_balance - amount)
        status = "processing" if get_auto_payouts() else "pending_review"
        cursor = conn.execute(
            """
            INSERT INTO withdrawals(
                telegram_id, amount, asset, status, spend_id, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                telegram_id,
                format_amount(amount),
                CRYPTO_PAY_ASSET,
                status,
                spend_id,
                timestamp,
                timestamp,
            ),
        )
        withdrawal_id = int(cursor.lastrowid)
        conn.execute(
            "UPDATE users SET balance = ?, updated_at = ? WHERE telegram_id = ?",
            (format_amount(new_balance), timestamp, telegram_id),
        )
        conn.commit()
    log_admin_action(telegram_id, "withdraw_request", "withdrawal", str(withdrawal_id), {"amount": format_amount(amount), "auto": get_auto_payouts()})

    with get_db() as conn:
        withdrawal = conn.execute("SELECT * FROM withdrawals WHERE id = ?", (withdrawal_id,)).fetchone()

    if get_auto_payouts():
        try:
            transfer_id = perform_transfer(withdrawal)
            mark_withdrawal_completed(withdrawal_id, transfer_id)
            send_bot_message(
                telegram_id,
                f"Вывод #{withdrawal_id} выполнен автоматически: {format_amount(amount)} {BALANCE_CURRENCY}.",
            )
            return {"withdrawal_id": withdrawal_id, "status": "completed"}
        except Exception as exc:
            logger.exception("Auto payout failed")
            refund_withdrawal(withdrawal_id, f"auto_error:{exc}")
            raise ValueError(
                "Автовыплата не прошла. Средства возвращены на баланс. Проверь, что пользователь запускал @CryptoBot."
            ) from exc

    if not PAYOUT_REVIEW_CHAT_ID:
        refund_withdrawal(withdrawal_id, "manual_review_chat_not_configured")
        raise ValueError("Не настроен чат модерации выводов")

    user = get_user_record(telegram_id)
    username = user["username"] or user["first_name"] or str(telegram_id)
    message = bot.send_message(
        PAYOUT_REVIEW_CHAT_ID,
        (
            f"Новый вывод #{withdrawal_id}\n"
            f"Пользователь: @{username} ({telegram_id})\n"
            f"Сумма: {format_amount(amount)} {BALANCE_CURRENCY}\n"
            f"Asset: {CRYPTO_PAY_ASSET}"
        ),
        reply_markup=create_review_keyboard(withdrawal_id),
    )
    with get_db() as conn:
        conn.execute(
            """
            UPDATE withdrawals
            SET review_chat_id = ?, review_message_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (str(PAYOUT_REVIEW_CHAT_ID), int(message.message_id), now_iso(), withdrawal_id),
        )
    send_bot_message(telegram_id, f"Заявка на вывод #{withdrawal_id} отправлена на проверку администраторам.")
    return {"withdrawal_id": withdrawal_id, "status": "pending_review"}


def resolve_withdrawal(withdrawal_id: int, approve: bool, reviewer_id: int) -> str:
    with get_db() as conn:
        withdrawal = conn.execute("SELECT * FROM withdrawals WHERE id = ?", (withdrawal_id,)).fetchone()
    if not withdrawal:
        raise ValueError("Заявка не найдена")
    if withdrawal["status"] not in {"pending_review", "processing"}:
        return f"Заявка уже обработана: {withdrawal['status']}"

    if approve:
        try:
            transfer_id = perform_transfer(withdrawal)
            mark_withdrawal_completed(withdrawal_id, transfer_id, reviewer_id)
            log_admin_action(reviewer_id, "withdraw_approve", "withdrawal", str(withdrawal_id), {"transfer_id": transfer_id})
            send_bot_message(
                int(withdrawal["telegram_id"]),
                f"Вывод #{withdrawal_id} подтвержден и отправлен в CryptoBot.",
            )
            return f"Вывод #{withdrawal_id} подтвержден. Transfer ID: {transfer_id}"
        except Exception as exc:
            logger.exception("Manual payout failed")
            refund_withdrawal(withdrawal_id, f"manual_error:{exc}", reviewer_id)
            return f"Вывод #{withdrawal_id} не удалось отправить через CryptoBot. Средства возвращены пользователю."

    refund_withdrawal(withdrawal_id, "rejected_by_admin", reviewer_id)
    log_admin_action(reviewer_id, "withdraw_reject", "withdrawal", str(withdrawal_id), {})
    return f"Вывод #{withdrawal_id} отклонен. Средства возвращены."


def get_pending_withdrawals(limit: int = 20) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT w.*, u.username, u.first_name
            FROM withdrawals w
            LEFT JOIN users u ON u.telegram_id = w.telegram_id
            WHERE w.status IN ('pending_review', 'processing')
            ORDER BY w.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def set_bonus_active_state(bonus_id: int, is_active: bool) -> None:
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE bonus_channels SET is_active = ? WHERE id = ?",
            (1 if is_active else 0, bonus_id),
        )
        if cursor.rowcount == 0:
            raise ValueError("Бонус не найден")


def delete_bonus(bonus_id: int) -> None:
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        exists = conn.execute("SELECT 1 FROM bonus_channels WHERE id = ?", (bonus_id,)).fetchone()
        if not exists:
            conn.rollback()
            raise ValueError("Бонус не найден")
        conn.execute("DELETE FROM bonus_claims WHERE bonus_id = ?", (bonus_id,))
        conn.execute("DELETE FROM bonus_channels WHERE id = ?", (bonus_id,))
        conn.commit()


def set_promo_active_state(promo_id: int, is_active: bool) -> None:
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE promo_codes SET is_active = ? WHERE id = ?",
            (1 if is_active else 0, promo_id),
        )
        if cursor.rowcount == 0:
            raise ValueError("Промокод не найден")


def delete_promo(promo_id: int) -> None:
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        exists = conn.execute("SELECT 1 FROM promo_codes WHERE id = ?", (promo_id,)).fetchone()
        if not exists:
            conn.rollback()
            raise ValueError("Промокод не найден")
        conn.execute("DELETE FROM promo_claims WHERE promo_id = ?", (promo_id,))
        conn.execute("DELETE FROM promo_codes WHERE id = ?", (promo_id,))
        conn.commit()


def bootstrap_payload(telegram_id: int) -> dict:
    user = get_user_record(telegram_id)
    promo_stats = get_promo_daily_stats(telegram_id)
    payment_settings = get_payment_settings()
    visual_settings = get_app_visual_settings()
    return {
        "ok": True,
        "user": {
            "id": telegram_id,
            "username": user["username"] if user else None,
            "first_name": user["first_name"] if user else None,
            "balance": format_amount(Decimal(user["balance"]) if user else Decimal("0")),
            "currency": BALANCE_CURRENCY,
            "is_admin": is_admin(telegram_id),
        },
        "roulette": {
            "wheel_size": 15,
            "green_number": 15,
            "multipliers": {key: format_amount(value) for key, value in COLOR_MULTIPLIERS.items()},
            "min_bet_amount": format_amount(MIN_BET_AMOUNT),
        },
        "payments": {**payment_settings, "auto_payouts": get_auto_payouts()},
        "app_settings": visual_settings,
        "recent_bets": get_recent_bets(telegram_id),
        "bonuses": get_bonus_list(telegram_id),
        "promos": {
            "stats": promo_stats,
            "items": get_promo_list(telegram_id),
        },
        "admin": {
            "pending_withdrawals": get_pending_withdrawals(),
            "promo_daily_limit": get_promo_daily_limit(),
            "bonus_items": get_admin_bonus_list(),
            "promo_items": get_admin_promo_list(),
            "payment_settings": payment_settings,
            "app_settings": visual_settings,
            "notifications": get_admin_notifications(),
            "logs": get_admin_logs(),
            "stats": get_admin_stats(),
            "broadcasts": get_recent_broadcasts(),
        } if is_admin(telegram_id) else None,
    }


@bot.message_handler(commands=["start"])
def handle_start(message: types.Message) -> None:
    bot.send_message(message.chat.id, BOT_START_TEXT, reply_markup=build_main_keyboard())


@bot.message_handler(content_types=["web_app_data"])
def handle_web_app_data(message: types.Message) -> None:
    payload = message.web_app_data.data if message.web_app_data else "{}"
    bot.send_message(message.chat.id, f"{BOT_WEBAPP_RESPONSE_PREFIX}\n<code>{payload}</code>")


@bot.callback_query_handler(func=lambda call: call.data.startswith("wd:"))
def handle_withdraw_callbacks(call: types.CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Нет доступа", show_alert=True)
        return

    _, action, raw_id = call.data.split(":", 2)
    result_text = resolve_withdrawal(int(raw_id), approve=action == "approve", reviewer_id=call.from_user.id)
    bot.answer_callback_query(call.id, "Готово")
    try:
        bot.edit_message_text(result_text, chat_id=call.message.chat.id, message_id=call.message.message_id)
    except Exception:
        logger.exception("Failed to edit withdrawal review message")


@bot.message_handler(func=lambda _: True)
def handle_fallback(message: types.Message) -> None:
    bot.send_message(message.chat.id, BOT_FALLBACK_TEXT, reply_markup=build_main_keyboard())


@app.get("/")
def landing():
    process_pending_invoices()
    visual_settings = get_app_visual_settings()
    return jsonify({"ok": True, "message": visual_settings["landing_text"], "mini_app": WEBAPP_URL, "auto_payouts": get_auto_payouts()})


@app.get("/health")
def healthcheck():
    return jsonify({"ok": True, "time": now_iso()})


@app.get("/mini-app")
def mini_app():
    visual_settings = get_app_visual_settings()
    return render_template(
        "mini_app.html",
        config={
            "app_title": visual_settings["app_title"],
            "app_subtitle": visual_settings["app_subtitle"],
            "button_text": BOT_WEBAPP_BUTTON_TEXT,
            "currency": BALANCE_CURRENCY,
            "logo_path": visual_settings["logo_path"],
            "background_path": visual_settings["background_path"],
            "primary_color": visual_settings["primary_color"],
            "secondary_color": visual_settings["secondary_color"],
            "accent_color": visual_settings["accent_color"],
            "support_channel": visual_settings["support_channel"],
        },
    )


@app.get("/uploads/<path:filename>")
def uploaded_file(filename: str) -> Response:
    return send_from_directory(UPLOADS_DIR, filename)


@app.get("/api/bootstrap")
@auth_required()
def api_bootstrap():
    return jsonify(bootstrap_payload(int(g.tg_user["id"])))


@app.post("/api/bet")
@auth_required()
def api_bet():
    payload = request.get_json(silent=True) or {}
    try:
        amount = parse_amount(str(payload.get("amount", "")), minimum=MIN_BET_AMOUNT)
        result = place_bet(int(g.tg_user["id"]), str(payload.get("color", "")).lower(), amount)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "result": result})


@app.post("/api/deposit")
@auth_required()
def api_deposit():
    payload = request.get_json(silent=True) or {}
    payment_settings = get_payment_settings()
    try:
        amount = parse_amount(
            str(payload.get("amount", "")),
            minimum=Decimal(payment_settings["min_deposit_amount"]),
            maximum=Decimal(payment_settings["max_deposit_amount"]),
        )
        invoice = create_crypto_invoice(int(g.tg_user["id"]), amount)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify(
        {
            "ok": True,
            "invoice": {
                "invoice_id": invoice["invoice_id"],
                "status": invoice["status"],
                "pay_url": invoice.get("mini_app_invoice_url")
                or invoice.get("bot_invoice_url")
                or invoice.get("web_app_invoice_url"),
            },
        }
    )


@app.get("/api/deposit/<int:invoice_id>")
@auth_required()
def api_deposit_status(invoice_id: int):
    process_pending_invoices()
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT crypto_invoice_id, status, credited_at
            FROM invoices
            WHERE crypto_invoice_id = ? AND telegram_id = ?
            """,
            (invoice_id, int(g.tg_user["id"])),
        ).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "Инвойс не найден"}), 404
    return jsonify({"ok": True, "invoice": dict(row), "balance": format_amount(get_balance(int(g.tg_user["id"])))})


@app.post("/api/withdraw")
@auth_required()
def api_withdraw():
    payload = request.get_json(silent=True) or {}
    payment_settings = get_payment_settings()
    try:
        amount = parse_amount(
            str(payload.get("amount", "")),
            minimum=Decimal(payment_settings["min_withdraw_amount"]),
            maximum=Decimal(payment_settings["max_withdraw_amount"]),
        )
        result = request_withdrawal(int(g.tg_user["id"]), amount)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "result": result, "balance": format_amount(get_balance(int(g.tg_user["id"])))})


@app.post("/api/bonus/<int:bonus_id>/claim")
@auth_required()
def api_bonus_claim(bonus_id: int):
    try:
        reward = claim_bonus(int(g.tg_user["id"]), bonus_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "reward": format_amount(reward), "balance": format_amount(get_balance(int(g.tg_user["id"])))})


@app.post("/api/promo/redeem")
@auth_required()
def api_promo_redeem():
    payload = request.get_json(silent=True) or {}
    try:
        reward = redeem_promo_code(int(g.tg_user["id"]), str(payload.get("code", "")))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify(
        {
            "ok": True,
            "reward": format_amount(reward),
            "balance": format_amount(get_balance(int(g.tg_user["id"]))),
            "promos": {
                "stats": get_promo_daily_stats(int(g.tg_user["id"])),
                "items": get_promo_list(int(g.tg_user["id"])),
            },
        }
    )


@app.post("/api/admin/settings")
@auth_required(admin_only=True)
def api_admin_settings():
    payload = request.get_json(silent=True) or {}
    auto_payouts = parse_bool(payload.get("auto_payouts"))
    promo_daily_limit = max(0, int(payload.get("promo_daily_limit", get_promo_daily_limit())))
    set_setting("auto_payouts", "true" if auto_payouts else "false")
    set_setting("promo_daily_limit", str(promo_daily_limit))
    log_admin_action(int(g.tg_user["id"]), "settings_update", "core_settings", "", {"auto_payouts": auto_payouts, "promo_daily_limit": promo_daily_limit})
    return jsonify({"ok": True, "auto_payouts": get_auto_payouts(), "promo_daily_limit": get_promo_daily_limit()})


@app.post("/api/admin/bonuses")
@auth_required(admin_only=True)
def api_admin_bonuses():
    try:
        title = request.form.get("title", "").strip()
        channel_ref = request.form.get("channel_ref", "").strip()
        reward_amount = parse_amount(request.form.get("reward_amount", ""), minimum=Decimal("0.01"))
        is_active = parse_bool(request.form.get("is_active", "true"), True)
        if not title or not channel_ref:
            raise ValueError("Нужно указать название и канал")

        image_path = None
        upload = request.files.get("image")
        if upload and upload.filename:
            suffix = Path(upload.filename).suffix.lower()
            if suffix not in UPLOAD_EXTENSIONS:
                raise ValueError("Недопустимый формат изображения")
            filename = secure_filename(f"{uuid.uuid4().hex}{suffix}")
            target = UPLOADS_DIR / filename
            upload.save(target)
            image_path = f"/uploads/{filename}"

        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO bonus_channels(title, channel_ref, reward_amount, image_path, is_active, created_at)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (title, channel_ref, format_amount(reward_amount), image_path, 1 if is_active else 0, now_iso()),
            )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify({"ok": True, "bonuses": get_bonus_list(int(g.tg_user["id"]))})


@app.post("/api/admin/promos")
@auth_required(admin_only=True)
def api_admin_promos():
    payload = request.get_json(silent=True) or {}
    try:
        code = str(payload.get("code", "")).strip().upper()
        title = str(payload.get("title", "")).strip() or code
        reward_amount = parse_amount(str(payload.get("reward_amount", "")), minimum=Decimal("0.01"))
        max_activations = int(payload.get("max_activations", 0))
        if not code:
            raise ValueError("Укажи код промо")
        if max_activations <= 0:
            raise ValueError("Количество активаций должно быть больше нуля")

        deposit_required = parse_bool(payload.get("deposit_required"))
        deposit_days = int(payload.get("deposit_days", 0) or 0)
        deposit_amount = Decimal("0")
        if deposit_required:
            if deposit_days <= 0:
                raise ValueError("Укажи срок депозита в днях")
            deposit_amount = parse_amount(str(payload.get("deposit_amount", "")), minimum=Decimal("0.01"))

        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO promo_codes(
                    code, title, reward_amount, max_activations, current_activations,
                    deposit_required, deposit_days, deposit_amount, is_active, created_at
                ) VALUES(?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
                """,
                (
                    code,
                    title,
                    format_amount(reward_amount),
                    max_activations,
                    1 if deposit_required else 0,
                    deposit_days if deposit_required else 0,
                    format_amount(deposit_amount) if deposit_required else "0",
                    1 if parse_bool(payload.get("is_active"), True) else 0,
                    now_iso(),
                ),
            )
    except sqlite3.IntegrityError:
        return jsonify({"ok": False, "error": "Такой промокод уже существует"}), 400
    except (ValueError, InvalidOperation) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify({"ok": True, "promos": get_promo_list(int(g.tg_user["id"]))})


@app.post("/api/admin/withdrawals/<int:withdrawal_id>/resolve")
@auth_required(admin_only=True)
def api_admin_resolve_withdrawal(withdrawal_id: int):
    payload = request.get_json(silent=True) or {}
    approve = parse_bool(payload.get("approve"))
    try:
        message = resolve_withdrawal(withdrawal_id, approve=approve, reviewer_id=int(g.tg_user["id"]))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify(
        {
            "ok": True,
            "message": message,
            "pending_withdrawals": get_pending_withdrawals(),
        }
    )


@app.post("/api/admin/bonuses/<int:bonus_id>/toggle")
@auth_required(admin_only=True)
def api_admin_bonus_toggle(bonus_id: int):
    payload = request.get_json(silent=True) or {}
    try:
        set_bonus_active_state(bonus_id, parse_bool(payload.get("is_active"), True))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    return jsonify({"ok": True, "bonus_items": get_admin_bonus_list(), "bonuses": get_bonus_list(int(g.tg_user["id"]))})


@app.post("/api/admin/bonuses/<int:bonus_id>/delete")
@auth_required(admin_only=True)
def api_admin_bonus_delete(bonus_id: int):
    try:
        delete_bonus(bonus_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    return jsonify({"ok": True, "bonus_items": get_admin_bonus_list(), "bonuses": get_bonus_list(int(g.tg_user["id"]))})


@app.post("/api/admin/promos/<int:promo_id>/toggle")
@auth_required(admin_only=True)
def api_admin_promo_toggle(promo_id: int):
    payload = request.get_json(silent=True) or {}
    try:
        set_promo_active_state(promo_id, parse_bool(payload.get("is_active"), True))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    return jsonify({"ok": True, "promo_items": get_admin_promo_list(), "promos": get_promo_list(int(g.tg_user["id"]))})


@app.post("/api/admin/promos/<int:promo_id>/delete")
@auth_required(admin_only=True)
def api_admin_promo_delete(promo_id: int):
    try:
        delete_promo(promo_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    return jsonify({"ok": True, "promo_items": get_admin_promo_list(), "promos": get_promo_list(int(g.tg_user["id"]))})


@app.get("/api/admin/overview")
@auth_required(admin_only=True)
def api_admin_overview():
    admin_id = int(g.tg_user["id"])
    return jsonify(
        {
            "ok": True,
            "pending_withdrawals": get_pending_withdrawals(),
            "promo_daily_limit": get_promo_daily_limit(),
            "bonus_items": get_admin_bonus_list(),
            "promo_items": get_admin_promo_list(),
            "payment_settings": get_payment_settings(),
            "app_settings": get_app_visual_settings(),
            "notifications": get_admin_notifications(),
            "logs": get_admin_logs(),
            "stats": get_admin_stats(),
            "broadcasts": get_recent_broadcasts(),
            "user_id": admin_id,
        }
    )


@app.post("/api/admin/payments")
@auth_required(admin_only=True)
def api_admin_payments():
    try:
        method_title = request.form.get("method_title", "").strip() or "CryptoBot"
        min_deposit = parse_amount(request.form.get("min_deposit_amount", ""), minimum=Decimal("0.01"))
        max_deposit = parse_amount(request.form.get("max_deposit_amount", ""), minimum=min_deposit)
        min_withdraw = parse_amount(request.form.get("min_withdraw_amount", ""), minimum=Decimal("0.01"))
        max_withdraw = parse_amount(request.form.get("max_withdraw_amount", ""), minimum=min_withdraw)
        fee_percent = max(Decimal("0"), parse_decimal_setting(request.form.get("withdraw_fee_percent", "0"), Decimal("0")))
        deposits_enabled = parse_bool(request.form.get("deposits_enabled", "true"), True)
        withdrawals_enabled = parse_bool(request.form.get("withdrawals_enabled", "true"), True)

        updates = {
            "payment_method_title": method_title,
            "payment_min_deposit": format_amount(min_deposit),
            "payment_max_deposit": format_amount(max_deposit),
            "payment_min_withdraw": format_amount(min_withdraw),
            "payment_max_withdraw": format_amount(max_withdraw),
            "payment_withdraw_fee_percent": format_amount(fee_percent),
            "payment_deposits_enabled": "true" if deposits_enabled else "false",
            "payment_withdrawals_enabled": "true" if withdrawals_enabled else "false",
        }
        icon_upload = request.files.get("method_icon")
        if icon_upload and icon_upload.filename:
            updates["payment_method_icon_path"] = save_uploaded_asset(icon_upload, "payment")

        for key, value in updates.items():
            set_setting(key, value)
        log_admin_action(int(g.tg_user["id"]), "payment_update", "payment_settings", "", updates)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify({"ok": True, "payment_settings": get_payment_settings()})


@app.post("/api/admin/app-settings")
@auth_required(admin_only=True)
def api_admin_app_settings():
    try:
        updates = {
            "app_title_override": request.form.get("app_title", APP_TITLE).strip() or APP_TITLE,
            "app_subtitle_override": request.form.get("app_subtitle", APP_SUBTITLE).strip() or APP_SUBTITLE,
            "app_primary_color": normalize_color(request.form.get("primary_color", "#9d63ff"), "#9d63ff"),
            "app_secondary_color": normalize_color(request.form.get("secondary_color", "#5f35c7"), "#5f35c7"),
            "app_accent_color": normalize_color(request.form.get("accent_color", "#7ec8ff"), "#7ec8ff"),
            "app_support_channel": request.form.get("support_channel", "").strip(),
            "app_telegram_link": request.form.get("telegram_link", "").strip(),
            "app_crypto_bot_link": request.form.get("crypto_bot_link", "https://t.me/CryptoBot").strip() or "https://t.me/CryptoBot",
            "app_landing_text": request.form.get("landing_text", LANDING_MESSAGE).strip() or LANDING_MESSAGE,
            "app_notifications_enabled": "true" if parse_bool(request.form.get("notifications_enabled", "true"), True) else "false",
        }
        logo_upload = request.files.get("logo")
        background_upload = request.files.get("background")
        if logo_upload and logo_upload.filename:
            updates["app_logo_path"] = save_uploaded_asset(logo_upload, "logo")
        if background_upload and background_upload.filename:
            updates["app_background_path"] = save_uploaded_asset(background_upload, "background")

        for key, value in updates.items():
            set_setting(key, value)
        log_admin_action(int(g.tg_user["id"]), "app_settings_update", "app_settings", "", updates)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify({"ok": True, "app_settings": get_app_visual_settings()})


@app.post("/api/admin/broadcasts/send")
@auth_required(admin_only=True)
def api_admin_broadcast_send():
    try:
        text_content = request.form.get("text_content", "").strip()
        if not text_content:
            raise ValueError("Нужно указать текст рассылки")
        buttons = parse_broadcast_buttons(request.form.get("buttons_json", "[]"))
        image_upload = request.files.get("image")
        image_path = save_uploaded_asset(image_upload, "broadcast") if image_upload and image_upload.filename else None
        result = send_broadcast(int(g.tg_user["id"]), text_content, buttons, image_path)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "broadcast": result, "broadcasts": get_recent_broadcasts(), "stats": get_admin_stats()})


@app.post("/api/webhook")
def telegram_webhook():
    if WEBHOOK_SECRET_TOKEN:
        request_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if request_secret != WEBHOOK_SECRET_TOKEN:
            logger.warning("Webhook rejected because secret token does not match")
            return jsonify({"ok": False, "error": "unauthorized"}), 403

    update = types.Update.de_json(request.get_data(as_text=True))
    bot.process_new_updates([update])
    return jsonify({"ok": True})


def configure_webhook() -> None:
    if not WEBHOOK_BASE_URL:
        logger.info("WEBHOOK_BASE_URL is not set, skip webhook registration")
        return
    webhook_url = f"{WEBHOOK_BASE_URL}/api/webhook"
    bot.remove_webhook()
    bot.set_webhook(url=webhook_url, secret_token=WEBHOOK_SECRET_TOKEN or None)
    logger.info("Webhook configured: %s", webhook_url)


init_db()
configure_webhook()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
