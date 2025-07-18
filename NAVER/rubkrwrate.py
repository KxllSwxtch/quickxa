import requests

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
    "u6": "standardUnit",
    "u7": "0",
    "u3": "RUB",
    "u4": "KRW",
    "u8": "down",
    "u2": "1",
}

response = requests.get(
    "https://m.search.naver.com/p/csearch/content/qapirender.nhn",
    params=params,
    headers=headers,
)
