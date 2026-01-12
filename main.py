import json
import telebot
import os
import re
import requests
import locale
import logging
import urllib.parse

from apscheduler.schedulers.background import BackgroundScheduler
from database import (
    create_tables,
    get_orders,
    get_all_orders,
    add_order,
    update_user_phone,
    update_order_status_in_db,
    delete_order_from_db,
    update_user_name,
    update_user_name,
    get_calculation_count,
    increment_calculation_count,
    check_user_subscription,
    update_user_subscription,
    delete_favorite_car,
    get_all_users,
    add_user,
)
from bs4 import BeautifulSoup
from io import BytesIO
from telebot import types
from dotenv import load_dotenv
from urllib.parse import urlparse, parse_qs
from utils import (
    generate_encar_photo_url,
    clean_number,
    get_customs_fees,
    calculate_age,
    calculate_age_for_customs,
    is_prokhodnaya_car,
    will_be_prokhodnaya_soon,
    format_number,
    get_customs_fees_manual,
    get_pan_auto_car_data,
)

CALCULATE_CAR_TEXT = "Рассчитать Автомобиль (Encar, KBChaCha)"
CHANNEL_USERNAME = "HYT_Trading"
BOT_TOKEN = os.getenv("BOT_TOKEN")

load_dotenv()
bot_token = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(bot_token)
bot.set_webhook("")


# Set locale for number formatting
locale.setlocale(locale.LC_ALL, "en_US.UTF-8")

# Storage for the last error message ID
last_error_message_id = {}

# global variables
car_data = {}
car_id_external = ""
total_car_price = 0
krw_rub_rate = 0
rub_to_krw_rate = 0
krw_to_rub_rate = 0  # Direct KRW to RUB conversion rate
usd_rate = 0
users = set()
user_data = {}

car_month = None
car_year = None

vehicle_id = None
vehicle_no = None

usd_to_krw_rate = 0
usd_to_rub_rate = 0

user_orders = {}
user_requests = {}  # Словарь для хранения информации о заявках клиентов
user_hp_pending = {}  # Словарь для хранения данных при запросе мощности (л.с.) у пользователя

# Шаги заполнения заявки
REQUEST_STEPS = {
    "car_type": "Какой тип авто вы ищете? (седан, кроссовер, внедорожник и т.д.)",
    "year": "Какой год выпуска вас интересует?",
    "mileage": "Какой максимальный пробег вас устроит?",
    "drive": "Какой привод вы предпочитаете? (передний, задний, полный)",
    "preferences": "Укажите ваши предпочтения (цвет, комплектация и т.д.):",
    "budget": "Какой у вас бюджет? (в рублях):",
    "region": "В каком регионе России вы находитесь?",
}

# Шаг, на котором находится пользователь
user_request_step = {}


################## КОД ДЛЯ СТАТУСОВ
# Храним заказы пользователей
pending_orders = {}
user_contacts = {}
user_names = {}

MANAGERS = [728438182, 5481346081, 455033439]
FREE_ACCESS_USERS = {728438182, 5481346081, 455033439}  # Дима,

ORDER_STATUSES = {
    "1": "🚗 Авто выкуплен (на базе)",
    "2": "🚢 Отправлен в порт г. Пусан на погрузку",
    "3": "🌊 В пути во Владивосток",
    "4": "🛃 Таможенная очистка",
    "5": "📦 Погрузка до МСК",
    "6": "🚛 Доставляется клиенту",
}


@bot.callback_query_handler(
    func=lambda call: call.data == "request_details" or call.data == "car_request"
)
def start_car_request(call):
    # Если это запрос на получение подробностей после расчета авто
    if call.data == "request_details":
        handle_car_request_after_calculation(call)
        return

    # Иначе обрабатываем стандартную заявку
    chat_id = call.message.chat.id

    # Очищаем данные предыдущей заявки, если она была
    user_requests[chat_id] = {}
    user_request_step[chat_id] = "car_type"  # Устанавливаем первый шаг

    # Создаем клавиатуру с кнопкой отмены
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add("Отмена")

    # Приветственное сообщение
    bot.send_message(
        chat_id,
        "Заполните мини-анкету, и мы подберем для вас лучшие варианты. "
        "Ответьте на несколько вопросов:",
        reply_markup=keyboard,
    )

    # Задаем первый вопрос
    msg = bot.send_message(chat_id, REQUEST_STEPS["car_type"])
    bot.register_next_step_handler(msg, process_car_request_step)


def handle_car_request_after_calculation(call):
    """Обработка заявки на автомобиль после расчета стоимости"""
    chat_id = call.message.chat.id

    # Сохраняем информацию о заявке
    if chat_id not in user_requests:
        user_requests[chat_id] = {}

    # Сохраняем ссылку на автомобиль и другие данные
    user_requests[chat_id]["car_link"] = car_data.get("link", "Ссылка не указана")
    user_requests[chat_id]["car_name"] = car_data.get("name", "Модель не указана")
    user_requests[chat_id]["car_price"] = car_data.get("car_price", "Цена не указана")

    # Запрашиваем ФИО
    msg = bot.send_message(chat_id, "Пожалуйста, введите ваше ФИО:")
    bot.register_next_step_handler(msg, process_fullname_for_car_request)


def process_fullname_for_car_request(message):
    """Обработка ФИО для заявки на автомобиль"""
    chat_id = message.chat.id
    fullname = message.text

    # Проверяем, не отменил ли пользователь заполнение заявки
    if fullname in ["Отмена", "Главное меню", "отмена", "главное меню"]:
        if chat_id in user_requests:
            del user_requests[chat_id]
        bot.send_message(
            chat_id,
            "Заявка отменена.",
            reply_markup=main_menu(),
        )
        return

    # Сохраняем ФИО
    user_requests[chat_id]["fullname"] = fullname

    # Запрашиваем номер телефона
    keyboard = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    button_phone = types.KeyboardButton(
        text="Поделиться номером телефона", request_contact=True
    )
    keyboard.add(button_phone)
    keyboard.add("Отмена")

    msg = bot.send_message(
        chat_id,
        "Пожалуйста, поделитесь своим номером телефона:",
        reply_markup=keyboard,
    )
    bot.register_next_step_handler(msg, process_phone_for_car_request)


def process_phone_for_car_request(message):
    """Обработка номера телефона для заявки на автомобиль"""
    chat_id = message.chat.id

    # Проверяем, не отменил ли пользователь заполнение заявки
    if message.text and message.text in [
        "Отмена",
        "Главное меню",
        "отмена",
        "главное меню",
    ]:
        if chat_id in user_requests:
            del user_requests[chat_id]
        bot.send_message(
            chat_id,
            "Заявка отменена.",
            reply_markup=main_menu(),
        )
        return

    if message.contact is not None:
        # Получаем номер телефона
        phone_number = message.contact.phone_number
        fullname = user_requests[chat_id].get("fullname", "Не указано")
        car_link = user_requests[chat_id].get("car_link", "Ссылка не указана")
        car_name = user_requests[chat_id].get("car_name", "Модель не указана")
        car_price = user_requests[chat_id].get("car_price", "Цена не указана")
        username = message.from_user.username or "Нет username"

        # Формируем сообщение для менеджеров
        manager_msg = (
            f"🚨 <b>НОВАЯ ЗАЯВКА НА АВТОМОБИЛЬ</b> 🚨\n\n"
            f"👤 Клиент: {fullname}\n"
            f"📱 Телефон: {phone_number}\n"
            f"👤 Telegram: @{username}\n\n"
            f"🚗 Автомобиль: {car_name}\n"
            f"💰 Стоимость: ₩{format_number(car_price)}\n"
            f"🔗 <a href='{car_link}'>Ссылка на автомобиль</a>\n\n"
            f"⚡ Клиент интересуется данным автомобилем и ожидает вашего звонка!"
        )

        # Отправляем уведомление всем менеджерам
        for manager_id in MANAGERS:
            try:
                bot.send_message(manager_id, manager_msg, parse_mode="HTML")
            except Exception as e:
                print(f"Ошибка отправки уведомления менеджеру {manager_id}: {e}")

        # Отправляем подтверждение пользователю
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("Главное меню")

        bot.send_message(
            chat_id,
            "Спасибо! Ваша заявка на автомобиль отправлена. Наш менеджер свяжется с вами в ближайшее время.",
            reply_markup=keyboard,
        )

        # Очищаем данные заявки
        if chat_id in user_requests:
            del user_requests[chat_id]
    else:
        # Если пользователь не отправил контакт, просим еще раз
        keyboard = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        button_phone = types.KeyboardButton(
            text="Поделиться номером телефона", request_contact=True
        )
        keyboard.add(button_phone)
        keyboard.add("Отмена")

        msg = bot.send_message(
            chat_id,
            "Для отправки заявки необходимо поделиться номером телефона.",
            reply_markup=keyboard,
        )
        bot.register_next_step_handler(msg, process_phone_for_car_request)


def process_car_request_step(message):
    """Обработка ответов на вопросы заявки"""
    chat_id = message.chat.id
    text = message.text

    # Проверяем, не хочет ли пользователь отменить заполнение заявки
    if text in ["Отмена", "Главное меню", "отмена", "главное меню"]:
        # Очищаем данные заявки
        if chat_id in user_requests:
            del user_requests[chat_id]
        if chat_id in user_request_step:
            del user_request_step[chat_id]

        # Возвращаем в главное меню
        bot.send_message(
            chat_id,
            "Заполнение заявки отменено.",
            reply_markup=main_menu(),
        )
        return

    # Если пользователь отправил что-то кроме текста, просим повторить
    if not text:
        msg = bot.send_message(chat_id, "Пожалуйста, введите текстовый ответ.")
        bot.register_next_step_handler(msg, process_car_request_step)
        return

    # Определяем текущий шаг
    current_step = user_request_step.get(chat_id)
    if not current_step or chat_id not in user_requests:
        # Если что-то пошло не так, начинаем заново
        start_new_request(message)
        return

    # Сохраняем ответ пользователя
    user_requests[chat_id][current_step] = text

    # Определяем следующий шаг
    steps = list(REQUEST_STEPS.keys())
    current_index = steps.index(current_step)

    if current_index < len(steps) - 1:
        # Если есть следующий шаг, переходим к нему
        next_step = steps[current_index + 1]
        user_request_step[chat_id] = next_step

        # Создаем клавиатуру с кнопкой отмены
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("Отмена")

        msg = bot.send_message(chat_id, REQUEST_STEPS[next_step], reply_markup=keyboard)
        bot.register_next_step_handler(msg, process_car_request_step)
    else:
        # Если это был последний шаг, переходим к запросу телефона
        finish_car_request(message)


def finish_car_request(message):
    """Завершение процесса заполнения заявки, запрос телефона"""
    chat_id = message.chat.id

    # Проверка, не отменил ли пользователь заполнение заявки
    if message.text in ["Отмена", "Главное меню", "отмена", "главное меню"]:
        # Очищаем данные заявки
        if chat_id in user_requests:
            del user_requests[chat_id]
        if chat_id in user_request_step:
            del user_request_step[chat_id]

        # Возвращаем в главное меню
        bot.send_message(
            chat_id,
            "Заполнение заявки отменено.",
            reply_markup=main_menu(),
        )
        return

    # Создаем кнопку для отправки номера телефона
    keyboard = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    button_phone = types.KeyboardButton(
        text="Отправить номер телефона", request_contact=True
    )
    keyboard.add(button_phone)
    keyboard.add("Отмена")  # Добавляем кнопку отмены

    msg = bot.send_message(
        chat_id,
        "Для завершения заявки, пожалуйста, поделитесь своим номером телефона:",
        reply_markup=keyboard,
    )
    bot.register_next_step_handler(msg, process_contact_for_request)


def process_contact_for_request(message):
    """Обработка полученного контакта и отправка заявки менеджерам"""
    chat_id = message.chat.id

    # Проверка, не отменил ли пользователь заполнение заявки
    if message.text in ["Отмена", "Главное меню", "отмена", "главное меню"]:
        # Очищаем данные заявки
        if chat_id in user_requests:
            del user_requests[chat_id]
        if chat_id in user_request_step:
            del user_request_step[chat_id]

        # Возвращаем в главное меню
        bot.send_message(
            chat_id,
            "Заполнение заявки отменено.",
            reply_markup=main_menu(),
        )
        return

    if message.contact is not None:
        # Получаем номер телефона
        phone_number = message.contact.phone_number
        user_name = message.from_user.first_name
        user_username = message.from_user.username or "Нет username"

        # Добавляем телефон к заявке
        user_requests[chat_id]["phone"] = phone_number

        # Формируем сообщение для менеджеров
        request_data = user_requests[chat_id]
        manager_msg = (
            f"📝 <b>НОВАЯ ЗАЯВКА НА ПОДБОР АВТО</b>\n\n"
            f"👤 От: {user_name} (@{user_username})\n"
            f"📱 Телефон: {phone_number}\n\n"
            f"🚗 Тип авто: {request_data.get('car_type', 'Не указано')}\n"
            f"📅 Год выпуска: {request_data.get('year', 'Не указано')}\n"
            f"🛣 Макс. пробег: {request_data.get('mileage', 'Не указано')} км\n"
            f"⚙️ Привод: {request_data.get('drive', 'Не указано')}\n"
            f"🎨 Предпочтения: {request_data.get('preferences', 'Не указано')}\n"
            f"💰 Бюджет: {request_data.get('budget', 'Не указано')} ₽\n"
            f"📍 Регион: {request_data.get('region', 'Не указано')}\n"
        )

        # Отправляем уведомление всем менеджерам
        for manager_id in MANAGERS:
            try:
                bot.send_message(manager_id, manager_msg, parse_mode="HTML")
            except Exception as e:
                print(f"Ошибка отправки уведомления менеджеру {manager_id}: {e}")

        # Отправляем подтверждение пользователю
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("Главное меню")

        bot.send_message(
            chat_id,
            "Спасибо! Мы получили вашу заявку и свяжемся с вами в ближайшее время.\n"
            "Менеджер: @HYT_TRADING_KR",
            reply_markup=keyboard,
        )

        # Очищаем данные заявки
        if chat_id in user_requests:
            del user_requests[chat_id]
        if chat_id in user_request_step:
            del user_request_step[chat_id]
    else:
        # Если пользователь не отправил контакт, просим еще раз
        keyboard = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        button_phone = types.KeyboardButton(
            text="Отправить номер телефона", request_contact=True
        )
        keyboard.add(button_phone)
        keyboard.add("Отмена")  # Добавляем кнопку отмены

        msg = bot.send_message(
            chat_id,
            "Для завершения заявки необходимо отправить контакт. Пожалуйста, нажмите на кнопку 'Отправить номер телефона'.",
            reply_markup=keyboard,
        )
        bot.register_next_step_handler(msg, process_contact_for_request)


def start_new_request(message):
    """Запуск нового процесса заполнения заявки"""
    chat_id = message.chat.id
    user_requests[chat_id] = {}
    user_request_step[chat_id] = "car_type"

    # Создаем клавиатуру с кнопкой отмены
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add("Отмена")

    bot.send_message(
        chat_id,
        "Заполните мини-анкету, и мы подберем для вас лучшие варианты. "
        "Ответьте на несколько вопросов:",
        reply_markup=keyboard,
    )

    msg = bot.send_message(chat_id, REQUEST_STEPS["car_type"])
    bot.register_next_step_handler(msg, process_car_request_step)


@bot.message_handler(commands=["stats"])
def show_stats(message):
    """Отображает статистику пользователей бота. Доступно только менеджерам."""
    user_id = message.from_user.id

    # Проверяем, является ли пользователь менеджером
    if user_id not in MANAGERS:
        bot.send_message(
            user_id,
            "⛔ У вас нет доступа к статистике. Эта функция доступна только менеджерам.",
        )
        return

    try:
        # Получаем список пользователей
        users = get_all_users()

        # Формируем статистику
        total_users = len(users)

        # Инициализируем сообщение здесь
        chunk_message = f"📊 <b>Статистика бота</b>\n\n"
        chunk_message += f"👥 Всего пользователей: <b>{total_users}</b>\n\n"

        # Список последних 10 пользователей
        if users:
            chunk_message += "<b>Последние 10 пользователей:</b>\n"
            for i, user in enumerate(users[:10], 1):
                username = user["username"] if user["username"] else "Нет username"
                name = f"{user['first_name']} {user['last_name'] or ''}".strip()
                reg_date = (
                    user["registered_at"].strftime("%d.%m.%Y %H:%M")
                    if user["registered_at"]
                    else "Неизвестно"
                )

                chunk_message += f"{i}. {name} (@{username})\n"
                chunk_message += f"   ID: {user['user_id']} | Дата: {reg_date}\n---------------------------------\n"
        else:
            chunk_message += "Пока нет зарегистрированных пользователей."

        bot.send_message(user_id, chunk_message, parse_mode="HTML")
    except Exception as e:
        bot.send_message(
            user_id, f"❌ Произошла ошибка при получении статистики: {str(e)}"
        )


@bot.callback_query_handler(func=lambda call: call.data.startswith("add_favorite_"))
def add_favorite_car(call):
    global car_data
    user_id = call.message.chat.id

    if not car_data or "name" not in car_data:
        bot.answer_callback_query(
            call.id, "🚫 Ошибка: Данные о машине отсутствуют.", show_alert=True
        )
        return

    # Проверяем, есть ли авто уже в избранном
    existing_orders = get_orders(user_id)
    if any(order["id"] == car_data.get("car_id") for order in existing_orders):
        bot.answer_callback_query(call.id, "✅ Этот автомобиль уже в избранном.")
        return

    # Получаем данные пользователя
    user = bot.get_chat(user_id)
    user_name = user.username if user.username else "Неизвестно"

    # Проверяем, есть ли сохранённый номер телефона пользователя
    phone_number = user_contacts.get(user_id, "Неизвестно")

    # Формируем объект заказа
    order_data = {
        "user_id": user_id,
        "car_id": car_data.get("car_id", "Нет ID"),
        "title": car_data.get("name", "Неизвестно"),
        "price": f"₩{format_number(car_data.get('car_price', 0))}",
        "link": car_data.get("link", "Нет ссылки"),
        "year": car_data.get("year", "Неизвестно"),
        "month": car_data.get("month", "Неизвестно"),
        "mileage": car_data.get("mileage", "Неизвестно"),
        "fuel": car_data.get("fuel", "Неизвестно"),
        "engine_volume": car_data.get("engine_volume", "Неизвестно"),
        "transmission": car_data.get("transmission", "Неизвестно"),
        "images": car_data.get("images", []),
        "status": "🔄 Не заказано",
        "total_cost_usd": car_data.get("total_cost_usd", 0),
        "total_cost_krw": car_data.get("total_cost_krw", 0),
        "total_cost_rub": car_data.get("total_cost_rub", 0),
        "user_name": user_name,  # ✅ Добавляем user_name
        "phone_number": phone_number,  # ✅ Добавляем phone_number (если нет, "Неизвестно")
    }

    # Логируем, чтобы проверить, какие данные отправляем в БД
    print(f"✅ Добавляем заказ: {order_data}")

    # Сохраняем в базу
    add_order(order_data)

    # Подтверждаем пользователю
    bot.answer_callback_query(
        call.id, "⭐ Автомобиль добавлен в избранное!", show_alert=True
    )


@bot.message_handler(commands=["my_cars"])
def show_favorite_cars(message):
    user_id = message.chat.id
    orders = get_orders(user_id)  # Берём заказы из БД

    if not orders:
        bot.send_message(user_id, "❌ У вас нет сохранённых автомобилей.")
        return

    for car in orders:
        car_id = car["car_id"]  # Используем car_id вместо id
        car_title = car["title"]
        car_status = car["status"]
        car_link = car["link"]
        car_year = car["year"]
        car_month = car["month"]
        car_mileage = car["mileage"]
        car_engine_volume = car["engine_volume"]
        car_transmission = car["transmission"]
        total_cost_krw = car["total_cost_krw"]
        total_cost_rub = car["total_cost_rub"]

        # Формируем текст сообщения
        response_text = (
            f"🚗 *{car_title} ({car_id})*\n\n"
            f"📅 {car_month}/{car_year} | ⚙️ {car_transmission}\n"
            f"🔢 Пробег: {car_mileage} | 🏎 Объём: {format_number(car_engine_volume)} cc\n\n"
            f"Стоимость авто под ключ:\n"
            f"₩{format_number(total_cost_krw)} | {format_number(total_cost_rub)} ₽\n\n"
            f"📌 *Статус:* {car_status}\n\n"
            f"[🔗 Ссылка на автомобиль]({car_link})\n\n"
            f"Консультация с менеджерами:\n\n"
            f"▪️ +82-10-7626-1999\n"
            f"▪️ +82-10-7934-6603\n"
        )

        # Создаём клавиатуру
        keyboard = types.InlineKeyboardMarkup()
        if car_status == "🔄 Не заказано":
            keyboard.add(
                types.InlineKeyboardButton(
                    f"📦 Заказать {car_title}",
                    callback_data=f"order_car_{car_id}",
                )
            )
        keyboard.add(
            types.InlineKeyboardButton(
                "❌ Удалить авто из списка", callback_data=f"delete_car_{car_id}"
            )
        )
        keyboard.add(
            types.InlineKeyboardButton(
                "Вернуться в главное меню", callback_data="main_menu"
            )
        )

        bot.send_message(
            user_id, response_text, parse_mode="Markdown", reply_markup=keyboard
        )


@bot.callback_query_handler(func=lambda call: call.data == "show_orders")
def callback_show_orders(call):
    """Обработчик кнопки 'Посмотреть список заказов'"""
    manager_id = call.message.chat.id
    print(f"📋 Менеджер {manager_id} нажал 'Посмотреть список заказов'")

    # ✅ Вызываем show_orders() с переданным сообщением из callback-запроса
    show_orders(call.message)


def notify_managers(order):
    """Отправляем информацию о заказе всем менеджерам"""
    print(f"📦 Отправляем заказ менеджерам: {order}")

    # Создаём клавиатуру с кнопкой "Посмотреть список заказов"
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton(
            "📋 Посмотреть список заказов", callback_data="show_orders"
        )
    )

    order_title = order.get("title", "Без названия")
    order_link = order.get("link", "#")
    user_name = order.get("user_name", "Неизвестный")
    user_id = order.get("user_id", None)
    phone_number = order.get("phone_number", "Не указан")

    user_mention = f"[{user_name}](tg://user?id={user_id})" if user_id else user_name

    message_text = (
        f"🚨 *Новый заказ!*\n\n"
        f"🚗 [{order_title}]({order_link})\n"
        f"👤 Заказчик: {user_mention}\n"
        f"📞 Контакт: {phone_number}\n"
        f"📌 *Статус:* 🕒 Ожидает подтверждения\n"
    )

    for manager_id in MANAGERS:
        bot.send_message(
            manager_id,
            message_text,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )


@bot.callback_query_handler(func=lambda call: call.data.startswith("order_car_"))
def order_car(call):
    user_id = call.message.chat.id
    car_id = call.data.split("_")[-1]

    # Получаем авто из базы
    user_orders = get_orders(user_id)
    order_found = None

    for order in user_orders:
        if str(order["car_id"]) == str(car_id):
            order_found = order
            break
        else:
            print(f"❌ Автомобиль {car_id} не совпадает с {order['car_id']}")

    if not order_found:
        print(f"❌ Ошибка: авто {car_id} не найдено в базе!")
        bot.send_message(user_id, "❌ Ошибка: автомобиль не найден.")
        return

    # ✅ Проверяем, есть ли ФИО у пользователя
    if user_id not in user_names:
        print(f"📝 Запрашиваем ФИО у {user_id}")
        bot.send_message(
            user_id,
            "📝 Введите ваше *ФИО* для оформления заказа:",
            parse_mode="Markdown",
        )

        # Сохраняем ID заказа в `pending_orders`
        pending_orders[user_id] = car_id
        return

    # ✅ Если ФИО уже есть, проверяем телефон
    if user_id not in user_contacts:
        print(f"📞 Запрашиваем телефон у {user_id}")
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        button = types.KeyboardButton("📞 Отправить номер", request_contact=True)
        markup.add(button)

        bot.send_message(
            user_id,
            "📲 Для оформления заказа, пожалуйста, отправьте номер телефона, "
            "на который зарегистрирован WhatsApp или Telegram.",
            reply_markup=markup,
        )

        # Сохраняем ID заказа в `pending_orders`
        pending_orders[user_id] = car_id
        return

    # ✅ Если ФИО и телефон уже есть → обновляем заказ
    phone_number = user_contacts[user_id]
    full_name = user_names[user_id]

    update_order_status(car_id, "🕒 Ожидает подтверждения")
    update_order_status_in_db(order_found["id"], "🕒 Ожидает подтверждения")

    bot.send_message(
        user_id,
        f"✅ Ваш заказ на {order_found['title']} оформлен!\n"
        f"📌 Статус: 🕒 Ожидает подтверждения\n"
        f"📞 Контакт для связи: {phone_number}\n"
        f"👤 ФИО: {full_name}",
        callback_data="show_orders",
    )

    # ✅ Добавляем ФИО в заказ перед отправкой менеджерам
    order_found["user_name"] = full_name
    notify_managers(order_found)


# Обработчик получения номера телефона
@bot.message_handler(content_types=["contact"])
def handle_contact(message):
    if not message.contact or not message.contact.phone_number:
        bot.send_message(user_id, "❌ Ошибка: номер телефона не передан.")
        return

    user_id = message.chat.id
    phone_number = message.contact.phone_number

    # Сохраняем номер телефона
    user_contacts[user_id] = phone_number
    bot.send_message(user_id, f"✅ Ваш номер {phone_number} сохранён!")

    # Проверяем, есть ли ожидаемый заказ
    if user_id not in pending_orders:
        bot.send_message(user_id, "✅ Ваш номер сохранён, но активного заказа нет.")
        return

    if user_id in pending_orders:
        car_id = pending_orders[user_id]  # Берём car_id из `pending_orders`
        print(f"📦 Пользователь {user_id} подтвердил заказ автомобиля {car_id}")

        # Получаем заказанное авто из базы
        user_orders = get_orders(user_id)
        order_found = None

        for order in user_orders:
            if str(order["car_id"]).strip() == str(car_id).strip():
                order_found = order
                break

        if not order_found:
            bot.send_message(user_id, "❌ Ошибка: автомобиль не найден в базе данных.")
            return

        # Добавляем `user_id` в order_found, если его нет
        order_found["user_id"] = user_id
        order_found["phone_number"] = (
            phone_number  # ✅ Сохраняем номер телефона в заказе
        )

        print(
            f"🛠 Обновляем телефон {phone_number} для user_id={user_id}, order_id={order_found['id']}"
        )
        update_user_phone(user_id, phone_number, order_found["id"])
        update_order_status_in_db(order_found["id"], "🕒 Ожидает подтверждения")

        bot.send_message(
            user_id,
            f"✅ Ваш заказ на {order_found['title']} оформлен!\n"
            f"📌 Статус: 🕒 Ожидает подтверждения\n"
            f"📞 Контакт: {phone_number}",
        )

        notify_managers(order_found)


@bot.message_handler(
    func=lambda message: not message.text.startswith("/")
    and message.chat.id in pending_orders
)
def handle_full_name(message):
    user_id = message.chat.id
    full_name = message.text.strip()

    # ❌ Если ФИО пустое, просим ввести заново
    if not full_name:
        bot.send_message(
            user_id, "❌ ФИО не может быть пустым. Введите ваше ФИО ещё раз:"
        )
        return

    # ✅ Сохраняем ФИО
    user_names[user_id] = full_name
    bot.send_message(user_id, f"✅ Ваше ФИО '{full_name}' сохранено!")

    # Проверяем, есть ли ожидаемый заказ
    car_id = pending_orders[user_id]  # Берём car_id из `pending_orders`
    print(
        f"📦 Пользователь {user_id} подтвердил заказ автомобиля {car_id} с ФИО {full_name}"
    )

    # Получаем заказанное авто из базы
    user_orders = get_orders(user_id)
    order_found = next(
        (
            order
            for order in user_orders
            if str(order["car_id"]).strip() == str(car_id).strip()
        ),
        None,
    )

    if not order_found:
        bot.send_message(user_id, "❌ Ошибка: автомобиль не найден в базе данных.")
        return

    # ✅ Обновляем статус заказа и добавляем ФИО в БД
    import hashlib

    def convert_car_id(car_id):
        if car_id.isdigit():
            return int(car_id)  # Если уже число, просто вернуть его
        else:
            return int(hashlib.md5(car_id.encode()).hexdigest(), 16) % (
                10**9
            )  # Преобразуем в число

    # Пример использования
    numeric_car_id = convert_car_id(car_id)

    update_order_status_in_db(order_found["id"], "🕒 Ожидает подтверждения")
    update_user_name(user_id, full_name)

    # ✅ Проверяем, есть ли уже телефон пользователя
    if user_id not in user_contacts:
        print(f"📞 Запрашиваем телефон у {user_id}")
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        button = types.KeyboardButton("📞 Отправить номер", request_contact=True)
        markup.add(button)

        bot.send_message(
            user_id,
            "📲 Теперь отправьте ваш *номер телефона*, на который зарегистрирован WhatsApp или Telegram.",
            reply_markup=markup,
            parse_mode="Markdown",
        )
        return  # Ждём телефон, дальше не идём

    # ✅ Если телефон уже есть → завершаем оформление
    phone_number = user_contacts[user_id]

    bot.send_message(
        user_id,
        f"✅ Ваш заказ на {order_found['title']} оформлен!\n"
        f"📌 Статус: 🕒 Ожидает подтверждения\n"
        f"📞 Контакт: {phone_number}\n"
        f"👤 ФИО: {full_name}",
    )

    # ✅ Отправляем информацию менеджерам
    order_found["user_name"] = full_name
    print(f"📦 Перед отправкой менеджерам заказ: {order_found}")  # Отладка
    notify_managers(order_found)

    # ✅ Удаляем `pending_orders`
    del pending_orders[user_id]


# Функция оформления заказа
def process_order(user_id, car_id, username, phone_number):
    # Достаём авто из списка
    car = next(
        (car for car in user_orders.get(user_id, []) if car["id"] == car_id), None
    )

    if not car:
        bot.send_message(user_id, "❌ Ошибка: автомобиль не найден.")
        return

    car_title = car.get("title", "Неизвестно")
    car_link = car.get("link", "Нет ссылки")

    # Менеджер, которому отправлять заявку
    manager_chat_id = MANAGERS[0]  # Здесь нужно указать ID менеджера

    # Сообщение менеджеру
    manager_text = (
        f"📢 *Новый заказ на автомобиль!*\n\n"
        f"🚗 {car_title}\n"
        f"🔗 [Ссылка на автомобиль]({car_link})\n\n"
        f"🔹 Username: @{username if username else 'Не указан'}\n"
        f"📞 Телефон: {phone_number if phone_number else 'Не указан'}\n"
    )

    bot.send_message(manager_chat_id, manager_text, parse_mode="Markdown")

    # Обновляем статус авто
    car["status"] = "🕒 Ожидает подтверждения"
    bot.send_message(
        user_id,
        f"✅ Ваш заказ на {car_title} оформлен! Менеджер скоро свяжется с вами.",
    )


@bot.message_handler(commands=["orders"])
def show_orders(message):
    manager_id = message.chat.id

    # Проверяем, является ли пользователь менеджером
    if manager_id not in MANAGERS:
        bot.send_message(manager_id, "❌ У вас нет доступа к заказам.")
        return

    # Загружаем все заказы из базы данных
    orders = get_all_orders()

    if not orders:
        bot.send_message(manager_id, "📭 Нет активных заказов.")
        return

    for idx, order in enumerate(orders, start=1):
        order_id = order.get("id", "Неизвестно")
        car_title = order.get("title", "Без названия")
        user_id = order.get("user_id")
        user_name = order.get("user_name", "Неизвестный")
        phone_number = order.get("phone_number", "Неизвестно")
        car_status = order.get("status", "🕒 Ожидает подтверждения")
        car_link = order.get("link", "#")
        car_id = order.get("car_id", "Неизвестно")

        if car_status == "🔄 Не заказано":
            car_status = "🕒 Ожидает подтверждения"

        user_mention = (
            f"[{user_name}](tg://user?id={user_id})" if user_id else user_name
        )

        response_text = (
            # f"📦 *Заказ #{idx}*\n"
            f"🚗 *{car_title}* (ID: {car_id})\n\n"
            f"👤 Заказчик: {user_mention}\n"
            f"📞 Телефон: *{phone_number}*\n\n"
            f"📌 *Статус:* {car_status}\n\n"
            f"[🔗 Ссылка на автомобиль]({car_link})"
        )

        # Создаем клавиатуру
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(
            types.InlineKeyboardButton(
                f"📌 Обновить статус ({car_title})",
                callback_data=f"update_status_{order_id}",
            ),
            types.InlineKeyboardButton(
                f"🗑 Удалить заказ ({car_title})",
                callback_data=f"delete_order_{order_id}",
            ),
        )
        keyboard.add(
            types.InlineKeyboardButton(
                "Вернуться в главное меню ", callback_data="main_menu"
            )
        )

        bot.send_message(
            manager_id,
            response_text,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )


@bot.callback_query_handler(func=lambda call: call.data.startswith("update_status_"))
def update_order_status(call):
    manager_id = call.message.chat.id
    order_id = call.data.split("_")[-1]  # ❗ Здесь приходит ID заказа, а не car_id

    print(f"🔍 Менеджер {manager_id} пытается обновить статус заказа {order_id}")

    # Получаем заказы из базы
    orders = get_all_orders()  # ✅ Загружаем все заказы
    # print(f"📦 Все заказы из базы: {orders}")  # Логируем заказы

    # 🛠 Теперь ищем по `id`, а не по `car_id`
    order_found = next(
        (order for order in orders if str(order["id"]) == str(order_id)), None
    )

    if not order_found:
        print(f"❌ Ошибка: заказ {order_id} не найден!")
        bot.answer_callback_query(call.id, "❌ Ошибка: заказ не найден.")
        return

    user_id = order_found["user_id"]
    car_id = order_found["car_id"]  # ✅ Берём car_id

    # 🔥 Генерируем кнопки статусов
    keyboard = types.InlineKeyboardMarkup()
    for status_code, status_text in ORDER_STATUSES.items():
        keyboard.add(
            types.InlineKeyboardButton(
                status_text,
                callback_data=f"set_status_{user_id}_{order_id}_{status_code}",
            )
        )

    bot.send_message(manager_id, "📌 Выберите новый статус:", reply_markup=keyboard)


@bot.callback_query_handler(func=lambda call: call.data.startswith("delete_car_"))
def delete_favorite_callback(call):
    user_id = call.message.chat.id
    car_id = call.data.split("_")[2]  # Получаем ID авто

    delete_favorite_car(user_id, car_id)  # Удаляем авто из БД

    bot.answer_callback_query(call.id, "✅ Авто удалено из списка!")
    bot.delete_message(
        call.message.chat.id, call.message.message_id
    )  # Удаляем сообщение с авто


@bot.callback_query_handler(func=lambda call: call.data.startswith("delete_order_"))
def delete_order(call):
    manager_id = call.message.chat.id
    order_id = call.data.split("_")[-1]

    print(f"🗑 Менеджер {manager_id} хочет удалить заказ {order_id}")

    # Удаляем заказ из базы
    delete_order_from_db(order_id)

    bot.answer_callback_query(call.id, "✅ Заказ удалён!")
    bot.send_message(manager_id, f"🗑 Заказ {order_id} успешно удалён.")


@bot.callback_query_handler(func=lambda call: call.data.startswith("set_status_"))
def set_new_status(call):
    manager_id = call.message.chat.id

    print(f"🔄 Получен `callback_data`: {call.data}")  # Логирование данных

    # Разбиваем callback_data
    _, _, user_id, order_id, status_code = call.data.split("_", 4)

    if not user_id.isdigit():
        print(f"❌ Ошибка: user_id некорректный: {user_id}")
        bot.answer_callback_query(call.id, "❌ Ошибка: неверный ID пользователя.")
        return

    user_id = int(user_id)

    # Проверяем статус
    if status_code not in ORDER_STATUSES:
        print(f"❌ Ошибка: неверный код статуса: {status_code}")
        bot.answer_callback_query(call.id, "❌ Ошибка: неверный статус.")
        return

    new_status = ORDER_STATUSES[status_code]  # Получаем текст статуса по коду

    print(
        f"🔄 Менеджер {manager_id} меняет статус заказа {order_id} для {user_id} на {new_status}"
    )

    # Получаем все заказы
    orders = get_all_orders()
    # print(f"📦 Все заказы пользователя {user_id}: {orders}")  # Логируем

    # 🛠 Ищем заказ по `id`
    order_found = next(
        (order for order in orders if str(order["id"]) == str(order_id)), None
    )

    if not order_found:
        print(f"❌ Ошибка: заказ {order_id} не найден!")
        bot.answer_callback_query(call.id, "❌ Ошибка: заказ не найден.")
        return

    # Обновляем статус заказа в БД
    update_order_status_in_db(order_id, new_status)

    # Уведомляем клиента
    bot.send_message(
        user_id,
        f"📢 *Обновление статуса заказа!*\n\n"
        f"🚗 [{order_found['title']}]({order_found['link']})\n"
        f"📌 Новый статус:\n*{new_status}*",
        parse_mode="Markdown",
    )

    # Подтверждаем менеджеру
    bot.answer_callback_query(call.id, f"✅ Статус обновлён на {new_status}!")

    # Обновляем заказы у менеджеров
    show_orders(call.message)


@bot.callback_query_handler(func=lambda call: call.data.startswith("place_order_"))
def place_order(call):
    user_id = call.message.chat.id
    order_id = call.data.split("_")[-1]

    # Проверяем, есть ли этот заказ
    if order_id not in user_orders:
        bot.answer_callback_query(call.id, "❌ Ошибка: заказ не найден.")
        return

    order = user_orders[order_id]

    # Создаём кнопку "Обновить статус" (только для менеджеров)
    keyboard = types.InlineKeyboardMarkup()
    if user_id in MANAGERS:
        keyboard.add(
            types.InlineKeyboardButton(
                "📌 Обновить статус", callback_data=f"update_status_{order_id}"
            )
        )

    bot.send_message(
        user_id,
        f"📢 *Заказ оформлен!*\n\n"
        f"🚗 [{order['title']}]({order['link']})\n"
        f"👤 Клиент: [{order['user_name']}](tg://user?id={order['user_id']})\n"
        f"📌 *Текущий статус:* {order['status']}",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )

    bot.answer_callback_query(call.id, "✅ Заказ отправлен менеджерам!")


################## КОД ДЛЯ СТАТУСОВ


@bot.callback_query_handler(func=lambda call: call.data == "check_subscription")
def check_subscription(call):
    user_id = call.from_user.id

    # Всегда считаем пользователя подписанным
    bot.answer_callback_query(
        call.id, "✅ Подписка оформлена! Вы можете продолжить расчёты."
    )
    # Установить подписку для пользователя в БД
    update_user_subscription(user_id, True)


@bot.callback_query_handler(func=lambda call: call.data == "check_subscription_calc")
def check_subscription_for_calculation(call):
    user_id = call.from_user.id
    
    if is_user_subscribed(user_id):
        bot.answer_callback_query(
            call.id, "✅ Подписка подтверждена! Теперь вы можете рассчитать стоимость автомобиля."
        )
        bot.send_message(
            call.message.chat.id,
            "Выберите способ расчета стоимости:",
            reply_markup=calculation_menu()
        )
        # Сохраняем статус подписки в БД
        update_user_subscription(user_id, True)
    else:
        bot.answer_callback_query(
            call.id, 
            "❌ Вы еще не подписались на канал. Пожалуйста, подпишитесь и попробуйте снова.", 
            show_alert=True
        )


def is_user_subscribed(user_id):
    """Проверяет, подписан ли пользователь на канал."""
    try:
        member = bot.get_chat_member(f"@{CHANNEL_USERNAME}", user_id)
        print(f"✅ Проверка подписки для пользователя {user_id}: статус = {member.status}")
        return member.status in ['member', 'administrator', 'creator']
    except telebot.apihelper.ApiTelegramException as e:
        error_message = str(e)
        print(f"❌ Ошибка проверки подписки для пользователя {user_id}: {error_message}")
        
        if "chat not found" in error_message.lower():
            print(f"⚠️ Канал @{CHANNEL_USERNAME} не найден или бот не имеет доступа")
        elif "user not found" in error_message.lower():
            print(f"⚠️ Пользователь {user_id} не найден в канале")
        elif "bot is not a member" in error_message.lower():
            print(f"⚠️ Бот не является администратором канала @{CHANNEL_USERNAME}")
        
        return False
    except Exception as e:
        print(f"❌ Неожиданная ошибка при проверке подписки: {e}")
        return False


def show_subscription_prompt(chat_id):
    """Показывает сообщение с требованием подписаться на канал."""
    keyboard = types.InlineKeyboardMarkup()
    subscribe_button = types.InlineKeyboardButton(
        text="📢 Подписаться на канал",
        url=f"https://t.me/{CHANNEL_USERNAME}"
    )
    check_button = types.InlineKeyboardButton(
        text="✅ Проверить подписку",
        callback_data="check_subscription_calc"
    )
    keyboard.add(subscribe_button)
    keyboard.add(check_button)
    
    bot.send_message(
        chat_id,
        "⚠️ Для использования функции расчета стоимости автомобиля необходимо подписаться на наш канал:\n\n"
        f"👉 @{CHANNEL_USERNAME}\n\n"
        "После подписки нажмите кнопку «Проверить подписку»",
        reply_markup=keyboard
    )


def print_message(message):
    print("\n\n##############")
    print(f"{message}")
    print("##############\n\n")
    return None


# Функция для установки команд меню
def set_bot_commands():
    commands = []

    # Публичные команды для обычных пользователей
    commands.extend(
        [
            types.BotCommand("start", "Запустить бота"),
            types.BotCommand("my_cars", "Мои сохранённые автомобили"),
            types.BotCommand("orders", "Мои заказы"),
        ]
    )

    bot.set_my_commands(commands)


def get_rub_to_krw_rate():
    global rub_to_krw_rate, krw_to_rub_rate

    headers = {
        "accept": "application/json, text/javascript, */*; q=0.01",
        "accept-language": "en,ru;q=0.9,en-CA;q=0.8,la;q=0.7,fr;q=0.6,ko;q=0.5",
        "origin": "https://search.naver.com",
        "priority": "u=1, i",
        "referer": "https://search.naver.com/",
        "sec-ch-ua": '"Not;A=Brand";v="99", "Google Chrome";v="139", "Chromium";v="139"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
    }

    params = {
        "key": "calculator",
        "pkid": "141",
        "q": "환율",
        "where": "m",
        "u1": "keb",
        "u6": "standardUnit",
        "u7": "0",
        "u3": "RUB",
        "u4": "KRW",
        "u8": "down",
        "u2": "1",
    }

    try:
        response = requests.get(
            "https://m.search.naver.com/p/csearch/content/qapirender.nhn",
            params=params,
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()

        # Extract rate from NAVER response and subtract 0.4
        if data and "country" in data and len(data["country"]) >= 2:
            raw_rate = float(data["country"][1]["value"])
            rub_to_krw_rate = raw_rate - 0.4
            # Calculate direct KRW to RUB rate for conversions
            krw_to_rub_rate = 1 / rub_to_krw_rate
            print(f"Курс RUB → KRW: {rub_to_krw_rate} (с учетом -0.4)")
            print(f"Курс KRW → RUB: {krw_to_rub_rate}")
        else:
            raise ValueError("Invalid response format from NAVER API")

    except requests.RequestException as e:
        print(f"Ошибка при получении курса: {e}")
        return None
    except (ValueError, KeyError, IndexError) as e:
        print(f"Ошибка при обработке данных: {e}")
        return None


def get_usd_to_krw_rate():
    global usd_to_krw_rate

    # Сначала пробуем основной источник - NAVER API
    try:
        headers = {
            "accept": "application/json, text/javascript, */*; q=0.01",
            "accept-language": "en,ru;q=0.9,en-CA;q=0.8,la;q=0.7,fr;q=0.6,ko;q=0.5",
            "origin": "https://search.naver.com",
            "priority": "u=1, i",
            "referer": "https://search.naver.com/",
            "sec-ch-ua": '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        }

        params = {
            "key": "calculator",
            "pkid": "141",
            "q": "환율",
            "where": "m",
            "u1": "keb",
            "u6": "receive",
            "u7": "0",
            "u3": "USD",
            "u4": "KRW",
            "u8": "down",
            "u2": "1",
        }

        response = requests.get(
            "https://m.search.naver.com/p/csearch/content/qapirender.nhn",
            params=params,
            headers=headers,
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()

        # Извлекаем курс из ответа
        if "country" in data and len(data["country"]) >= 2:
            rate_text = data["country"][1]["value"]
            usd_to_krw = float(rate_text.replace(",", ""))
            usd_to_krw_rate = usd_to_krw
            print(f"✅ Курс USD → KRW (NAVER): {usd_to_krw_rate}")
            return

        print("⚠️ Не удалось извлечь курс из NAVER API — пробуем запасной источник")

    except Exception as e:
        print(f"❌ Ошибка при получении курса из NAVER: {e}")
        print("🔄 Переключаемся на запасной источник...")

    # Fallback - используем внешний API для курса USD→KRW
    try:
        fallback_url = "https://api.exchangerate-api.com/v4/latest/USD"
        response = requests.get(fallback_url, timeout=10)
        response.raise_for_status()
        data = response.json()

        if "KRW" in data["rates"]:
            usd_to_krw_rate = data["rates"]["KRW"]
            print(f"✅ Курс USD → KRW (ExchangeRate API): {usd_to_krw_rate}")
            return

    except Exception as e:
        print(f"❌ Ошибка при получении курса из запасного источника: {e}")

    # Если все источники недоступны, используем значение по умолчанию
    usd_to_krw_rate = 1400.0
    print(f"⚠️ Используется курс USD→KRW по умолчанию: {usd_to_krw_rate}")


def get_usd_to_rub_rate():
    global usd_to_rub_rate

    url = "https://www.cbr-xml-daily.ru/daily_json.js"

    try:
        response = requests.get(url)
        response.raise_for_status()  # Проверяем успешность запроса
        data = response.json()

        # Получаем курс USD → RUB из ЦБ РФ
        usd_to_rub = data["Valute"]["USD"]["Value"]
        usd_to_rub_rate = usd_to_rub

        print(f"Курс USD → RUB: {usd_to_rub_rate}")
    except requests.RequestException as e:
        print(f"Ошибка при получении курса USD → RUB: {e}")
        usd_to_rub_rate = 95.0  # Значение по умолчанию
    except Exception as e:
        print(f"Ошибка при парсинге курса USD → RUB: {e}")
        usd_to_rub_rate = 95.0  # Значение по умолчанию


def get_currency_rates():
    global usd_rate, usd_to_krw_rate, usd_to_rub_rate

    print_message("ПОЛУЧАЕМ КУРСЫ ВАЛЮТ")

    # Получаем курс USD → KRW
    get_usd_to_krw_rate()

    # Получаем курс USD → RUB
    get_usd_to_rub_rate()

    # Проверяем, что курсы получены корректно
    if usd_to_krw_rate is None:
        usd_to_krw_rate = 1400.0  # Значение по умолчанию
        print("⚠️ Используется курс USD→KRW по умолчанию")

    if usd_to_rub_rate is None:
        usd_to_rub_rate = 95.0  # Значение по умолчанию
        print("⚠️ Используется курс USD→RUB по умолчанию")

    rates_text = (
        f"USD → KRW: <b>{usd_to_krw_rate:.2f} ₩</b>\n"
        f"USD → RUB: <b>{usd_to_rub_rate:.2f} ₽</b>"
    )

    return rates_text


# Обработчик команды /cbr
@bot.message_handler(commands=["exchange_rates"])
def cbr_command(message):
    try:
        rates_text = get_currency_rates()

        # Создаем клавиатуру с кнопкой для расчета автомобиля
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(
            types.InlineKeyboardButton(
                "Рассчитать стоимость автомобиля", callback_data="calculate_another"
            )
        )

        # Отправляем сообщение с курсами и клавиатурой
        bot.send_message(
            message.chat.id, rates_text, reply_markup=keyboard, parse_mode="HTML"
        )
    except Exception as e:
        bot.send_message(
            message.chat.id, "Не удалось получить курсы валют. Попробуйте позже."
        )
        print(f"Ошибка при получении курсов валют: {e}")


# Main menu creation function
def main_menu():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    keyboard.add(types.KeyboardButton("Гид по покупке авто"))
    keyboard.add(types.KeyboardButton("Расчёт стоимости авто"))
    keyboard.add(types.KeyboardButton("Заказать автомобиль / Оставить заявку"))
    return keyboard


# Submenu for cost calculation
def calculation_menu():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    keyboard.add(types.KeyboardButton("Рассчитать по ссылке"))
    keyboard.add(types.KeyboardButton("Расчёт вручную"))
    keyboard.add(types.KeyboardButton("Вернуться в главное меню"))
    return keyboard


# Start command handler
@bot.message_handler(commands=["start"])
def send_welcome(message):
    get_currency_rates()

    user_first_name = message.from_user.first_name
    welcome_message = (
        f"Здравствуйте, {user_first_name}!\n\n"
        "Я бот компании Quickxa. Я помогу вам рассчитать стоимость понравившегося вам автомобиля из Южной Кореи до стран СНГ.\n\n"
        "Выберите действие из меню ниже."
    )

    # Отправляем логотип компании (если файл существует)
    try:
        with open("logo.jpeg", "rb") as logo_file:
            bot.send_photo(
                message.chat.id,
                photo=logo_file,
            )
    except FileNotFoundError:
        # Если файл не найден, просто пропускаем отправку логотипа
        print("⚠️ Файл logo.jpeg не найден, пропускаем отправку логотипа")
    except Exception as e:
        print(f"⚠️ Ошибка при отправке логотипа: {e}")

    # Добавляем пользователя в базу данных
    add_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
        message.from_user.last_name,
    )

    # Отправляем приветственное сообщение
    bot.send_message(message.chat.id, welcome_message, reply_markup=main_menu())


# Error handling function
def send_error_message(message, error_text):
    global last_error_message_id

    # Remove previous error message if it exists
    if last_error_message_id.get(message.chat.id):
        try:
            bot.delete_message(message.chat.id, last_error_message_id[message.chat.id])
        except Exception as e:
            logging.error(f"Error deleting message: {e}")

    # Send new error message and store its ID
    error_message = bot.reply_to(message, error_text, reply_markup=main_menu())
    last_error_message_id[message.chat.id] = error_message.id
    logging.error(f"Error sent to user {message.chat.id}: {error_text}")


def get_car_info(url):
    global car_id_external, vehicle_no, vehicle_id, car_year, car_month

    if "fem.encar.com" in url:
        car_id_match = re.findall(r"\d+", url)
        car_id = car_id_match[0]
        car_id_external = car_id

        url = f"https://api.encar.com/v1/readside/vehicle/{car_id}"

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Referer": "http://www.encar.com/",
            "Cache-Control": "max-age=0",
            "Connection": "keep-alive",
        }

        response = requests.get(url, headers=headers).json()

        # Информация об автомобиле
        car_make = response["category"]["manufacturerEnglishName"]  # Марка
        car_model = response["category"]["modelGroupEnglishName"]  # Модель
        car_trim = response["category"]["gradeDetailEnglishName"] or ""  # Комплектация

        car_title = f"{car_make} {car_model} {car_trim}"  # Заголовок

        # Получаем все необходимые данные по автомобилю
        car_price = str(response["advertisement"]["price"])
        car_date = response["category"]["yearMonth"]
        year = car_date[2:4]
        month = car_date[4:]
        car_year = year
        car_month = month

        # Пробег (форматирование)
        mileage = response["spec"]["mileage"]
        formatted_mileage = f"{mileage:,} км"

        # Тип КПП
        transmission = response["spec"]["transmissionName"]
        formatted_transmission = "Автомат" if "오토" in transmission else "Механика"

        car_engine_displacement = str(response["spec"]["displacement"])
        car_type = response["spec"]["bodyName"]

        # Список фотографий (берем первые 10)
        car_photos = [
            generate_encar_photo_url(photo["path"]) for photo in response["photos"][:10]
        ]
        car_photos = [url for url in car_photos if url]

        # Дополнительные данные
        vehicle_no = response["vehicleNo"]
        vehicle_id = response["vehicleId"]

        # Форматируем
        formatted_car_date = f"01{month}{year}"
        formatted_car_type = "crossover" if car_type == "SUV" else "sedan"

        print_message(
            f"ID: {car_id}\nType: {formatted_car_type}\nDate: {formatted_car_date}\nCar Engine Displacement: {car_engine_displacement}\nPrice: {car_price} KRW"
        )

        return [
            car_price,
            car_engine_displacement,
            formatted_car_date,
            car_title,
            formatted_mileage,
            formatted_transmission,
            car_photos,
            year,
            month,
            car_type,
        ]
    elif "kbchachacha.com" in url:
        url = f"https://www.kbchachacha.com/public/car/detail.kbc?carSeq={car_id_external}"

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "en,ru;q=0.9,en-CA;q=0.8,la;q=0.7,fr;q=0.6,ko;q=0.5",
            "Connection": "keep-alive",
        }

        response = requests.get(url=url, headers=headers)
        soup = BeautifulSoup(response.text, "html.parser")

        # Находим JSON в <script type="application/ld+json">
        json_script = soup.find("script", {"type": "application/ld+json"})
        if json_script:
            json_data = json.loads(json_script.text.strip())

            # Извлекаем данные
            car_name = json_data.get("name", "Неизвестная модель")
            car_images = json_data.get("image", [])[:10]  # Берем первые 10 фото
            car_price = json_data.get("offers", {}).get("price", "Не указано")

            # Находим таблицу с информацией
            table = soup.find("table", {"class": "detail-info-table"})
            if table:
                rows = table.find_all("tr")

                # Достаём данные
                car_number = None
                car_year = None
                car_mileage = None
                car_fuel = None
                car_engine_displacement = None
                car_type = None

                for row in rows:
                    headers = row.find_all("th")
                    values = row.find_all("td")

                    for th, td in zip(headers, values):
                        header_text = th.text.strip()
                        value_text = td.text.strip()

                        if header_text == "차량정보":  # Номер машины
                            car_number = value_text
                        elif header_text == "연식":  # Год выпуска
                            car_year = value_text
                        elif header_text == "주행거리":  # Пробег
                            car_mileage = value_text
                        elif header_text == "연료":  # Топливо
                            car_fuel = value_text
                        elif header_text == "배기량":  # Объем двигателя
                            car_engine_displacement = value_text
                        elif header_text == "차종토" or header_text == "차종":
                            car_type = value_text
            else:
                print("❌ Таблица информации не найдена")

            # Проверяем, есть ли объем двигателя, и если нет или он равен 0, извлекаем его из названия авто
            if (
                not car_engine_displacement
                or car_engine_displacement == "0cc"
                or car_engine_displacement == "0"
            ):
                # Ищем числа с десятичной точкой (например, 3.0) в названии автомобиля
                engine_volume_match = re.search(r"(\d+\.\d+)", car_name)
                if engine_volume_match:
                    # Извлекаем значение объема и умножаем на 1000 для перевода в кубические сантиметры
                    engine_volume_liters = float(engine_volume_match.group(1))
                    car_engine_displacement = (
                        str(int(engine_volume_liters * 1000)) + "cc"
                    )
                    print(
                        f"Извлечен объем двигателя из названия авто: {car_engine_displacement}"
                    )

            car_info = {
                "name": car_name,
                "car_price": car_price,
                "images": car_images,
                "number": car_number,
                "year": car_year,
                "mileage": car_mileage,
                "fuel": car_fuel,
                "engine_volume": car_engine_displacement,
                "transmission": "오토",
                "type": car_type,
            }

            return car_info
        else:
            print(
                "❌ Не удалось найти JSON-данные в <script type='application/ld+json'>"
            )


# Function to calculate the total cost
def calculate_cost(link, message, hp_override=None, skip_increment=False):
    global car_data, car_id_external, car_month, car_year, krw_rub_rate, eur_rub_rate, rub_to_krw_rate, krw_to_rub_rate, usd_rate

    user_id = message.from_user.id

    # Увеличиваем счётчик расчётов (пропускаем если вызвано повторно после ввода HP)
    if not skip_increment:
        increment_calculation_count(user_id)

    print_message("ЗАПРОС НА РАСЧЁТ АВТОМОБИЛЯ")

    # Отправляем сообщение и сохраняем его ID
    processing_message = bot.send_message(message.chat.id, "Обрабатываю данные... ⏳")

    get_currency_rates()
    get_rub_to_krw_rate()

    is_manager = user_id in MANAGERS  # Check if user is a manager

    bot.send_message(
        message.chat.id,
        "✅ Подгружаю актуальный курс валют и делаю расчёты. ⏳ Пожалуйста подождите...",
        parse_mode="Markdown",
    )

    car_id = None
    car_title = ""
    car_type = None

    if "fem.encar.com" in link:
        car_id_match = re.findall(r"\d+", link)
        if car_id_match:
            car_id = car_id_match[0]  # Use the first match of digits
            car_id_external = car_id
            link = f"https://fem.encar.com/cars/detail/{car_id}"
        else:
            send_error_message(message, "🚫 Не удалось извлечь carid из ссылки.")
            return

    elif "kbchachacha.com" in link or "m.kbchachacha.com" in link:
        parsed_url = urlparse(link)
        query_params = parse_qs(parsed_url.query)
        car_id = query_params.get("carSeq", [None])[0]

        if car_id:
            car_id_external = car_id
            link = f"https://www.kbchachacha.com/public/car/detail.kbc?carSeq={car_id}"
        else:
            send_error_message(message, "🚫 Не удалось извлечь carSeq из ссылки.")
            return

    else:
        # Извлекаем carid с URL encar
        parsed_url = urlparse(link)
        query_params = parse_qs(parsed_url.query)
        car_id = query_params.get("carid", [None])[0]

    # Если ссылка с encar
    if "fem.encar.com" in link:
        result = get_car_info(link)
        (
            car_price,
            car_engine_displacement,
            formatted_car_date,
            car_title,
            formatted_mileage,
            formatted_transmission,
            car_photos,
            year,
            month,
            car_type,
        ) = result

        preview_link = f"https://fem.encar.com/cars/detail/{car_id}"
        car_type = car_type

    # Если ссылка с kbchacha
    if "kbchachacha.com" in link:
        result = get_car_info(link)

        car_title = result["name"]
        car_type = result["type"]
        match = re.search(r"(\d{2})년(\d{2})월", result["year"])
        if match:
            car_year = match.group(1)
            car_month = match.group(2)  # Получаем двухзначный месяц
        else:
            car_year = "Не найдено"
            car_month = "Не найдено"

        month = car_month
        year = car_year

        # Обработка объема двигателя - удаляем 'cc' суффикс если есть и конвертируем в число
        engine_vol = result["engine_volume"]
        if engine_vol:
            # Удаляем все нецифровые символы, включая 'cc'
            car_engine_displacement = re.sub(r"[^\d]", "", engine_vol)
            if not car_engine_displacement or car_engine_displacement == "0":
                # Если объем все равно не получен или равен 0, ищем в названии авто
                engine_volume_match = re.search(r"(\d+\.\d+)", car_title)
                if engine_volume_match:
                    engine_volume_liters = float(engine_volume_match.group(1))
                    car_engine_displacement = str(int(engine_volume_liters * 1000))
        else:
            # Если объем не указан, попробуем извлечь из названия авто
            engine_volume_match = re.search(r"(\d+\.\d+)", car_title)
            if engine_volume_match:
                engine_volume_liters = float(engine_volume_match.group(1))
                car_engine_displacement = str(int(engine_volume_liters * 1000))
            else:
                car_engine_displacement = "0"  # Значение по умолчанию

        car_price = int(result["car_price"]) / 10000
        formatted_car_date = f"01{car_month}{match.group(1)}"
        formatted_mileage = result["mileage"]
        formatted_transmission = (
            "Автомат" if "오토" in result["transmission"] else "Механика"
        )
        car_photos = result["images"]

        preview_link = (
            f"https://www.kbchachacha.com/public/car/detail.kbc?carSeq={car_id}"
        )

    if not car_price and car_engine_displacement and formatted_car_date:
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(
            types.InlineKeyboardButton(
                "Написать менеджеру", url="https://t.me/HYT_TRADING_KR"
            )
        )
        keyboard.add(
            types.InlineKeyboardButton(
                "Рассчитать стоимость другого автомобиля",
                callback_data="calculate_another",
            )
        )
        bot.send_message(
            message.chat.id, "Ошибка", parse_mode="Markdown", reply_markup=keyboard
        )
        bot.delete_message(message.chat.id, processing_message.message_id)
        return

    if car_price and car_engine_displacement and formatted_car_date:
        car_engine_displacement = int(car_engine_displacement)

        # Форматирование данных
        formatted_car_year = f"20{car_year}"
        engine_volume_formatted = f"{format_number(car_engine_displacement)} cc"

        # Расчет возраста с учетом будущего статуса "Проходная"
        age = calculate_age(int(formatted_car_year), car_month)
        customs_age, is_future_prokhodnaya, months_to_prokhodnaya = calculate_age_for_customs(
            int(formatted_car_year), car_month
        )
        
        # Проверяем текущий статус проходной/непроходной
        is_prokhodnaya = is_prokhodnaya_car(int(formatted_car_year), car_month)

        age_formatted = (
            "до 3 лет"
            if age == "0-3"
            else (
                "от 3 до 5 лет"
                if age == "3-5"
                else "от 5 до 7 лет" if age == "5-7" else "от 7 лет"
            )
        )

        print(f"CAR_TYPE: {car_type}")

        # Конвертируем стоимость авто в рубли (прямая конверсия)
        price_krw = int(car_price) * 10000
        price_usd = price_krw / usd_to_krw_rate  # Оставляем для расчета доставки в USD
        price_rub = price_krw * krw_to_rub_rate  # Прямая конверсия KRW → RUB

        # Инициализация переменных для таможенных платежей
        customs_fee = 0
        customs_duty = 0
        recycling_fee = 0
        hp_value = None

        # Если HP передан (после ввода пользователем), используем calcus.ru с этим HP
        if hp_override:
            response = get_customs_fees(
                car_engine_displacement,
                price_krw,
                int(formatted_car_year),
                car_month,
                engine_type=1,
                custom_age=customs_age,
                hp=hp_override,
            )
            customs_fee = clean_number(response.get("sbor", 0))
            customs_duty = clean_number(response.get("tax", 0))
            recycling_fee = clean_number(response.get("util", 0))
            hp_value = hp_override
        else:
            # Для encar.com: сначала пробуем получить данные с pan-auto.ru
            if "fem.encar.com" in link:
                pan_auto_data = get_pan_auto_car_data(car_id_external)

                if pan_auto_data and pan_auto_data.get('hp'):
                    # Используем таможенные значения с pan-auto.ru
                    customs_fee = int(pan_auto_data['clearanceCost'])
                    recycling_fee = int(pan_auto_data['utilizationFee'])
                    customs_duty = int(pan_auto_data['customsDuty'])
                    hp_value = pan_auto_data['hp']
                    print(f"Pan-Auto: HP={hp_value}, customs_fee={customs_fee}, customs_duty={customs_duty}, recycling_fee={recycling_fee}")
                else:
                    # HP не найден на pan-auto.ru - запрашиваем у пользователя
                    user_hp_pending[message.chat.id] = {
                        'type': 'link',
                        'link': link,
                        'message': message,
                        'car_title': car_title,
                        'car_type': car_type,
                        'car_engine_displacement': car_engine_displacement,
                        'formatted_car_date': formatted_car_date,
                        'formatted_car_year': formatted_car_year,
                        'car_month': car_month,
                        'car_year': car_year,
                        'formatted_mileage': formatted_mileage,
                        'formatted_transmission': formatted_transmission,
                        'car_photos': car_photos,
                        'preview_link': preview_link,
                        'car_id': car_id,
                    }
                    bot.delete_message(message.chat.id, processing_message.message_id)
                    bot.send_message(
                        message.chat.id,
                        "⚠️ Не удалось определить мощность автомобиля автоматически.\n\n"
                        "Пожалуйста, введите мощность двигателя в лошадиных силах (л.с.):",
                    )
                    return

            # Для kbchachacha.com: всегда запрашиваем HP у пользователя
            elif "kbchachacha.com" in link:
                user_hp_pending[message.chat.id] = {
                    'type': 'link',
                    'link': link,
                    'message': message,
                    'car_title': car_title,
                    'car_type': car_type,
                    'car_engine_displacement': car_engine_displacement,
                    'formatted_car_date': formatted_car_date,
                    'formatted_car_year': formatted_car_year,
                    'car_month': car_month,
                    'car_year': car_year,
                    'formatted_mileage': formatted_mileage,
                    'formatted_transmission': formatted_transmission,
                    'car_photos': car_photos,
                    'preview_link': preview_link,
                    'car_id': car_id,
                }
                bot.delete_message(message.chat.id, processing_message.message_id)
                bot.send_message(
                    message.chat.id,
                    "Пожалуйста, введите мощность двигателя в лошадиных силах (л.с.):",
                )
                return

        # Расчет стоимости брокерских услуг
        broker_fee = 100000.00  # Брокерские услуги (СВХ + СБКТС + лаборатория + перегон)

        # Расчет стоимости доставки
        delivery_fee = 850.00 if car_type == "SUV" else 750.00  # в долларах
        delivery_fee_rub = delivery_fee * usd_to_rub_rate  # конвертация в рубли

        # Расчет стоимости услуги дилера/аукциона
        dealer_fee_krw = 440000  # в вонах
        dealer_fee_usd = dealer_fee_krw / usd_to_krw_rate  # конвертация в доллары
        dealer_fee_rub = dealer_fee_krw * krw_to_rub_rate  # Прямая конверсия KRW → RUB

        # Расчет стоимости оформления и перевозки по Корее
        kr_documentation_fee_krw = 300000  # в вонах
        kr_documentation_fee_usd = (
            kr_documentation_fee_krw / usd_to_krw_rate
        )  # конвертация в доллары
        kr_documentation_fee_rub = (
            kr_documentation_fee_krw * krw_to_rub_rate
        )  # Прямая конверсия KRW → RUB

        # Расчет финальной стоимости автомобиля во Владивостоке
        total_cost_vladivostok = (
            price_rub  # стоимость авто
            + dealer_fee_rub  # услуги дилера/аукциона
            + kr_documentation_fee_rub  # оформление, снятие с учёта и перевозка по Корее
            + customs_duty  # таможенная пошлина
            + customs_fee  # таможенные сборы
            + recycling_fee  # утилизационный сбор
            + broker_fee  # брокерские услуги (СВХ + СБКТС + лаборатория + перегон)
            + delivery_fee_rub  # доставка паромом
        )

        # Доставка до других городов рассчитывается индивидуально

        # Сохраняем все данные в car_data для последующей передачи в детализацию
        car_data = {
            "name": car_title,
            "car_id": car_id,
            "year": year,
            "month": month,
            "mileage": formatted_mileage,
            "engine_volume": car_engine_displacement,
            "transmission": formatted_transmission,
            "car_price": price_krw,
            "link": preview_link,
            "images": car_photos,
            "total_cost_usd": price_usd,
            "total_cost_krw": price_krw,
            "total_cost_rub": total_cost_vladivostok,
            "usd_to_krw_rate": usd_to_krw_rate,
            "usd_to_rub_rate": usd_to_rub_rate,
        }

        # Сохраняем данные для расчета в глобальные переменные
        # total_cost_usd = price_usd
        # total_cost_krw = price_krw
        # total_cost_rub = total_cost_vladivostok

        # Определяем стоимость доставки в зависимости от типа авто
        delivery_fee_usd = 850 if car_type == "SUV" else 750
        # Определяем стоимость услуг дилера/аукциона
        dealer_fee_krw = 440000
        dealer_fee_usd = dealer_fee_krw / usd_to_krw_rate

        car_type_formatted = "Кроссовер" if car_type == "SUV" else "Легковой"

        # Формирование сообщения результата
        # Расчет общих сумм для Кореи
        korea_total_krw = price_krw + dealer_fee_krw + kr_documentation_fee_krw + (delivery_fee_usd * usd_to_krw_rate)
        korea_total_rub = price_rub + dealer_fee_rub + kr_documentation_fee_rub + delivery_fee_rub
        
        # Расчет общих расходов в России
        russia_total_expenses = customs_duty + customs_fee + recycling_fee + broker_fee
        
        # Формируем сообщение о статусе "Проходная"
        prokhodnaya_status = ""
        if is_prokhodnaya:
            prokhodnaya_status = "✅ Проходная (3-5 лет)"
        elif is_future_prokhodnaya:
            months_word = "месяц" if months_to_prokhodnaya == 1 else ("месяца" if months_to_prokhodnaya < 5 else "месяцев")
            prokhodnaya_status = f"⏳ Станет Проходной через {months_to_prokhodnaya} {months_word}\n📌 Расчет выполнен по ставке 3-5 лет"
        else:
            prokhodnaya_status = "❌ Непроходная"
        
        if message.from_user.id in MANAGERS:
            # Упрощенное сообщение для менеджеров по новому формату
            result_message = (
                f"📊 Расчёт автомобиля: {car_title}\n"
                f"◾️ Дата регистрации: {month}/{formatted_car_year}\n"
                f"🛣 Пробег: {formatted_mileage}\n"
                f"⚙️ Объём двигателя: {engine_volume_formatted}\n\n"
                f"🇰🇷 Расходы по Корее и логистика:\n"
                f"• В вонах: ₩{format_number(korea_total_krw)}\n"
                f"• В рублях: {format_number(korea_total_rub)} ₽\n\n"
                f"🇷🇺 Расходы в России: {format_number(russia_total_expenses)} ₽\n"
                f"   • Таможенная пошлина: {format_number(customs_duty)} ₽\n"
                f"   • Таможенные сборы: {format_number(customs_fee)} ₽\n"
                f"   • Утилизационный сбор: {format_number(recycling_fee)} ₽\n"
                f"   • Брокерские услуги: {format_number(broker_fee)} ₽\n\n"
                f"• Цена под ключ во Владивостоке: {format_number(total_cost_vladivostok)} ₽\n\n"
                f"🚚 Для доставки до вашего города уточняйте у менеджера\n\n"
                f"Ссылка на авто: {preview_link}\n\n"
            )
        else:
            # Упрощённое сообщение для клиентов по новому формату
            result_message = (
                f"📊 Расчёт автомобиля: {car_title}\n"
                f"◾️ Дата регистрации: {month}/{formatted_car_year}\n"
                f"🛣 Пробег: {formatted_mileage}\n"
                f"⚙️ Объём двигателя: {engine_volume_formatted}\n\n"
                f"🇰🇷 Расходы по Корее и логистика:\n"
                f"• В вонах: ₩{format_number(korea_total_krw)}\n"
                f"• В рублях: {format_number(korea_total_rub)} ₽\n\n"
                f"🇷🇺 Расходы в России: {format_number(russia_total_expenses)} ₽\n"
                f"   • Таможенная пошлина: {format_number(customs_duty)} ₽\n"
                f"   • Таможенные сборы: {format_number(customs_fee)} ₽\n"
                f"   • Утилизационный сбор: {format_number(recycling_fee)} ₽\n"
                f"   • Брокерские услуги: {format_number(broker_fee)} ₽\n\n"
                f"• Цена под ключ во Владивостоке: {format_number(total_cost_vladivostok)} ₽\n\n"
                f"🚚 Для доставки до вашего города уточняйте у менеджера\n\n"
                f"Ссылка на авто: {preview_link}\n\n"
                f"⚠️ Если данное авто попадает под санкции, уточните возможность отправки у наших менеджеров:\n\n"
                f"📱 +82-10-7626-1999\n"
                f"📱 +82-10-7934-6603\n"
                f"📢 <a href='https://t.me/HYT_Trading'>Официальный телеграм канал</a>"
            )

        # Клавиатура с дальнейшими действиями
        keyboard = types.InlineKeyboardMarkup()

        # Кнопка для добавления в избранное
        keyboard.add(
            types.InlineKeyboardButton(
                "⭐ Добавить в избранное",
                callback_data=f"add_favorite_{car_id_external}",
            )
        )

        if "fem.encar.com" in link:
            keyboard.add(
                types.InlineKeyboardButton(
                    "Технический Отчёт об Автомобиле", callback_data="technical_card"
                )
            )
            keyboard.add(
                types.InlineKeyboardButton(
                    "Выплаты по ДТП",
                    callback_data="technical_report",
                )
            )
        # keyboard.add(
        #     types.InlineKeyboardButton(
        #         "Написать менеджеру", url="https://t.me/HYT_TRADING_KR"
        #     )
        # )
        keyboard.add(
            types.InlineKeyboardButton(
                "Расчёт другого автомобиля",
                callback_data="calculate_another",
            )
        )
        keyboard.add(
            types.InlineKeyboardButton(
                "Главное меню",
                callback_data="main_menu",
            )
        )

        car_data["car_id"] = car_id
        car_data["name"] = car_title
        car_data["images"] = car_photos if isinstance(car_photos, list) else []
        car_data["link"] = preview_link
        car_data["year"] = year
        car_data["month"] = month
        car_data["mileage"] = formatted_mileage
        car_data["engine_volume"] = car_engine_displacement
        car_data["transmission"] = formatted_transmission
        car_data["car_price"] = price_krw
        car_data["user_name"] = message.from_user.username
        car_data["first_name"] = message.from_user.first_name
        car_data["last_name"] = message.from_user.last_name

        bot.send_message(
            message.chat.id,
            result_message,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

        # Если пользователь не менеджер, отправляем дополнительное сообщение с предложением оставить заявку
        if not is_manager:
            request_keyboard = types.InlineKeyboardMarkup()
            request_keyboard.add(
                types.InlineKeyboardButton(
                    "Оставить заявку", callback_data="request_details"
                )
            )

            bot.send_message(
                message.chat.id,
                "🔥 Хотите узнать точную стоимость и получить персональное предложение? Оставьте заявку прямо сейчас и наши специалисты подготовят для вас детальный расчёт со всеми скидками!",
                reply_markup=request_keyboard,
            )

        bot.delete_message(
            message.chat.id, processing_message.message_id
        )  # Удаляем сообщение о передаче данных в обработку

    else:
        send_error_message(
            message,
            "🚫 Произошла ошибка при получении данных. Проверьте ссылку и попробуйте снова.",
        )
        bot.delete_message(message.chat.id, processing_message.message_id)


# Function to get insurance total
def get_insurance_total():
    global car_id_external, vehicle_no, vehicle_id

    print_message("[ЗАПРОС] ТЕХНИЧЕСКИЙ ОТЧËТ ОБ АВТОМОБИЛЕ")

    formatted_vehicle_no = urllib.parse.quote(str(vehicle_no).strip())
    url = f"https://api.encar.com/v1/readside/record/vehicle/{str(vehicle_id)}/open?vehicleNo={formatted_vehicle_no}"

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Referer": "http://www.encar.com/",
            "Cache-Control": "max-age=0",
            "Connection": "keep-alive",
        }

        response = requests.get(url, headers)
        json_response = response.json()

        # Форматируем данные
        damage_to_my_car = json_response["myAccidentCost"]
        damage_to_other_car = json_response["otherAccidentCost"]

        print(
            f"Выплаты по представленному автомобилю: {format_number(damage_to_my_car)}"
        )
        print(f"Выплаты другому автомобилю: {format_number(damage_to_other_car)}")

        return [format_number(damage_to_my_car), format_number(damage_to_other_car)]

    except Exception as e:
        print(f"Произошла ошибка при получении данных: {e}")
        return ["", ""]


def get_technical_card():
    global vehicle_id

    url = f"https://api.encar.com/v1/readside/inspection/vehicle/{vehicle_id}"

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Referer": "http://www.encar.com/",
            "Cache-Control": "max-age=0",
            "Connection": "keep-alive",
        }

        response = requests.get(url, headers=headers)
        json_response = response.json() if response.status_code == 200 else None

        if not json_response:
            return "❌ Ошибка: не удалось получить данные. Проверьте ссылку."

        master = json_response.get("master", {}).get("detail", {})
        if not master:
            return "❌ Ошибка: данные о транспортном средстве не найдены."

        vehicle_id = json_response.get("vehicleId", "Не указано")
        model_year = master.get("modelYear", "Не указано").strip()
        vin = master.get("vin", "Не указано")
        first_registration_date = master.get("firstRegistrationDate", "Не указано")
        registration_date = master.get("registrationDate", "Не указано")
        mileage = f"{int(master.get('mileage', 0)):,}".replace(",", " ") + " км"
        transmission = master.get("transmissionType", {}).get("title", "Не указано")
        motor_type = master.get("motorType", "Не указано")
        color = master.get("colorType", {}).get("title", "Не указано")
        accident = "❌ Нет" if not master.get("accdient", False) else "⚠️ Да"
        simple_repair = "❌ Нет" if not master.get("simpleRepair", False) else "⚠️ Да"
        waterlog = "❌ Нет" if not master.get("waterlog", False) else "⚠️ Да"
        tuning = "❌ Нет" if not master.get("tuning", False) else "⚠️ Да"
        car_state = master.get("carStateType", {}).get("title", "Не указано")

        # Переводы
        translations = {
            "오토": "Автоматическая",
            "수동": "Механическая",
            "자가보증": "Собственная гарантия",
            "양호": "Хорошее состояние",
            "무채색": "Нейтральный",
            "적정": "В норме",
            "없음": "Нет",
            "누유": "Утечка",
            "불량": "Неисправность",
            "미세누유": "Незначительная утечка",
            "양호": "В хорошем состоянии",
            "주의": "Требует внимания",
            "교환": "Замена",
            "부족": "Недостаточный уровень",
            "정상": "Нормально",
            "작동불량": "Неисправна",
            "소음": "Шум",
            "작동양호": "Работает хорошо",
        }

        def translate(value):
            return translations.get(value, value)

        # Проверка состояния узлов
        inners = json_response.get("inners", [])
        nodes_status = {}

        for inner in inners:
            for child in inner.get("children", []):
                type_code = child.get("type", {}).get("code", "")
                status_type = child.get("statusType")
                status = (
                    translate(status_type.get("title", "Не указано"))
                    if status_type
                    else "Не указано"
                )

                nodes_status[type_code] = status

        output = (
            f"🚗 <b>Основная информация об автомобиле</b>\n"
            f"	•	ID автомобиля: {vehicle_id}\n"
            f"	•	Год выпуска: {model_year}\n"
            f"	•	Дата первой регистрации: {first_registration_date}\n"
            f"	•	Дата регистрации в системе: {registration_date}\n"
            f"	•	VIN: {vin}\n"
            f"	•	Пробег: {mileage}\n"
            f"	•	Тип трансмиссии: {translate(transmission)} ({transmission})\n"
            f"	•	Тип двигателя: {motor_type}\n"
            f"	•	Состояние автомобиля: {translate(car_state)} ({car_state})\n"
            f"	•	Цвет: {translate(color)} ({color})\n"
            f"	•	Тюнинг: {tuning}\n"
            f"	•	Автомобиль попадал в ДТП: {accident}\n"
            f"	•	Были ли простые ремонты: {simple_repair}\n"
            f"	•	Затопление: {waterlog}\n"
            f"\n⸻\n\n"
            f"⚙️ <b>Проверка основных узлов</b>\n"
            f"	•	Двигатель: ✅ {nodes_status.get('s001', 'Не указано')}\n"
            f"	•	Трансмиссия: ✅ {nodes_status.get('s002', 'Не указано')}\n"
            f"	•	Работа двигателя на холостом ходу: ✅ {nodes_status.get('s003', 'Не указано')}\n"
            f"	•	Утечка масла двигателя: {'❌ Нет' if nodes_status.get('s004', '없음') == 'Нет' else '⚠️ Да'} ({nodes_status.get('s004', 'Не указано')})\n"
            f"	•	Уровень масла в двигателе: ✅ {nodes_status.get('s005', 'Не указано')}\n"
            f"	•	Утечка охлаждающей жидкости: {'❌ Нет' if nodes_status.get('s006', '없음') == 'Нет' else '⚠️ Да'} ({nodes_status.get('s006', 'Не указано')})\n"
            f"	•	Уровень охлаждающей жидкости: ✅ {nodes_status.get('s007', 'Не указано')}\n"
            f"	•	Система подачи топлива: ✅ {nodes_status.get('s008', 'Не указано')}\n"
            f"	•	Автоматическая коробка передач: ✅ {nodes_status.get('s009', 'Не указано')}\n"
            f"	•	Утечка масла в АКПП: {'❌ Нет' if nodes_status.get('s010', '없음') == 'Нет' else '⚠️ Да'} ({nodes_status.get('s010', 'Не указано')})\n"
            f"	•	Работа АКПП на холостом ходу: ✅ {nodes_status.get('s011', 'Не указано')}\n"
            f"	•	Система сцепления: ✅ {nodes_status.get('s012', 'Не указано')}\n"
            f"	•	Карданный вал и подшипники: ✅ {nodes_status.get('s013', 'Не указано')}\n"
            f"	•	Редуктор: ✅ {nodes_status.get('s014', 'Не указано')}\n"
        )

        return output

    except requests.RequestException as e:
        return f"❌ Ошибка при получении данных: {e}"


# Callback query handler
@bot.callback_query_handler(
    func=lambda call: not call.data.startswith("guide_")
    and call.data != "back_to_guide"
    and call.data != ""
)
def handle_callback_query(call):
    global car_data, car_id_external, usd_rate

    if call.data.startswith("detail"):
        print_message("[ЗАПРОС] ДЕТАЛИЗАЦИЯ РАСЧËТА")

        detail_message = (
            f"<i>ПЕРВАЯ ЧАСТЬ ОПЛАТЫ (КОРЕЯ)</i>:\n\n"
            f"Стоимость автомобиля:\n<b>₩{format_number(car_data['car_price_krw'])}</b> | <b>{format_number(car_data['car_price_rub'])} ₽</b>\n\n"
            f"Услуги фирмы (поиск и подбор авто, документация, 3 осмотра):\n<b>₩{format_number(car_data['company_fees_krw'])}</b> | <b>{format_number(car_data['company_fees_rub'])} ₽</b>\n\n"
            f"Фрахт (отправка в порт, доставка автомобиля на базу, оплата судна):\n<b>₩{format_number(car_data['freight_korea_krw'])}</b> | <b>{format_number(car_data['freight_korea_rub'])} ₽</b>\n\n\n"
            f"Дилерский сбор:\n<b>₩{format_number(car_data['dealer_korea_krw'])}</b> | <b>{format_number(car_data['dealer_korea_rub'])} ₽</b>\n\n"
            f"<i>ВТОРАЯ ЧАСТЬ ОПЛАТЫ (РОССИЯ)</i>:\n\n"
            f"Брокер-Владивосток:\n<b>₩{format_number(car_data['broker_russia_krw'])}</b> | <b>{format_number(car_data['broker_russia_rub'])} ₽</b>\n\n\n"
            f"Единая таможенная ставка:\n<b>₩{format_number(car_data['customs_duty_krw'])}</b> | <b>{format_number(car_data['customs_duty_rub'])} ₽</b>\n\n"
            f"Таможенное оформление:\n<b>₩{format_number(car_data['customs_fee_krw'])}</b> | <b>{format_number(car_data['customs_fee_rub'])} ₽</b>\n\n"
            f"Утилизационный сбор:\n<b>₩{format_number(car_data['util_fee_krw'])}</b> | <b>{format_number(car_data['util_fee_rub'])} ₽</b>\n\n\n"
            f"Перегон во Владивостоке:\n<b>₩{format_number(car_data['vladivostok_transfer_krw'])}</b> | <b>{format_number(car_data['vladivostok_transfer_rub'])} ₽</b>\n\n"
            f"Итого под ключ во Владивостоке: \n<b>₩{format_number(car_data['total_cost_krw'])}</b> | <b>{format_number(car_data['total_cost_rub'])} ₽</b>\n\n"
            f"<b>🚚 Для доставки до вашего города уточняйте у менеджеров:</b>\n"
            # f"▪️ +82 10-5128-8082 (Александр)\n\n"
            f"▪️ +82-10-7934-6603\n"
        )

        # Inline buttons for further actions
        keyboard = types.InlineKeyboardMarkup()

        if call.data.startswith("detail_manual"):
            keyboard.add(
                types.InlineKeyboardButton(
                    "Рассчитать стоимость другого автомобиля",
                    callback_data="calculate_another_manual",
                )
            )
        else:
            keyboard.add(
                types.InlineKeyboardButton(
                    "Рассчитать стоимость другого автомобиля",
                    callback_data="calculate_another",
                )
            )

        keyboard.add(
            types.InlineKeyboardButton("Главное меню", callback_data="main_menu")
        )

        bot.send_message(
            call.message.chat.id,
            detail_message,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    elif call.data == "technical_card":
        print_message("[ЗАПРОС] ТЕХНИЧЕСКАЯ ОТЧËТ ОБ АВТОМОБИЛЕ")

        technical_card_output = get_technical_card()

        bot.send_message(
            call.message.chat.id,
            "Запрашиваю отчёт по автомобилю. Пожалуйста подождите ⏳",
        )

        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(
            types.InlineKeyboardButton(
                "Рассчитать стоимость другого автомобиля",
                callback_data="calculate_another",
            )
        )
        keyboard.add(
            types.InlineKeyboardButton("Главное меню", callback_data="main_menu")
        )
        # keyboard.add(
        #     types.InlineKeyboardButton(
        #         "Связаться с менеджером", url="https://t.me/HYT_TRADING_KR"
        #     )
        # )

        bot.send_message(
            call.message.chat.id,
            technical_card_output,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    elif call.data == "technical_report":
        bot.send_message(
            call.message.chat.id,
            "Запрашиваю отчёт по ДТП. Пожалуйста подождите ⏳",
        )

        # Retrieve insurance information
        insurance_info = get_insurance_total()

        # Проверка на наличие ошибки
        if (
            insurance_info is None
            or "Нет данных" in insurance_info[0]
            or "Нет данных" in insurance_info[1]
        ):
            error_message = (
                "Не удалось получить данные о страховых выплатах. \n\n"
                f'<a href="https://fem.encar.com/cars/report/accident/{car_id_external}">🔗 Посмотреть страховую историю вручную 🔗</a>\n\n\n'
                f"<b>Найдите две строки:</b>\n\n"
                f"보험사고 이력 (내차 피해) - Выплаты по представленному автомобилю\n"
                f"보험사고 이력 (타차 가해) - Выплаты другим участникам ДТП"
            )

            # Inline buttons for further actions
            keyboard = types.InlineKeyboardMarkup()
            keyboard.add(
                types.InlineKeyboardButton(
                    "Рассчитать стоимость другого автомобиля",
                    callback_data="calculate_another",
                )
            )
            keyboard.add(
                types.InlineKeyboardButton(
                    "Связаться с менеджером", url="https://t.me/HYT_TRADING_KR"
                )
            )

            # Отправка сообщения об ошибке
            bot.send_message(
                call.message.chat.id,
                error_message,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        else:
            current_car_insurance_payments = (
                "0" if len(insurance_info[0]) == 0 else insurance_info[0]
            )
            other_car_insurance_payments = (
                "0" if len(insurance_info[1]) == 0 else insurance_info[1]
            )

            # Construct the message for the technical report
            tech_report_message = (
                f"Страховые выплаты по представленному автомобилю: \n<b>{current_car_insurance_payments} ₩</b>\n\n"
                f"Страховые выплаты другим участникам ДТП: \n<b>{other_car_insurance_payments} ₩</b>\n\n"
                f'<a href="https://fem.encar.com/cars/report/inspect/{car_id_external}">🔗 Ссылка на схему повреждений кузовных элементов 🔗</a>'
            )

            # Inline buttons for further actions
            keyboard = types.InlineKeyboardMarkup()
            keyboard.add(
                types.InlineKeyboardButton(
                    "Рассчитать стоимость другого автомобиля",
                    callback_data="calculate_another",
                )
            )
            keyboard.add(
                types.InlineKeyboardButton(
                    "Связаться с менеджером", url="https://t.me/HYT_TRADING_KR"
                )
            )
            keyboard.add(
                types.InlineKeyboardButton("Главное меню", callback_data="main_menu")
            )

            bot.send_message(
                call.message.chat.id,
                tech_report_message,
                parse_mode="HTML",
                reply_markup=keyboard,
            )

    elif call.data == "calculate_another":
        bot.send_message(
            call.message.chat.id,
            "Пожалуйста, введите ссылку на автомобиль с сайта (encar.com, kbchachacha.com, web.chutcha.net)",
        )

    elif call.data == "calculate_another_manual":
        # Создаем клавиатуру с кнопками выбора возраста
        keyboard = types.ReplyKeyboardMarkup(
            resize_keyboard=True, one_time_keyboard=True
        )
        keyboard.add("До 3 лет", "От 3 до 5 лет")
        keyboard.add("От 5 до 7 лет", "Более 7 лет")

        msg = bot.send_message(
            call.message.chat.id, "Выберите возраст автомобиля:", reply_markup=keyboard
        )
        bot.register_next_step_handler(msg, process_car_age)

    elif call.data == "main_menu":
        bot.send_message(call.message.chat.id, "Главное меню", reply_markup=main_menu())


def process_car_age(message):
    user_input = message.text.strip()

    # Проверяем ввод
    age_mapping = {
        "До 3 лет": "0-3",
        "От 3 до 5 лет": "3-5",
        "От 5 до 7 лет": "5-7",
        "Более 7 лет": "7-0",
    }

    if user_input not in age_mapping:
        bot.send_message(message.chat.id, "Пожалуйста, выберите возраст из списка.")
        return

    # Сохраняем возраст авто
    user_data[message.chat.id] = {"car_age": age_mapping[user_input]}

    # Создаем обычную клавиатуру с кнопками для выбора объема двигателя
    keyboard = types.ReplyKeyboardMarkup(
        resize_keyboard=True, one_time_keyboard=True, row_width=3
    )

    # Добавляем кнопки с объемами двигателя от 1000 до 4400
    engine_volumes = []
    for volume in range(1000, 4401, 200):
        engine_volumes.append(str(volume))

    # Разбиваем на ряды по 3 кнопки
    for i in range(0, len(engine_volumes), 3):
        row = engine_volumes[i : i + 3]
        keyboard.add(*[types.KeyboardButton(vol) for vol in row])

    # Запрашиваем объем двигателя с помощью обычных кнопок
    bot.send_message(
        message.chat.id,
        "Выберите объем двигателя в см³:",
        reply_markup=keyboard,
    )
    bot.register_next_step_handler(message, process_engine_volume)


def process_engine_volume(message):
    user_input = message.text.strip()

    # Проверяем валидность выбора объема двигателя
    valid_volumes = [str(vol) for vol in range(1000, 4401, 200)]

    if user_input not in valid_volumes:
        bot.send_message(
            message.chat.id,
            "Пожалуйста, выберите объем двигателя из предложенных вариантов.",
        )
        bot.register_next_step_handler(message, process_engine_volume)
        return

    # Сохраняем объем двигателя
    user_data[message.chat.id]["engine_volume"] = int(user_input)

    # Запрашиваем стоимость авто (возвращаем к обычному вводу текста)
    keyboard = types.ReplyKeyboardRemove()

    # Информация о формате ввода цены
    price_info = (
        "В Корее стоимость часто указывается в 만원 (ман вон) — это укороченное обозначение, "
        "где 1 ман = 10 000 вон.\n"
        "Например, если указано 12,400만원, это означает:\n"
        "12 400 × 10 000 = 124 000 000\n\n"
        "Пожалуйста, при вводе стоимости указывайте итоговую сумму в полном числовом "
        "формате — без пробелов, запятых или символов."
    )

    bot.send_message(message.chat.id, price_info, reply_markup=keyboard)

    # Запрашиваем стоимость авто
    bot.send_message(
        message.chat.id,
        "Введите стоимость автомобиля в корейских вонах (например, 15000000):",
    )
    bot.register_next_step_handler(message, process_car_price)


def process_car_price(message):
    global usd_to_krw_rate, usd_to_rub_rate, krw_to_rub_rate, rub_to_krw_rate

    user_input = message.text.strip()

    # Удаляем все пробелы и другие не-цифровые символы из ввода
    cleaned_input = "".join(filter(str.isdigit, user_input))

    # Проверяем, что введено число
    if not cleaned_input:
        bot.send_message(
            message.chat.id,
            "Пожалуйста, введите корректную стоимость автомобиля в вонах.",
        )
        bot.register_next_step_handler(message, process_car_price)
        return

    # Сохраняем стоимость автомобиля
    user_data[message.chat.id]["car_price_krw"] = int(cleaned_input)
    user_id = message.chat.id
    is_manager = user_id in MANAGERS  # Check if user is a manager

    # Извлекаем данные пользователя
    if message.chat.id not in user_data:
        user_data[message.chat.id] = {}

    if "car_age" not in user_data[message.chat.id]:
        bot.send_message(message.chat.id, "Произошла ошибка, попробуйте снова.")
        return  # Прерываем выполнение, если возраст не установлен

    age_group = user_data[message.chat.id]["car_age"]
    engine_volume = user_data[message.chat.id]["engine_volume"]
    car_price_krw = user_data[message.chat.id]["car_price_krw"]

    # Сохраняем данные для расчета и запрашиваем HP
    user_hp_pending[message.chat.id] = {
        'type': 'manual',
        'engine_volume': engine_volume,
        'car_price_krw': car_price_krw,
        'age_group': age_group,
        'is_manager': is_manager,
    }

    bot.send_message(
        message.chat.id,
        "Введите мощность двигателя в лошадиных силах (л.с.):",
    )
    # Ожидаем ввод HP, обработка в handle_hp_input


# Устаревшая функция - оставлена для обратной совместимости
def process_car_price_legacy(message):
    global usd_to_krw_rate, usd_to_rub_rate, krw_to_rub_rate, rub_to_krw_rate

    user_input = message.text.strip()

    # Удаляем все пробелы и другие не-цифровые символы из ввода
    cleaned_input = "".join(filter(str.isdigit, user_input))

    # Проверяем, что введено число
    if not cleaned_input:
        bot.send_message(
            message.chat.id,
            "Пожалуйста, введите корректную стоимость автомобиля в вонах.",
        )
        bot.register_next_step_handler(message, process_car_price_legacy)
        return

    # Сохраняем стоимость автомобиля
    user_data[message.chat.id]["car_price_krw"] = int(cleaned_input)
    user_id = message.chat.id
    is_manager = user_id in MANAGERS  # Check if user is a manager

    # Извлекаем данные пользователя
    if message.chat.id not in user_data:
        user_data[message.chat.id] = {}

    if "car_age" not in user_data[message.chat.id]:
        bot.send_message(message.chat.id, "Произошла ошибка, попробуйте снова.")
        return  # Прерываем выполнение, если возраст не установлен

    age_group = user_data[message.chat.id]["car_age"]
    engine_volume = user_data[message.chat.id]["engine_volume"]
    car_price_krw = user_data[message.chat.id]["car_price_krw"]

    # Получаем актуальные курсы валют
    get_currency_rates()
    get_rub_to_krw_rate()

    # Конвертируем стоимость автомобиля в USD и RUB
    price_usd = car_price_krw / usd_to_krw_rate  # Оставляем для расчета доставки в USD
    price_rub = car_price_krw * krw_to_rub_rate  # Прямая конверсия KRW → RUB

    # Рассчитываем таможенные платежи
    customs_fees = get_customs_fees_manual(engine_volume, car_price_krw, age_group)

    customs_duty = clean_number(customs_fees["tax"])  # Таможенная пошлина
    customs_fee = clean_number(customs_fees["sbor"])  # Таможенный сбор
    recycling_fee = clean_number(customs_fees["util"])  # Утилизационный сбор

    # Расчет стоимости брокерских услуг
    broker_fee = 100000.00  # Брокерские услуги (СВХ + СБКТС + лаборатория + перегон)

    # Определяем тип авто (SUV или седан) на основе объема двигателя
    car_type = "SUV" if engine_volume >= 2500 else "Sedan"

    # Расчет стоимости доставки согласно логике из calculate_cost
    delivery_fee = 850.00 if car_type == "SUV" else 750.00  # в долларах
    delivery_fee_rub = delivery_fee * usd_to_rub_rate  # конвертация в рубли

    # Расчет стоимости услуги дилера/аукциона
    dealer_fee_krw = 440000  # в вонах
    dealer_fee_usd = dealer_fee_krw / usd_to_krw_rate  # конвертация в доллары
    dealer_fee_rub = dealer_fee_krw * krw_to_rub_rate  # Прямая конверсия KRW → RUB

    # Расчет стоимости оформления и перевозки по Корее
    kr_documentation_fee_krw = 300000  # в вонах
    kr_documentation_fee_usd = (
        kr_documentation_fee_krw / usd_to_krw_rate
    )  # конвертация в доллары
    kr_documentation_fee_rub = (
        kr_documentation_fee_krw * krw_to_rub_rate
    )  # Прямая конверсия KRW → RUB

    # Расчет финальной стоимости автомобиля во Владивостоке
    total_cost_vladivostok = (
        price_rub  # стоимость авто
        + dealer_fee_rub  # услуги дилера/аукциона
        + kr_documentation_fee_rub  # оформление, снятие с учёта и перевозка по Корее
        + customs_duty  # таможенная пошлина
        + customs_fee  # таможенные сборы
        + recycling_fee  # утилизационный сбор
        + broker_fee  # брокерские услуги (СВХ + СБКТС + лаборатория + перегон)
        + delivery_fee_rub  # доставка паромом
    )

    # Доставка до других городов рассчитывается индивидуально

    # Определяем стоимость доставки в зависимости от типа авто
    delivery_fee_usd = 850 if car_type == "SUV" else 750
    # Определяем стоимость услуг дилера/аукциона
    dealer_fee_krw = 440000
    dealer_fee_usd = dealer_fee_krw / usd_to_krw_rate

    # Формируем сообщение с расчетом стоимости
    if is_manager:
        age_display = (
            "До 3 лет"
            if age_group == "0-3"
            else (
                "От 3 до 5 лет"
                if age_group == "3-5"
                else (
                    "От 5 до 7 лет"
                    if age_group == "5-7"
                    else "От 7 лет" if age_group == "7-0" else age_group
                )
            )
        )

        car_type_formatted = "Кроссовер" if car_type == "SUV" else "Легковой"

        result_message = (
            f"🔧 Объём двигателя: {engine_volume} cc\n"
            f"🏎 Тип кузова: {car_type_formatted}\n\n"
            f"💰 СТОИМОСТЬ АВТОМОБИЛЯ\n"
            f"• Цена авто: ₩{format_number(car_price_krw)}\n"
            f"• Услуги дилера/аукциона: ₩{format_number(dealer_fee_krw)}\n"
            f"• Оформление, снятие с учёта + перевозка по Корее: ₩300,000\n\n"
            f"⛴️ ДОСТАВКА\n"
            f"• До Владивостока (Ro-Ro): ₩{format_number(delivery_fee_usd * usd_to_krw_rate)}\n\n"
            f"💵 К ОПЛАТЕ ПО ИНВОЙСУ\n"
            f"• В вонах: ₩{format_number(car_price_krw + dealer_fee_krw + kr_documentation_fee_krw + (delivery_fee_usd * usd_to_krw_rate))}\n"
            f"• В рублях: {format_number(price_rub + dealer_fee_rub + kr_documentation_fee_rub + delivery_fee_rub)} ₽\n\n"
            f"♻️Стоимость услуг в РФ\n"
            f"🛃 Таможенные платежи (РФ)\n"
            f"• Таможенная пошлина: {format_number(customs_duty)} ₽\n"
            f"• Таможенные сборы: {format_number(customs_fee)} ₽\n"
            f"• Утилизационный сбор: {format_number(recycling_fee)} ₽\n\n"
            f"💼 БРОКЕРСКИЕ УСЛУГИ\n"
            f"• СВХ + СБКТС + лаборатория + перегон: 85,000 ₽\n\n"
            f"💰 ИТОГОВАЯ СТОИМОСТЬ\n"
            f"• Владивосток: {format_number(total_cost_vladivostok)} ₽\n\n"
        )
    else:
        age_display = (
            "До 3 лет"
            if age_group == "0-3"
            else (
                "От 3 до 5 лет"
                if age_group == "3-5"
                else (
                    "От 5 до 7 лет"
                    if age_group == "5-7"
                    else "От 7 лет" if age_group == "7-0" else age_group
                )
            )
        )

        car_type_formatted = "Кроссовер" if car_type == "SUV" else "Легковой"

        result_message = (
            f"🏎 Тип кузова: {car_type_formatted}\n"
            f"🔧 Объём двигателя: {engine_volume} cc\n\n"
            f"💰 Финальная стоимость во Владивостоке на текущий день: {format_number(total_cost_vladivostok)} ₽\n\n"
            f"⚠️ Если данное авто попадает под санкции, уточните возможность отправки у наших менеджеров:\n\n"
            f"📱 +82-10-7626-1999\n"
            f"📱 +82-10-7934-6603\n"
            f"📢 <a href='https://t.me/HYT_Trading'>Официальный телеграм канал</a>"
        )

    # Клавиатура с дальнейшими действиями
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton(
            "Рассчитать другой автомобиль", callback_data="calculate_another_manual"
        )
    )
    # keyboard.add(
    #     types.InlineKeyboardButton(
    #         "Связаться с менеджером", url="https://t.me/HYT_TRADING_KR"
    #     )
    # )
    keyboard.add(types.InlineKeyboardButton("Главное меню", callback_data="main_menu"))

    # Отправляем сообщение пользователю
    bot.send_message(
        message.chat.id,
        result_message,
        parse_mode="HTML",
        reply_markup=keyboard,
    )

    # Очищаем данные пользователя после расчета
    del user_data[message.chat.id]

    # Если пользователь не менеджер, отправляем дополнительное сообщение с предложением оставить заявку
    if not is_manager:
        request_keyboard = types.InlineKeyboardMarkup()
        request_keyboard.add(
            types.InlineKeyboardButton("Оставить заявку", callback_data="car_request")
        )

        bot.send_message(
            message.chat.id,
            "🔥 Хотите узнать точную стоимость и получить персональное предложение? Оставьте заявку прямо сейчас и наши специалисты подготовят для вас детальный расчёт со всеми скидками!",
            reply_markup=request_keyboard,
        )


# Обработчик ввода мощности (HP) - должен быть перед основным обработчиком сообщений
@bot.message_handler(func=lambda message: message.chat.id in user_hp_pending)
def handle_hp_input(message):
    """Обработка ввода мощности двигателя (л.с.) от пользователя."""
    chat_id = message.chat.id
    user_input = message.text.strip()

    # Проверяем, что ввод - это число
    try:
        hp = int(user_input)
        if hp <= 0 or hp > 2000:
            bot.send_message(
                chat_id,
                "⚠️ Введите корректное значение мощности (1-2000 л.с.):"
            )
            return

        # Получаем сохраненные данные
        pending = user_hp_pending.pop(chat_id)

        if pending['type'] == 'link':
            # Продолжаем расчет по ссылке с введенным HP
            calculate_cost(
                pending['link'],
                pending['message'],
                hp_override=hp,
                skip_increment=True
            )
        elif pending['type'] == 'manual':
            # Продолжаем ручной расчет с введенным HP
            continue_manual_calculation_with_hp(chat_id, pending, hp)

    except ValueError:
        bot.send_message(
            chat_id,
            "⚠️ Пожалуйста, введите число (мощность в л.с.):"
        )


def continue_manual_calculation_with_hp(chat_id, pending_data, hp):
    """Завершение ручного расчета с введенным HP."""
    global usd_to_krw_rate, usd_to_rub_rate, krw_to_rub_rate

    engine_volume = pending_data['engine_volume']
    car_price_krw = pending_data['car_price_krw']
    age_group = pending_data['age_group']
    is_manager = pending_data['is_manager']

    # Получаем актуальные курсы валют
    get_currency_rates()
    get_rub_to_krw_rate()

    # Конвертируем стоимость автомобиля в USD и RUB
    price_usd = car_price_krw / usd_to_krw_rate
    price_rub = car_price_krw * krw_to_rub_rate

    # Рассчитываем таможенные платежи с HP
    customs_fees = get_customs_fees_manual(engine_volume, car_price_krw, age_group, hp=hp)

    customs_duty = clean_number(customs_fees["tax"])
    customs_fee = clean_number(customs_fees["sbor"])
    recycling_fee = clean_number(customs_fees["util"])

    # Расчет стоимости брокерских услуг
    broker_fee = 100000.00

    # Определяем тип авто (SUV или седан) на основе объема двигателя
    car_type = "SUV" if engine_volume >= 2500 else "Sedan"

    # Расчет стоимости доставки
    delivery_fee = 850.00 if car_type == "SUV" else 750.00
    delivery_fee_rub = delivery_fee * usd_to_rub_rate

    # Расчет стоимости услуги дилера/аукциона
    dealer_fee_krw = 440000
    dealer_fee_rub = dealer_fee_krw * krw_to_rub_rate

    # Расчет стоимости оформления и перевозки по Корее
    kr_documentation_fee_krw = 300000
    kr_documentation_fee_rub = kr_documentation_fee_krw * krw_to_rub_rate

    # Расчет финальной стоимости автомобиля во Владивостоке
    total_cost_vladivostok = (
        price_rub
        + dealer_fee_rub
        + kr_documentation_fee_rub
        + customs_duty
        + customs_fee
        + recycling_fee
        + broker_fee
        + delivery_fee_rub
    )

    car_type_formatted = "Кроссовер" if car_type == "SUV" else "Легковой"

    age_display = (
        "До 3 лет"
        if age_group == "0-3"
        else (
            "От 3 до 5 лет"
            if age_group == "3-5"
            else (
                "От 5 до 7 лет"
                if age_group == "5-7"
                else "От 7 лет" if age_group == "7-0" else age_group
            )
        )
    )

    if is_manager:
        result_message = (
            f"🔧 Объём двигателя: {engine_volume} cc\n"
            f"🐴 Мощность: {hp} л.с.\n"
            f"🏎 Тип кузова: {car_type_formatted}\n\n"
            f"💰 СТОИМОСТЬ АВТОМОБИЛЯ\n"
            f"• Цена авто: ₩{format_number(car_price_krw)}\n"
            f"• Услуги дилера/аукциона: ₩{format_number(dealer_fee_krw)}\n"
            f"• Оформление, снятие с учёта + перевозка по Корее: ₩300,000\n\n"
            f"⛴️ ДОСТАВКА\n"
            f"• До Владивостока (Ro-Ro): ₩{format_number(delivery_fee * usd_to_krw_rate)}\n\n"
            f"💵 К ОПЛАТЕ ПО ИНВОЙСУ\n"
            f"• В вонах: ₩{format_number(car_price_krw + dealer_fee_krw + kr_documentation_fee_krw + (delivery_fee * usd_to_krw_rate))}\n"
            f"• В рублях: {format_number(price_rub + dealer_fee_rub + kr_documentation_fee_rub + delivery_fee_rub)} ₽\n\n"
            f"♻️Стоимость услуг в РФ\n"
            f"🛃 Таможенные платежи (РФ)\n"
            f"• Таможенная пошлина: {format_number(customs_duty)} ₽\n"
            f"• Таможенные сборы: {format_number(customs_fee)} ₽\n"
            f"• Утилизационный сбор: {format_number(recycling_fee)} ₽\n\n"
            f"💼 БРОКЕРСКИЕ УСЛУГИ\n"
            f"• СВХ + СБКТС + лаборатория + перегон: 100,000 ₽\n\n"
            f"💰 ИТОГОВАЯ СТОИМОСТЬ\n"
            f"• Владивосток: {format_number(total_cost_vladivostok)} ₽\n\n"
        )
    else:
        result_message = (
            f"🏎 Тип кузова: {car_type_formatted}\n"
            f"🔧 Объём двигателя: {engine_volume} cc\n"
            f"🐴 Мощность: {hp} л.с.\n\n"
            f"💰 Финальная стоимость во Владивостоке на текущий день: {format_number(total_cost_vladivostok)} ₽\n\n"
            f"⚠️ Если данное авто попадает под санкции, уточните возможность отправки у наших менеджеров:\n\n"
            f"📱 +82-10-7626-1999\n"
            f"📱 +82-10-7934-6603\n"
            f"📢 <a href='https://t.me/HYT_Trading'>Официальный телеграм канал</a>"
        )

    # Клавиатура с дальнейшими действиями
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton(
            "Рассчитать другой автомобиль", callback_data="calculate_another_manual"
        )
    )

    bot.send_message(chat_id, result_message, parse_mode="HTML", reply_markup=keyboard)


@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_message = message.text.strip()
    user_id = message.from_user.id

    # Главное меню
    if user_message == "Гид по покупке авто":
        print(
            f"\n=== DEBUG: Пользователь {user_id} запросил 'Гид по покупке авто' ===\n"
        )
        bot.send_chat_action(message.chat.id, "typing")
        show_acquisition_guide_menu(message.chat.id)
        return

    elif user_message == "Расчёт стоимости авто":
        user_id = message.from_user.id
        if is_user_subscribed(user_id):
            bot.send_message(
                message.chat.id,
                "Выберите способ расчета стоимости:",
                reply_markup=calculation_menu(),
            )
        else:
            show_subscription_prompt(message.chat.id)

    elif user_message == "Заказать автомобиль / Оставить заявку":
        # Запускаем процесс заполнения заявки
        start_new_request(message)

    # Подменю расчета
    elif user_message == "Рассчитать по ссылке":
        user_id = message.from_user.id
        if is_user_subscribed(user_id):
            bot.send_message(
                message.chat.id,
                "Пожалуйста, введите ссылку на автомобиль с одного из сайтов (encar.com, kbchachacha.com):",
            )
        else:
            show_subscription_prompt(message.chat.id)

    elif user_message == "Расчёт вручную":
        user_id = message.from_user.id
        if is_user_subscribed(user_id):
            # Запрашиваем возраст автомобиля
            keyboard = types.ReplyKeyboardMarkup(
                resize_keyboard=True, one_time_keyboard=True
            )
            keyboard.add("До 3 лет", "От 3 до 5 лет")
            keyboard.add("От 5 до 7 лет", "Более 7 лет")

            bot.send_message(
                message.chat.id,
                "Выберите возраст автомобиля:",
                reply_markup=keyboard,
            )
            bot.register_next_step_handler(message, process_car_age)
        else:
            show_subscription_prompt(message.chat.id)

    elif user_message == "Вернуться в главное меню":
        bot.send_message(
            message.chat.id,
            "Главное меню",
            reply_markup=main_menu(),
        )

    # Проверка на корректность ссылки
    elif re.match(
        r"^https?://(www|fem)\.encar\.com/.*|^https?://(www\.)?kbchachacha\.com/.*|^https?://m\.kbchachacha\.com/.*",
        user_message,
    ):
        user_id = message.from_user.id
        if is_user_subscribed(user_id):
            calculate_cost(user_message, message)
        else:
            show_subscription_prompt(message.chat.id)

    # В случае неизвестной команды
    else:
        bot.send_message(
            message.chat.id,
            "Пожалуйста, воспользуйтесь кнопками меню для навигации.",
            reply_markup=main_menu(),
        )


def show_acquisition_guide_menu(chat_id):
    """Показывает меню гида по приобретению автомобиля"""
    print(
        f"\n=== DEBUG: Вызвана функция show_acquisition_guide_menu для chat_id={chat_id} ===\n"
    )

    keyboard = types.InlineKeyboardMarkup(row_width=1)
    buttons = [
        types.InlineKeyboardButton(
            "1. Шаги приобретения авто", callback_data="guide_steps"
        ),
        types.InlineKeyboardButton(
            "2. Банки и SWIFT-переводы", callback_data="guide_banks"
        ),
        types.InlineKeyboardButton(
            "3. Сроки доставки", callback_data="guide_delivery_time"
        ),
        types.InlineKeyboardButton(
            "4. Что входит в наши услуги", callback_data="guide_services"
        ),
        types.InlineKeyboardButton(
            "5. Наши преимущества", callback_data="guide_advantages"
        ),
        types.InlineKeyboardButton(
            "6. Информация от брокера", callback_data="guide_broker"
        ),
        types.InlineKeyboardButton(
            "7. Отличие корейских комплектаций", callback_data="guide_configurations"
        ),
        types.InlineKeyboardButton(
            "8. Доставка в другие страны", callback_data="guide_international"
        ),
        types.InlineKeyboardButton("9. Контакты", callback_data="guide_contacts"),
        types.InlineKeyboardButton("Главное меню", callback_data="main_menu"),
    ]

    for button in buttons:
        keyboard.add(button)

    print(f"\n=== DEBUG: Отправляю сообщение с меню для chat_id={chat_id} ===\n")
    bot.send_message(
        chat_id,
        "📚 <b>ГИД ПО ПРИОБРЕТЕНИЮ АВТОМОБИЛЯ</b>\n\nВыберите интересующий вас раздел:",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("guide_") or call.data == "back_to_guide"
)
def handle_guide_sections(call):
    """Обработчик разделов гида по приобретению автомобиля"""
    chat_id = call.message.chat.id
    print(f"\n=== DEBUG: Обработка callback {call.data} для chat_id={chat_id} ===\n")

    # Обработка запроса на возврат к гиду
    if call.data == "back_to_guide":
        print(f"\n=== DEBUG: Пользователь {chat_id} запросил возврат к гиду ===\n")
        try:
            bot.delete_message(chat_id, call.message.message_id)
        except Exception as e:
            print(f"DEBUG: Ошибка при удалении сообщения: {e}")

        show_acquisition_guide_menu(chat_id)
        bot.answer_callback_query(call.id)
        return

    guide_section = call.data

    # Содержимое для каждого раздела
    guide_content = {
        "guide_steps": """<b>1. Шаги приобретения авто</b>

<b>1.1 Подписание договора</b>
Вы получаете договор и приложение, заполняете его (паспортные и контактные данные), затем отправляете скан подписанного документа. После этого мы подписываем его со своей стороны.

<b>1.2 Предоплата</b>
Вы вносите предоплату 80 000₽, которая входит в депозит на будущую покупку авто. Менеджер отправит вам реквизиты. Эта сумма вычитается из финального инвойса.

<b>1.3 Поиск автомобиля</b>
После подписания и оплаты мы приступаем к поиску авто по вашим параметрам и бюджету.
Площадки:
• https://encar.com
• https://kbchachacha.com
• https://kcar.com
• https://carmanager.co.kr
Аукционы:
• Lotte Autohub
• Glovis
• KCar
• HappyCar (битые авто)
Также используем предложения от корейских партнёров (публикуем в Telegram-канале).

<b>1.4 Осмотр автомобиля</b>
После согласования — выезд на осмотр:
• Замер ЛКП толщиномером
• Проверка ДВС, ходовой, салона
• Проверка опций
• Фото и видео отчёт
• Консультация по состоянию
Если авто подходит — вносится депозит и начинается выкуп.

<b>1.5 Оплата</b>
Вы получаете инвойс с:
• Стоимостью авто
• Услугами дилера
• Услуга компании и стоимостью доставки до Владивостока
Все данные в инвойсе указываются на английском языке. Оплата производится в $ или KRW через SWIFT-перевод.""",
        "guide_banks": """<b>2. Банки и SWIFT-переводы</b>

Для перевода вам потребуются:
• Паспорт
• Реквизиты нашей компании (счёт, SWIFT-код)
• Инвойс на английском языке

Перевод возможен в корейских вонах через банки, работающие с международными переводами (например: МТС Банк, Газпромбанк, ОТП Банк и др.).""",
        "guide_delivery_time": """<b>3. Сроки доставки</b>

<b>Общий срок от выкупа до прибытия во Владивосток:</b>
от 1 до 2 недель
<b>Этапы доставки:</b>
  1. <b>Выкуп и оформление автомобиля:</b>
Оформление документов, снятие с учёта и подготовка к отправке.
(1–3 рабочих дня)
  2. <b>Внутрикорейская транспортировка:</b>
Перевозка авто к порту отправки.
(1–2 дня)
  3. <b>Погрузка и морская доставка (Ro-Ro):</b>
Отправка судном до порта Владивосток.
• Время в пути: от 27 часов
• Зависит от расписания судов, очереди и сезона
  4. <b>Прибытие во Владивосток:</b>
Машина доставляется на стоянку брокера.
Ожидает оформления и дальнейшей отправки.
  5. <b>Доставка до вашего города:</b>
• Транспортной компанией
• Или самовывозом""",
        "guide_services": """<b>4. Что входит в наши услуги</b>

• Поиск автомобиля по вашим параметрам
• Осмотр на месте
• Подробный фото/видео отчёт
• Перевод и оформление документов
• Подготовка и снятие авто с учёта
• Организация логистики и сопровождение
• Консультации по таможенному оформлению

<b>Обговаривается отдельно или за доп.плату:</b>
1. Снятие тонировки с лобового стекла и передних стекол
2. Дозаправка автомобиля топливом
3. Покупка чехлов для салона и их установка
4. Покупка антигеля, масла
5. Покупка запчастей для авто (малогабаритные). Например: задние фонари, туманки лед, муз. усилитель.""",
        "guide_advantages": """<b>5. Наши преимущества</b>

• Опыт более 6 лет
• Мы стараемся, подбирать лучшие варианты
• Прозрачные условия
• Не делаем накрутки и лишних процентов на разных этапах покупки
• Вы можете самостоятельно просчитать стоимость интересующего Вас авто
• Сопровождение вашего авто на всех этапах
• Сотрудничаем с лучшими компаниями по доставке и растаможке авто""",
        "guide_broker": """<b>6. Информация от брокера</b>

Наш брокер:
• Забирает авто со стоянки
• Оформляет таможенные документы
• Организует транспортировку
• Предоставляет СВХ, СБКТС, ЭПТС
• Отправляет авто в ваш город или готовит к самовывозу

Все платежи на брокерские услуги, СВХ, СБКТС, ЭПТС — в рублях. Мы предоставим контакты проверенного брокера.""",
        "guide_configurations": """<b>7. Отличие корейских комплектаций</b>

• Часто богаче по опциям, чем европейские/российские версии
• Более мягкие настройки подвески
• Могут быть адаптированы под внутренний рынок (например, только корейский язык на мультимедиа — но это решаемо)""",
        "guide_international": """<b>8. Доставка в другие страны</b>

1. Грузия, г. Поти
2. Румыния, г. Констанца
3. Через Китай в Киргизию и Казахстан
4. Транзитом с Владивостока в Казахстан и Киргизию

Цены постоянно меняются. Уточняйте у администраторов канала (см.раздел «Контакты»)""",
        "guide_contacts": """<b>9. Контакты:</b>

Наш канал в телеграм: <a href='https://t.me/HYT_Trading'>@HYT_Trading</a>
Наш менеджер: <a href='https://t.me/HYT_TRADING_KR'>@HYT_TRADING_KR</a>

По всем остальным вопросам (также по вопросам сотрудничества):
+82 10 7934 6603 (WhatsApp, Telephone)

Наш адрес: 인천시 연수구 동춘동 913-1 (913-1, Dongchun-dong, Yeonsu-gu, Incheon)""",
    }

    # Если запрашиваемого раздела нет, отвечаем пользователю и выходим
    if guide_section not in guide_content:
        bot.answer_callback_query(call.id, "Раздел не найден")
        return

    # Создаем клавиатуру для возврата в меню гида
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton(
            "◀️ Назад к разделам гида", callback_data="back_to_guide"
        )
    )
    keyboard.add(
        types.InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")
    )

    # Показываем пользователю, что бот обрабатывает запрос
    bot.answer_callback_query(call.id)

    try:
        # Пытаемся отредактировать текущее сообщение
        print(f"DEBUG: Редактирую сообщение для раздела {guide_section}")
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=guide_content[guide_section],
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        print(f"DEBUG: Успешно отредактировано сообщение для раздела {guide_section}")
    except Exception as e:
        # Если не удалось отредактировать, отправляем новое сообщение
        print(f"DEBUG: Ошибка при редактировании сообщения: {e}")
        try:
            # Удаляем старое сообщение, если возможно
            bot.delete_message(chat_id, call.message.message_id)
        except Exception as delete_error:
            print(f"DEBUG: Не удалось удалить старое сообщение: {delete_error}")

        # Отправляем новое сообщение
        print(f"DEBUG: Отправляю новое сообщение для раздела {guide_section}")
        bot.send_message(
            chat_id=chat_id,
            text=guide_content[guide_section],
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )


# Run the bot
if __name__ == "__main__":
    # create_tables()

    print("🚀 ===============================================")
    print("🚀 Quickxa Bot - Инициализация...")
    print("🚀 ===============================================")
    set_bot_commands()
    print("✅ Команды бота успешно установлены")

    # Удаляем вебхук перед запуском бота
    print("🔄 Удаление вебхука...")
    bot.delete_webhook()
    print("✅ Вебхук успешно удален")

    # Обновляем курс каждые 12 часов и удаляем вебхук каждые 5 минут
    print("⏱️ Настройка планировщика задач...")
    scheduler = BackgroundScheduler()
    scheduler.add_job(get_usd_to_krw_rate, "interval", hours=12)
    print("💱 Задача обновления курса USD→KRW добавлена (каждые 12 часов)")
    scheduler.add_job(bot.delete_webhook, "interval", minutes=5)
    print("🔄 Задача удаления вебхука добавлена (каждые 5 минут)")
    scheduler.start()
    print("✅ Планировщик задач успешно запущен")

    print("🤖 ===============================================")
    print("🤖 Бот запускается в режиме polling...")
    print("🤖 ===============================================")
    bot.polling(non_stop=True)
