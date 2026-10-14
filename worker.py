
import os
import time
import json
import threading
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone

import scratchattach as sa
from flask import Flask, jsonify

app = Flask(__name__)

# =====================================
# 環境変数
# =====================================

PORT = int(os.environ.get("PORT", "10000"))

PROJECT_ID = os.environ["SCRATCH_PROJECT_ID"]
USERNAME = os.environ["SCRATCH_USERNAME"]
SESSION_ID = os.environ["SCRATCH_SESSION_ID"]

WEATHER_API_URL = os.environ["WEATHER_API_URL"].rstrip("/")
WEATHER_API_SECRET = os.environ["WEATHER_API_SECRET"]

# =====================================
# Scratchのクラウド変数
# =====================================

REQUEST_VAR = "req"
CITY_VAR = "city"
DONE_VAR = "done"

RESULT_VARS = ["current", "day1", "day2", "day3"]

# =====================================
# 状態管理
# =====================================

status = {
    "web": True,
    "scratch_connected": False,
    "last_connected": None,
    "last_event": None,
    "last_request": None,
    "last_success": None,
    "last_error": None,
    "message": "起動中",
}

status_lock = threading.Lock()
request_lock = threading.Lock()

last_request_id = None


def log(message):
    now = datetime.now(timezone.utc).isoformat()
    print(f"[{now}] {message}", flush=True)


def update_status(**kwargs):
    with status_lock:
        status.update(kwargs)


# =====================================
# Vercel天気API
# =====================================

def get_weather(city_id):
    # cityパラメータを追加
    separator = "&" if "?" in WEATHER_API_URL else "?"
    query = urllib.parse.urlencode({"city": city_id})
    url = f"{WEATHER_API_URL}{separator}{query}"


    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "ScratchWeatherWorker/1.0",
        },
        method="GET",
    )


    # シークレットはログに出さない
    log(f"[WEATHER API] リクエスト送信: city={city_id}")
    log(f"[WEATHER API] 接続先: {url}")

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            http_status = response.status
            content_type = response.headers.get("Content-Type", "")
            body = response.read().decode("utf-8", errors="replace")

    except urllib.error.HTTPError as exc:
        # 401、403、404、500などの本文を確認
        error_body = exc.read().decode(
            "utf-8", errors="replace"
        )

        log(f"[WEATHER API ERROR] HTTPステータス: {exc.code}")
        log(f"[WEATHER API ERROR] Content-Type: "
            f"{exc.headers.get('Content-Type', '')}")
        log(f"[WEATHER API ERROR] 本文: {error_body[:1500]!r}")

        raise RuntimeError(
            f"天気APIのHTTPエラー: {exc.code}; "
            f"本文={error_body[:500]!r}"
        ) from exc

    except Exception as exc:
        log(f"[WEATHER API ERROR] 通信エラー: {exc!r}")
        raise

    log(f"[WEATHER API] HTTPステータス: {http_status}")
    log(f"[WEATHER API] Content-Type: {content_type}")
    log(f"[WEATHER API] レスポンス長: {len(body)}文字")

    # 空のレスポンスを明示的に検出
    if not body.strip():
        raise RuntimeError(
            "天気APIのレスポンスが空です。"
            f" HTTP={http_status}, Content-Type={content_type}"
        )

    # JSONとして解析
    try:
        result = json.loads(body)
    except json.JSONDecodeError as exc:
        log(f"[WEATHER API ERROR] JSON解析失敗: {exc}")
        log(f"[WEATHER API ERROR] レスポンス本文: {body[:1500]!r}")

        raise RuntimeError(
            "天気APIがJSON以外の応答を返しました。"
            f" HTTP={http_status}, Content-Type={content_type}, "
            f"本文={body[:500]!r}"
        ) from exc

    if not isinstance(result, dict):
        raise ValueError(
            f"API応答がJSONオブジェクトではありません: "
            f"{type(result).__name__}"
        )

    # API側がエラーをJSONで返した場合も検出
    if result.get("error"):
        raise RuntimeError(
            f"天気APIのエラー応答: {result['error']!r}"
        )

    for key in RESULT_VARS:
        if key not in result:
            raise ValueError(
                f"API応答に {key} がありません。"
                f" 応答キー={list(result.keys())}"
            )

    log("[WEATHER API] 取得成功")
    return result


# =====================================
# Scratchリクエスト処理
# =====================================

def handle_request(cloud, request_id):
    global last_request_id

    # 同じreqの重複処理を防ぐ
    with request_lock:
        if request_id == last_request_id:
            log(f"[SKIP] 処理済みリクエスト: req={request_id}")
            return

        last_request_id = request_id

    update_status(
        last_request=str(request_id),
        message="リクエストを受信しました",
        last_error=None,
    )

    try:
        log(f"[REQUEST] 処理開始: req={request_id}")

        city_value = cloud.get_var(CITY_VAR)
        log(f"[CITY] cityの値: {city_value!r}")

        city_id = int(city_value)

        if not 1 <= city_id <= 47:
            raise ValueError(
                f"cityは1～47で指定してください: {city_id}"
            )

        update_status(message="天気を取得しています")

        result = get_weather(city_id)

        values = {
            "current": str(result["current"]),
            "day1": str(result["day1"]),
            "day2": str(result["day2"]),
            "day3": str(result["day3"]),
        }

        log("[SCRATCH] 天気データを書き込みます")
        cloud.set_vars(values, intelligent_waits=True)
        log("[SCRATCH] 天気データの書き込み完了")

        # 全データを書き込んでから完了番号を更新
        cloud.set_var(DONE_VAR, str(request_id))

        log(f"[DONE] req={request_id}, done={request_id}")

        update_status(
            last_success=datetime.now(timezone.utc).isoformat(),
            message="天気取得成功",
            last_error=None,
        )

    except Exception as exc:
        error = repr(exc)
        log(f"[ERROR] リクエスト処理失敗: {error}")

        update_status(
            message="天気取得に失敗しました",
            last_error=error,
        )


# =====================================
# Scratch接続・Cloud Events
# =====================================

def scratch_loop():
    while True:
        try:
            update_status(
                scratch_connected=False,
                message="Scratchに接続しています",
            )

            log("[SCRATCH] ログイン開始")

            session = sa.login_by_id(
                SESSION_ID,
                username=USERNAME,
            )

            log("[SCRATCH] ログイン処理完了")

            # Cloud Events専用接続
            event_cloud = session.connect_cloud(PROJECT_ID)
            log(f"[SCRATCH] イベント接続完了: {PROJECT_ID}")

            # クラウド変数の読み書き用接続
            data_cloud = session.connect_cloud(PROJECT_ID)
            log("[SCRATCH] データ接続完了")

            events = event_cloud.events()

            @events.event
            def on_set(activity):
                var_name = activity.var
                value = activity.value

                log(
                    f"[CLOUD EVENT] "
                    f"var={var_name!r}, value={value!r}"
                )

                update_status(
                    last_event={
                        "var": str(var_name),
                        "value": str(value),
                        "time": datetime.now(
                            timezone.utc
                        ).isoformat(),
                    }
                )

                if var_name != REQUEST_VAR:
                    return

                try:
                    request_id = int(value)
                except (ValueError, TypeError):
                    log("[REQUEST] reqが整数ではないため無視します")
                    return

                # イベント受信をブロックしない
                threading.Thread(
                    target=handle_request,
                    args=(data_cloud, request_id),
                    daemon=True,
                    name=f"weather-request-{request_id}",
                ).start()

            @events.event
            def on_ready():
                now = datetime.now(timezone.utc).isoformat()

                update_status(
                    scratch_connected=True,
                    last_connected=now,
                    message="Scratch Cloud Events接続完了",
                    last_error=None,
                )

                log("[SCRATCH] Cloud Events 接続完了")

            log("[SCRATCH] Cloud Eventsを開始します")
            events.start()

            while True:
                time.sleep(30)

        except Exception as exc:
            error = repr(exc)
            log(f"[SCRATCH ERROR] {error}")

            update_status(
                scratch_connected=False,
                message="Scratch接続エラー。再接続を試みます",
                last_error=error,
            )

            time.sleep(15)


# =====================================
# ステータスページ
# =====================================

@app.route("/")
def index():
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
    max-width: 760px;
    margin: 35px auto;
    padding: 0 18px;
    color: #222;
    background: #f5f7fb;
}
.panel {
    background: white;
    padding: 24px;
    border-radius: 14px;
    box-shadow: 0 2px 12px #0001;
}
button {
    padding: 10px 16px;
    border: 0;
    border-radius: 8px;
    background: #1769aa;
    color: white;
    cursor: pointer;
}
pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
}
a { color: #1769aa; }
</style>
</head>
<body>
<div class="panel">
    <h1>Scratch天気サーバー</h1>
    <p>Webサーバーは起動しています。</p>
    <p>Scratch接続状態とイベント受信状況を確認できます。</p>
    <button onclick="checkStatus()">状態を更新</button>
    <p><a href="/health">JSON形式で確認する</a></p>
    <div id="result">確認中...</div>
</div>
<script>
async function checkStatus() {
    const result = document.getElementById("result");
    result.textContent = "確認中...";
    try {
        const response = await fetch("/health", {
            cache: "no-store"
        });
        const data = await response.json();
        const pre = document.createElement("pre");
        pre.textContent = JSON.stringify(data, null, 2);
        result.replaceChildren(pre);
    } catch (error) {
        result.textContent = "取得失敗: " + error;
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
    with status_lock:
        snapshot = dict(status)

    return jsonify({
        "ok": True,
        "server_time": datetime.now(timezone.utc).isoformat(),
        **snapshot,
    })


@app.route("/favicon.ico")
def favicon():
    return "", 204


# =====================================
# 起動
# =====================================

if __name__ == "__main__":
    log("[SERVER] Scratch天気サーバー起動")

    threading.Thread(
        target=scratch_loop,
        daemon=True,
        name="scratch-cloud-events",
    ).start()

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
    )
