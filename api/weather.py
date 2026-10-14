
import json
import os
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

CITIES = {
    1: (35.6762, 139.6503),  # 東京
    2: (34.6937, 135.5023),  # 大阪
    3: (43.0618, 141.3545),  # 札幌
    4: (33.5902, 130.4017),  # 福岡
    5: (26.2124, 127.6809),  # 那覇
}


def fetch_json(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "ScratchWeather/1.0"}
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def encode_temp(value):
    # 例：23.4℃ → 2234
    # 気温を10倍して2000を足す
    return round(value * 10) + 2000


def encode_code(value):
    # 天気コードに100を足して3桁にする
    return int(value) + 100


def pack_current(current):
    temp = encode_temp(current["temperature_2m"])
    humidity = int(current["relative_humidity_2m"]) + 100
    code = encode_code(current["weather_code"])

    return temp * 1_000_000 + humidity * 1000 + code


def pack_day(high, low, code):
    return (
        encode_temp(high) * 10_000_000
        + encode_temp(low) * 1000
        + encode_code(code)
    )



class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            from urllib.parse import urlparse, parse_qs

            query = parse_qs(urlparse(self.path).query)
            city_id = int(query.get("city", ["2"])[0])

            if city_id not in CITIES:
                self.send_error(400, "Invalid city")
                return

            lat, lon = CITIES[city_id]


            params = urllib.parse.urlencode({
                "latitude": lat,
                "longitude": lon,
                "current": (
                    "temperature_2m,relative_humidity_2m,weather_code"
                ),
                "daily": (
                    "weather_code,temperature_2m_max,temperature_2m_min"
                ),
                "forecast_days": 3,
                "timezone": "Asia/Tokyo",
            })

            data = fetch_json(
                "https://api.open-meteo.com/v1/forecast?" + params
            )

            current = pack_current(data["current"])
            daily = data["daily"]

            result = {
                "current": current,
                "day1": pack_day(
                    daily["temperature_2m_max"][0],
                    daily["temperature_2m_min"][0],
                    daily["weather_code"][0]
                ),
                "day2": pack_day(
                    daily["temperature_2m_max"][1],
                    daily["temperature_2m_min"][1],
                    daily["weather_code"][1]
                ),
                "day3": pack_day(
                    daily["temperature_2m_max"][2],
                    daily["temperature_2m_min"][2],
                    daily["weather_code"][2]
                ),
            }

            body = json.dumps(result).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        except Exception as exc:
            print("Weather API error:", repr(exc))
            self.send_error(502, "Weather API failed")

    def log_message(self, fmt, *args):
        print(fmt % args)
