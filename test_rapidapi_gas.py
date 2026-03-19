import os
from typing import Any, Dict, List

import requests
from dotenv import load_dotenv


def load_api_key(env_key: str = "RAPIDAPI_KEY") -> str:
    """
    Load the RapidAPI key from a .env file.

    Raises:
        RuntimeError: If the environment variable is not set.
    """
    # Load variables from .env into environment (if present)
    load_dotenv()

    api_key = os.getenv(env_key)
    if not api_key:
        raise RuntimeError(
            f"Environment variable '{env_key}' is not set. "
            "Please create a .env file and define it, e.g.\n"
            "RAPIDAPI_KEY=your_rapidapi_key_here"
        )
    return api_key


def fetch_gas_prices_by_zip(zip_code: str) -> Dict[str, Any]:
    """
    Fetch gas station information near the specified US zip code.

    NOTE:
        - Replace the placeholder URL and headers below with the actual
          RapidAPI endpoint and host values when you have them.

    Args:
        zip_code: US ZIP code (e.g. "90210").

    Returns:
        Parsed JSON response as a Python dictionary.

    Raises:
        RuntimeError: If the HTTP response status is not 200.
        requests.RequestException: For network-related errors.
    """
    api_key = load_api_key()

    # TODO: Replace this with the real RapidAPI gas prices endpoint
    url = "https://example-gas-api.p.rapidapi.com/prices"

    headers = {
        "X-RapidAPI-Key": api_key,
        # TODO: Replace with actual host, e.g. "us-gas-prices.p.rapidapi.com"
        "X-RapidAPI-Host": "example-gas-api.p.rapidapi.com",
    }

    # 多くのRapidAPIエンドポイントはクエリパラメータでZIPコードを渡します。
    # 実際のパラメータ名はご利用のAPI仕様に合わせて変更してください。
    params = {
        "zip": zip_code,
    }

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
    except requests.RequestException as exc:
        # ネットワークエラーやタイムアウトなど
        raise RuntimeError(f"Failed to call RapidAPI endpoint: {exc}") from exc

    if response.status_code != 200:
        # ステータスコードが200以外の場合のエラーハンドリング
        # 可能であればレスポンスボディも一緒に出力してデバッグしやすくします。
        raise RuntimeError(
            f"RapidAPI request failed with status code {response.status_code}. "
            f"Response body: {response.text}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError("Response is not valid JSON.") from exc


def pretty_print_gas_data(data: Dict[str, Any]) -> None:
    """
    Extract and pretty-print gas station information from JSON data.

    This function assumes that the API response contains a list of stations.
    Since実際のスキーマはまだ不明なので、典型的な構造を仮定しつつ、
    ある程度柔軟にキーを探すようにしています。
    """
    # まず、「stations」や「data」などにリストが格納されていそうなキーを探します。
    station_list: List[Dict[str, Any]] = []

    candidate_keys = ["stations", "data", "results", "items"]
    for key in candidate_keys:
        value = data.get(key)
        if isinstance(value, list):
            station_list = value  # type: ignore[assignment]
            break

    # もしトップレベルがすでにリストなら、そのまま扱います。
    if not station_list and isinstance(data, list):
        station_list = data  # type: ignore[assignment]

    if not station_list:
        print("ガソリンスタンド情報が見つかりませんでした。レスポンス形式を確認してください。")
        print("Raw data (truncated):")
        text_repr = str(data)
        print(text_repr[:1000])  # 長すぎる場合に備えて先頭1000文字だけ表示
        return

    print("=" * 60)
    print(f"取得したガソリンスタンド数: {len(station_list)}")
    print("=" * 60)

    for idx, station in enumerate(station_list, start=1):
        # スタンド名の候補キー
        name = (
            station.get("name")
            or station.get("station")
            or station.get("brand")
            or "名称不明"
        )

        # 住所 or 緯度経度の候補キー
        address = (
            station.get("address")
            or station.get("location")
            or station.get("vicinity")
        )
        lat = station.get("lat") or station.get("latitude")
        lng = station.get("lon") or station.get("lng") or station.get("longitude")

        if not address and (lat is not None and lng is not None):
            address = f"lat={lat}, lng={lng}"

        # ガソリン価格の候補キー
        # "price", "regular", "gas_price" など、よくありそうなキーを順にチェック
        price = (
            station.get("price")
            or station.get("regular")
            or station.get("gas_price")
            or station.get("cash_price")
        )

        print(f"[{idx}] スタンド名: {name}")
        print(f"    住所/位置: {address or '不明'}")
        print(f"    ガソリン価格: {price if price is not None else '不明'}")
        print("-" * 60)


def main() -> None:
    """
    シンプルなテスト実行用エントリポイント。

    デフォルトではZIPコード「90210」でAPIを叩きます。
    """
    # 必要に応じてここを書き換えてテストできます
    zip_code = "90210"  # Beverly Hills etc.

    print(f"ZIPコード {zip_code} 周辺のガソリン価格情報を取得します...")
    try:
        data = fetch_gas_prices_by_zip(zip_code)
    except Exception as exc:  # 包括的にキャッチしてテスト時にわかりやすく表示
        print("API呼び出し中にエラーが発生しました。")
        print(f"詳細: {exc}")
        return

    pretty_print_gas_data(data)


if __name__ == "__main__":
    main()

