
import json
import os
import time
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import scratchattach as sa
from flask import Flask, jsonify

app = Flask(__name__)

# Renderの環境変数
PORT = int(os.environ.get("PORT", "10000"))

PROJECT_ID = os.environ["SCRATCH_PROJECT_ID"]
USERNAME = os.environ["SCRATCH_USERNAME"]
SESSION_ID = os.environ["SCRATCH_SESSION_ID"]

WEATHER_API_URL = os.environ["WEATHER_API_URL"].rstrip("/")
WEATHER_API_SECRET = os.environ["WEATHER_API_SECRET"]

# Scratchのクラウド変数名（☁ は付けない）
REQUEST_VAR = "req"
CITY_VAR = "city"
DONE_VAR = "done"

# 状態確認ページ用
status = {
    "web": True,
    "scratch_connected": False,
    "last_connected": None,
    "last_request": None,
    "last_success": None,
    "last_error": None,
    "message": "Scratchへの接続待ち",
}

status_lock = threading.Lock()
request_lock = threading.Lock()
last_request_id = None


def update_status(**kwargs):
    with status_lock:
        status.update(kwargs)


def get_weather(city_id):
    """Vercelの天気APIから天気情報を取得する"""
    query = urllib.parse.urlencode({"city": city_id})
    url = f"{WEATHER_API_URL}?{query}"

    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {WEATHER_API_SECRET}",
            "User-Agent": "ScratchWeatherWorker/1.0",
        },
    )

    with urllib.request.urlopen(req, timeout=25) as response:
        return json.loads(response.read().decode("utf-8"))


def handle_request(cloud, request_id):
    global last_request_id

    # 同じリクエストの重複処理を防ぐ
    with request_lock:
        if request_id == last_request_id:
            return

        last_request_id = request_id
        update_status(
            last_request=str(request_id),
            message="天気を取得しています",
            last_error=None,
        )

        try:
            city_value = cloud.get_var(CITY_VAR)
            city_id = int(city_value)

            # 都道府県IDは1～47
            if city_id < 1 or city_id > 47:
                raise ValueError(
                    f"都道府県IDが範囲外です: {city_id}"
                )

            print(
                f"[{datetime.now().isoformat()}] "
                f"天気取得開始: req={request_id}, city={city_id}"
            )

            result = get_weather(city_id)

            # Vercel APIから4つの数値を受け取る
            values = {
                "current": str(result["current"]),
                "day1": str(result["day1"]),
                "day2": str(result["day2"]),
                "day3": str(result["day3"]),
            }

            # 結果をクラウド変数に保存
            cloud.set_vars(values, intelligent_waits=True)

            # 最後に完了番号を更新
            cloud.set_var(DONE_VAR, str(request_id))

            update_status(
                last_success=datetime.now(timezone.utc).isoformat(),
                message="天気の取得に成功しました",
                last_error=None,
            )

            print(f"天気取得成功: req={request_id}")

        except Exception as exc:
            error = repr(exc)
            print(f"天気取得エラー: {error}")
            update_status(
                message="天気取得に失敗しました",
                last_error=error,
            )


def scratch_loop():
    """Scratch接続を開始し、失敗したら再接続を試みる"""
    while True:
        try:
            update_status(
                scratch_connected=False,
                message="Scratchに接続しています",
            )

            print("Scratchにログインしています...")
            session = sa.login_by_id(
                SESSION_ID,
                username=USERNAME,
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
                now = datetime.now(timezone.utc).isoformat()
                update_status(
                    scratch_connected=True,
                    last_connected=now,
                    message="Scratchに接続しています",
                )
                print("Scratch Cloud Events 接続完了")

            print("Cloud Eventsを開始します...")
            events.start()

            # events.start() が戻っても監視プロセスを維持
            while True:
                time.sleep(30)

        except Exception as exc:
            error = repr(exc)
            print("Scratch接続エラー:", error)

            update_status(
                scratch_connected=False,
                message="Scratchへの接続に失敗。再接続します",
                last_error=error,
            )

            time.sleep(15)


@app.route("/")
def index():
    """ブラウザで開く動作確認ページ"""
    return """
    <!DOCTYPE html>
    <html lang="ja">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Scratch天気サーバー</title>
      <style>
        body {
          font-family: sans-serif;
          max-width: 700px;
          margin: 40px auto;
          padding: 0 18px;
          color: #222;
          background: #f7f9fc;
        }
        .panel {
          background: white;
          padding: 24px;
          border-radius: 14px;
          box-shadow: 0 2px 12px #0001;
        }
        .status { margin: 12px 0; }
        a { color: #1769aa; }
        button {
          padding: 10px 16px;
          border: 0;
          border-radius: 8px;
          background: #1769aa;
          color: white;
          cursor: pointer;
        }
        pre { white-space: pre-wrap; overflow-wrap: anywhere; }
      </style>
    </head>
    <body>
      <div class="panel">
        <h1>Scratch天気サーバー</h1>
        <p>Webサーバーは起動しています。</p>
        <div id="status">状態を確認しています...</div>
        <button onclick="checkStatus()">状態を更新</button>
        <p><a href="/health">ヘルスチェックAPI</a></p>
      </div>
      <script>
        async function checkStatus() {
          const el = document.getElementById("status");
          el.textContent = "確認中...";
          try {
            const res = await fetch("/health", {cache: "no-store"});
            const data = await res.json();
            el.innerHTML = "";
            const pre = document.createElement("pre");
            pre.textContent = JSON.stringify(data, null, 2);
            el.appendChild(pre);
          } catch (e) {
            el.textContent = "状態を取得できませんでした: " + e;
          }
        }
        checkStatus();
        setInterval(checkStatus, 30000);
      </script>
    </body>
    </html>
    """


@app.route("/health")
def health():
    """監視サービス向けの状態確認API"""
    with status_lock:
        snapshot = dict(status)

    # Webが応答していることと、Scratch接続状態を分けて返す
    return jsonify({
        "ok": True,
        "server_time": datetime.now(timezone.utc).isoformat(),
        **snapshot,
    })


if __name__ == "__main__":
    # Scratch接続処理をバックグラウンドスレッドで開始
    thread = threading.Thread(target=scratch_loop, daemon=True)
    thread.start()

    # Renderから指定されたポートでWebサーバーを起動
    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
    )
