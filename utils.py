import time
import requests
import datetime
import locale
import random

PROXY_URL = "http://B01vby:GBno0x@45.118.250.2:8000"
proxies = {"http": PROXY_URL, "https": PROXY_URL}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


def format_number(number):
    return locale.format_string("%d", int(number), grouping=True)


def is_prokhodnaya_car(year, month):
    """
    Определяет, является ли автомобиль "Проходным" или "Непроходным".
    Проходные - автомобили возрастом от 3 до 5 лет на момент таможенного оформления.
    Непроходные - автомобили младше 3 лет или старше 5 лет.

    :param year: Год регистрации автомобиля
    :param month: Месяц регистрации автомобиля
    :return: True если автомобиль "Проходной", False если "Непроходной"
    """
    # Убираем ведущий ноль у месяца, если он есть
    month = int(month.lstrip("0")) if isinstance(month, str) else int(month)
    year = int(year)

    # Рассчитываем возраст автомобиля в месяцах
    current_date = datetime.datetime.now()
    car_date = datetime.datetime(year=year, month=month, day=1)

    age_in_months = (
        (current_date.year - car_date.year) * 12 + current_date.month - car_date.month
    )

    # Проходные: от 36 месяцев (3 года) до 60 месяцев (5 лет)
    if 36 <= age_in_months <= 60:
        return True
    else:
        return False


def will_be_prokhodnaya_soon(year, month, months_ahead=3):
    """
    Проверяет, станет ли автомобиль "Проходным" в ближайшие месяцы.
    
    :param year: Год регистрации автомобиля
    :param month: Месяц регистрации автомобиля
    :param months_ahead: Количество месяцев вперед для проверки (по умолчанию 3)
    :return: Tuple (станет_ли_проходным, через_сколько_месяцев)
    """
    month = int(month.lstrip("0")) if isinstance(month, str) else int(month)
    year = int(year)
    
    current_date = datetime.datetime.now()
    car_date = datetime.datetime(year=year, month=month, day=1)
    
    age_in_months = (
        (current_date.year - car_date.year) * 12 + current_date.month - car_date.month
    )
    
    # Если авто уже проходное или старше 5 лет
    if age_in_months >= 36:
        return False, 0
    
    # Проверяем, станет ли проходным в ближайшие месяцы
    months_to_prokhodnaya = 36 - age_in_months
    if 0 < months_to_prokhodnaya <= months_ahead:
        return True, months_to_prokhodnaya
    
    return False, 0


def calculate_age(year, month):
    """
    Рассчитывает возрастную категорию автомобиля по классификации calcus.ru.

    :param year: Год выпуска автомобиля
    :param month: Месяц выпуска автомобиля
    :return: Возрастная категория ("0-3", "3-5", "5-7", "7-0")
    """
    # Убираем ведущий ноль у месяца, если он есть
    month = int(month.lstrip("0")) if isinstance(month, str) else int(month)

    current_date = datetime.datetime.now()
    car_date = datetime.datetime(year=int(year), month=month, day=1)

    age_in_months = (
        (current_date.year - car_date.year) * 12 + current_date.month - car_date.month
    )

    if age_in_months < 36:
        return "0-3"
    elif 36 <= age_in_months < 60:
        return "3-5"
    elif 60 <= age_in_months < 84:
        return "5-7"
    else:
        return "7-0"


def calculate_age_for_customs(year, month):
    """
    Рассчитывает возрастную категорию для таможни с учетом будущего статуса "Проходная".
    Если авто станет проходным в ближайшие 3 месяца, возвращает категорию 3-5 лет.
    
    :param year: Год выпуска автомобиля
    :param month: Месяц выпуска автомобиля
    :return: Tuple (возрастная_категория, is_future_prokhodnaya, months_to_prokhodnaya)
    """
    current_age = calculate_age(year, month)
    will_be_prokhodnaya, months_to = will_be_prokhodnaya_soon(year, month)
    
    if will_be_prokhodnaya and current_age == "0-3":
        # Если скоро станет проходной, используем ставку 3-5 лет
        return "3-5", True, months_to
    
    return current_age, False, 0


def get_customs_fees_manual(engine_volume, car_price, car_age, engine_type=1):
    """
    Запрашивает расчёт таможенных платежей с сайта calcus.ru.
    :param engine_volume: Объём двигателя (куб. см)
    :param car_price: Цена авто в вонах
    :param car_year: Год выпуска авто
    :param engine_type: Тип двигателя (1 - бензин, 2 - дизель, 3 - гибрид, 4 - электромобиль)
    :return: JSON с результатами расчёта
    """
    url = "https://calcus.ru/calculate/Customs"

    payload = {
        "owner": 1,  # Физлицо
        "age": car_age,  # Возрастная категория
        "engine": engine_type,  # Тип двигателя (по умолчанию 1 - бензин)
        "power": 1,  # Лошадиные силы (можно оставить 1)
        "power_unit": 1,  # Тип мощности (1 - л.с.)
        "value": int(engine_volume),  # Объём двигателя
        "price": int(car_price),  # Цена авто в KRW
        "curr": "KRW",  # Валюта
    }

    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": "https://calcus.ru/",
        "Origin": "https://calcus.ru",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    try:
        response = requests.post(url, data=payload, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"Ошибка при запросе к calcus.ru: {e}")
        return None
    finally:
        time.sleep(3)


def get_customs_fees(engine_volume, car_price, car_year, car_month, engine_type=1, custom_age=None):
    """
    Запрашивает расчёт таможенных платежей с сайта calcus.ru.
    :param engine_volume: Объём двигателя (куб. см)
    :param car_price: Цена авто в вонах
    :param car_year: Год выпуска авто
    :param car_month: Месяц выпуска авто
    :param engine_type: Тип двигателя (1 - бензин, 2 - дизель, 3 - гибрид, 4 - электромобиль)
    :param custom_age: Возрастная категория для таможни (если None, рассчитывается автоматически)
    :return: JSON с результатами расчёта
    """
    url = "https://calcus.ru/calculate/Customs"

    # Используем переданную возрастную категорию или рассчитываем
    if custom_age:
        age_category = custom_age
    else:
        age_category, _, _ = calculate_age_for_customs(car_year, car_month)

    payload = {
        "owner": 1,  # Физлицо
        "age": age_category,  # Возрастная категория
        "engine": engine_type,  # Тип двигателя (по умолчанию 1 - бензин)
        "power": 1,  # Лошадиные силы (можно оставить 1)
        "power_unit": 1,  # Тип мощности (1 - л.с.)
        "value": int(engine_volume),  # Объём двигателя
        "price": int(car_price),  # Цена авто в KRW
        "curr": "KRW",  # Валюта
    }

    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": "https://calcus.ru/",
        "Origin": "https://calcus.ru",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    try:
        response = requests.post(url, data=payload, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"Ошибка при запросе к calcus.ru: {e}")
        return None
    finally:
        time.sleep(3)


def clean_number(value):
    """Очищает строку от пробелов и преобразует в число"""
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(float(str(value).replace(" ", "").replace(",", ".")))
    except Exception as e:
        print(f"[clean_number] Ошибка: {e}, value={value}")
        return 0  # или другое значение по умолчанию


def generate_encar_photo_url(photo_path):
    """
    Формирует правильный URL для фотографий Encar.
    Пример результата: https://ci.encar.com/carpicture02/pic3902/39027097_006.jpg
    """

    base_url = "https://ci.encar.com"
    photo_url = f"{base_url}/{photo_path}"

    return photo_url
