# Roulette Telegram Bot + Mini App

Проект теперь включает:

- Mini App с рулеткой на 15 чисел
- внутренний баланс пользователя
- пополнение через CryptoBot invoice
- авто- или ручные выводы через CryptoBot transfer
- лог ставок в Telegram-группу
- бонусы за подписку на каналы
- промокоды с дневным лимитом и условиями по депозиту
- админ-панель внутри Mini App

## Логика рулетки

- всего 15 чисел
- `1 зеленое`
- `7 красных`
- `7 черных`
- множители:
- `красный x2`
- `черный x4`
- `зеленый x14`

## Что нужно настроить

1. Создай бота в `@BotFather`
2. Создай приложение в `@CryptoBot -> Crypto Pay -> Create App`
3. Включи `Transfers` в Security у Crypto Pay приложения
4. Добавь `ADMIN_IDS`
5. Добавь бота в канал/каналы, если хочешь проверку бонусов за подписку
6. Добавь бота в группу логов и в группу модерации выводов
7. При необходимости поменяй `DEFAULT_PROMO_DAILY_LIMIT` или редактируй лимит уже из админки

## Основные переменные окружения

Смотри полный список в `.env.example`.

Критичные:

- `BOT_TOKEN`
- `WEBHOOK_BASE_URL`
- `WEBAPP_URL`
- `CRYPTO_PAY_API_TOKEN`
- `ADMIN_IDS`
- `BET_LOG_CHAT_ID`
- `PAYOUT_REVIEW_CHAT_ID`

## Важные ограничения CryptoBot

- пользователь должен хотя бы один раз запустить `@CryptoBot`, иначе `transfer` может не пройти
- метод `transfer` нужно отдельно включить в Security у Crypto Pay приложения
- для автопополнений в проекте используется создание invoice и проверка их статуса через API

## Запуск

```bash
pip install -r requirements.txt
python app.py
```

Для WSGI:

```bash
gunicorn app:app
```
