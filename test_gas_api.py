import json
import os

import requests
from dotenv import load_dotenv


def main() -> None:
    load_dotenv()

    url = os.environ.get("API_URL")
    rapidapi_key = os.environ.get("RAPIDAPI_KEY")
    rapidapi_host = os.environ.get("RAPIDAPI_HOST")

    missing = [name for name, val in [("API_URL", url), ("RAPIDAPI_KEY", rapidapi_key), ("RAPIDAPI_HOST", rapidapi_host)] if not val]
    if missing:
        print("必要な環境変数が設定されていません: " + ", ".join(missing))
        print("`.env` を確認してください。")
        return

    headers = {
        "x-rapidapi-key": rapidapi_key,
        "x-rapidapi-host": rapidapi_host,
    }

    try:
        response = requests.get(url, headers=headers, timeout=15)
    except requests.RequestException as exc:
        print("通信エラーが発生しました。")
        print(f"詳細: {exc}")
        return

    if response.status_code != 200:
        print("APIリクエストが失敗しました。")
        print(f"ステータスコード: {response.status_code}")
        try:
            print("レスポンス本文:")
            print(response.text)
        except Exception:
            pass
        return

    try:
        data = response.json()
    except ValueError:
        print("JSONのパースに失敗しました。")
        print("レスポンス本文:")
        print(response.text)
        return

    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

