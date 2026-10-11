
import json
import os
import time
import urllib.parse
import urllib.request
import threading

import scratchattach as sa


PROJECT_ID = os.environ["SCRATCH_PROJECT_ID"]
USERNAME = os.environ["SCRATCH_USERNAME"]
SESSION_ID = os.environ["SCRATCH_SESSION_ID"]
WEATHER_API_URL = os.environ["WEATHER_API_URL"].rstrip("/")
WEATHER_API_SECRET = os.environ["WEATHER_API_SECRET"]

REQUEST_VAR = "req"
CITY_VAR = "city"
DONE_VAR = "done"

# 同じリクエストを二重処理しない
last_request = None
request_lock = threading.Lock()


def get_weather(city_id):
    url = WEATHER_API_URL + "?" + urllib.parse.urlencode({
        "city": city_id
    })

    req = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer " + WEATHER_API_SECRET,
            "User-Agent": "ScratchWeatherWorker/1.0",
        }
    )

    with urllib.request.urlopen(req, timeout=25) as response:
        return json.loads(response.read().decode("utf-8"))


def handle_request(cloud, request_id):
    global last_request

    with request_lock:
        if request_id == last_request:
            return

        last_request = request_id

    try:
        city_id = int(cloud.get_var(CITY_VAR))
        if city_id not in (1, 2, 3, 4, 5):
            print("Unknown city:", city_id)
            return

        print("Weather request:", request_id, "city:", city_id)

        result = get_weather(city_id)

        # 結果を更新。各値は11桁以下の整数
        cloud.set_vars({
            "current": str(result["current"]),
            "day1": str(result["day1"]),
            "day2": str(result["day2"]),
            "day3": str(result["day3"]),
        }, intelligent_waits=True)

        # すべての結果を書いたあとに完了番号を更新
        cloud.set_var(DONE_VAR, str(request_id))

        print("Weather updated:", request_id)

    except Exception as exc:
        print("Request failed:", repr(exc))
        # 失敗時は done を更新しない。Scratch側で待機を続けられる。


def main():
    print("Logging in to Scratch...")
    session = sa.login_by_id(
        SESSION_ID,
        username=USERNAME
    )

    cloud = session.connect_cloud(PROJECT_ID)
    events = cloud.events()

    @events.event
    def on_set(activity):
        if activity.var != REQUEST_VAR:
            return

        try:
            request_id = int(activity.value)
        except (ValueError, TypeError):
            return

        handle_request(cloud, request_id)

    @events.event
    def on_ready():
        print("Cloud Events connected")

    print("Starting Cloud Events...")
    events.start()

    # Render Background Workerを終了させず、常時動作させる
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
