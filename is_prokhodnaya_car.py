import datetime


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
