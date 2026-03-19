import os
import time
from collections import deque
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv, dotenv_values
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core_calculator import (
    calculate_net_savings,
    calculate_net_savings_one_way_detour,
    break_even_distance_one_way,
)


load_dotenv()

app = FastAPI(title="Worth The Drive? API", version="0.1.0")

allowed_origins_raw = os.environ.get("ALLOWED_ORIGINS", "").strip()
allowed_origins = [o.strip() for o in allowed_origins_raw.split(",") if o.strip()]
if allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["*"],
    )

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


_CACHE: Dict[str, Tuple[float, Any]] = {}
_REQUEST_LOG: Dict[str, Deque[float]] = {}
_DAILY_QUOTA: Dict[str, Tuple[str, int]] = {}


def _get_env(name: str) -> str:
    """
    Get an environment variable, falling back to .env file if needed.

    This avoids issues where uvicorn reload/process boundaries lose in-memory env.
    """
    # First, check real environment
    value = os.environ.get(name)
    if value:
        return value

    # Fallback: read directly from .env on disk
    env_values = dotenv_values()
    value = env_values.get(name)
    if not value:
        raise RuntimeError(f"Missing environment variable: {name}")

    # Cache into os.environ so subsequent calls are fast
    os.environ[name] = value
    return value


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # Use first IP in X-Forwarded-For chain
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _require_app_api_key(request: Request) -> None:
    """
    Very simple shared-secret auth to prevent public abuse.

    If APP_API_KEY is empty/unset, auth is not enforced.
    """
    expected = os.environ.get("APP_API_KEY", "")
    if not expected:
        return
    provided = request.headers.get("x-app-api-key") or request.headers.get("X-App-Api-Key")
    if provided != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _rate_limit_or_429(request: Request) -> None:
    """
    In-memory IP rate limit.

    Defaults:
      - RATE_LIMIT_PER_MINUTE=60
    """
    limit = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "60"))
    if limit <= 0:
        return

    ip = _client_ip(request)
    now = time.time()
    window = 60.0

    q = _REQUEST_LOG.get(ip)
    if q is None:
        q = deque()
        _REQUEST_LOG[ip] = q

    # Drop old timestamps
    cutoff = now - window
    while q and q[0] < cutoff:
        q.popleft()

    if len(q) >= limit:
        raise HTTPException(status_code=429, detail="Too Many Requests")

    q.append(now)


def _rate_limit_or_429_custom(request: Request, limit: int) -> None:
    if limit <= 0:
        return
    ip = _client_ip(request)
    now = time.time()
    window = 60.0

    q = _REQUEST_LOG.get(ip)
    if q is None:
        q = deque()
        _REQUEST_LOG[ip] = q

    cutoff = now - window
    while q and q[0] < cutoff:
        q.popleft()

    if len(q) >= limit:
        raise HTTPException(status_code=429, detail="Too Many Requests")

    q.append(now)


def _utc_day_key(dt: Optional[datetime] = None) -> str:
    now = dt or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d")


def _utc_next_midnight_iso() -> str:
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).date()
    nxt = datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=timezone.utc)
    return nxt.isoformat().replace("+00:00", "Z")


def _get_daily_limit() -> int:
    raw = (os.environ.get("FREE_DAILY_LIMIT") or "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            return 20
    raw = (dotenv_values().get("FREE_DAILY_LIMIT") or "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            return 20
    return 20


def _consume_daily_quota_or_429(request: Request) -> Dict[str, Any]:
    """
    Simple per-IP daily quota reset at UTC midnight.
    """
    limit = _get_daily_limit()
    ip = _client_ip(request)
    day = _utc_day_key()
    prev = _DAILY_QUOTA.get(ip)
    if prev and prev[0] == day:
        used = prev[1]
    else:
        used = 0

    if limit > 0 and used >= limit:
        raise HTTPException(
            status_code=429,
            detail={
                "message": "Daily free limit reached",
                "limit": limit,
                "used": used,
                "resets_at_utc": _utc_next_midnight_iso(),
            },
        )

    used_next = used + 1
    _DAILY_QUOTA[ip] = (day, used_next)
    remaining = max(0, limit - used_next) if limit > 0 else None
    return {
        "limit": limit,
        "used": used_next,
        "remaining": remaining,
        "resets_at_utc": _utc_next_midnight_iso(),
    }


def _cache_get(key: str) -> Optional[Any]:
    ttl_seconds = int(os.environ.get("CACHE_TTL_SECONDS", "300"))
    item = _CACHE.get(key)
    if not item:
        return None
    saved_at, payload = item
    if (time.time() - saved_at) > ttl_seconds:
        _CACHE.pop(key, None)
        return None
    return payload


def _cache_set(key: str, payload: Any) -> None:
    _CACHE[key] = (time.time(), payload)


def _rapidapi_headers() -> Dict[str, str]:
    return {
        "x-rapidapi-key": _get_env("RAPIDAPI_KEY"),
        "x-rapidapi-host": _get_env("RAPIDAPI_HOST"),
    }


def _fetch_json(url: str, headers: Dict[str, str], params: Optional[Dict[str, str]] = None) -> Any:
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Upstream request error: {exc}") from exc

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail={"message": "Upstream returned non-200", "status_code": resp.status_code, "body": resp.text},
        )

    try:
        return resp.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Upstream returned invalid JSON") from exc


def _liters_to_gallons(liters: float) -> float:
    return liters / 3.785411784


def _parse_price_to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().replace("$", "").replace(",", "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _car_mpg_from_type(car_type: Optional[str]) -> float:
    if not car_type:
        return 25.0
    ct = car_type.strip().lower()
    if ct in {"compact", "small", "sedan"}:
        return 28.0
    if ct in {"suv", "cuv"}:
        return 22.0
    if ct in {"truck", "pickup", "van"}:
        return 18.0
    return 25.0


def _compute_break_even_distance_miles(
    current_price: float,
    target_price: float,
    car_mpg: float,
    gallons_needed: float,
) -> float:
    # Net Savings = (current-target)*gallons - (2d/car_mpg)*target
    # Solve Net Savings=0 for d (one-way miles):
    # d = ((current-target)*gallons*car_mpg) / (2*target)
    if target_price <= 0 or car_mpg <= 0 or gallons_needed <= 0:
        return 0.0
    simple = (current_price - target_price) * gallons_needed
    if simple <= 0:
        return 0.0
    return (simple * car_mpg) / (2 * target_price)


def _reverse_geocode_state_name(latitude: float, longitude: float) -> Optional[str]:
    """
    Best-effort reverse geocoding using OpenStreetMap Nominatim.
    This is intentionally lightweight (no API key) for prototype use.
    """
    cache_key = f"revgeo:{round(latitude, 3)},{round(longitude, 3)}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    url = "https://nominatim.openstreetmap.org/reverse"
    params = {
        "format": "jsonv2",
        "lat": str(latitude),
        "lon": str(longitude),
        "zoom": "5",
        "addressdetails": "1",
    }
    headers = {"User-Agent": "worth-the-drive-prototype/0.1 (local)"}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
    except requests.RequestException:
        return None

    if resp.status_code != 200:
        return None

    try:
        data = resp.json()
    except ValueError:
        return None

    state = None
    address = data.get("address") if isinstance(data, dict) else None
    if isinstance(address, dict):
        state = address.get("state")

    if isinstance(state, str) and state.strip():
        _cache_set(cache_key, state)
        return state
    return None


def _get_state_average_regular_price_usd(state_name: str) -> Optional[float]:
    """
    Fetch all-state prices (allUsaPrice) and return the state's regular price.
    """
    cache_key = "all-usa-price-json"
    data = _cache_get(cache_key)
    if data is None:
        try:
            url = _get_env("ALL_USA_PRICE_URL")
            headers = _rapidapi_headers()
        except RuntimeError:
            return None
        data = _fetch_json(url, headers=headers)
        _cache_set(cache_key, data)

    if not isinstance(data, dict):
        return None
    results = data.get("result")
    if not isinstance(results, list):
        return None

    needle = state_name.strip().lower()
    for item in results:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if isinstance(name, str) and name.strip().lower() == needle:
            return _parse_price_to_float(item.get("regular"))
    return None


def _decision_label(
    within_tolerance: bool,
    net: float,
    minutes_one_way: float,
    max_minutes_one_way: Optional[float],
) -> str:
    if not within_tolerance:
        return "outside_tolerance" if net > 0 else "outside_not_worth"
    if net <= 0:
        return "not_worth_it"
    if max_minutes_one_way and max_minutes_one_way > 0 and minutes_one_way <= max_minutes_one_way * 0.65 and net >= 1.0:
        return "strong"
    return "good"


def _compute_recommendations(
    latitude: float,
    longitude: float,
    *,
    car_type: Optional[str],
    liters_needed: float,
    max_minutes_one_way: Optional[float],
    avg_speed_mph: float,
    trip_mode: str = "round_trip",
    baseline_price_usd_per_gal: Optional[float] = None,
) -> Dict[str, Any]:
    stations = _mock_fetch_nearby_stations(latitude, longitude)
    car_mpg = _car_mpg_from_type(car_type)
    gallons_needed = _liters_to_gallons(liters_needed)
    one_way_mode = trip_mode.strip().lower() in {"one_way", "oneway", "one-way"}

    if not stations:
        return {
            "recommendations": [],
            "recommendations_within_tolerance": [],
            "recommendation_beyond_tolerance": None,
            "map_pins": [],
            "assumptions": {
                "car_mpg": car_mpg,
                "liters_needed": liters_needed,
                "gallons_needed": round(gallons_needed, 3),
                "avg_speed_mph": avg_speed_mph,
                "max_minutes_one_way": max_minutes_one_way,
                "trip_mode": "one_way_detour" if one_way_mode else "round_trip",
            },
        }

    nearest = min(
        stations,
        key=lambda s: _haversine_miles(latitude, longitude, float(s["latitude"]), float(s["longitude"])),
    )

    state_name = _reverse_geocode_state_name(latitude, longitude)
    baseline_from_state = None
    if state_name:
        baseline_from_state = _get_state_average_regular_price_usd(state_name)

    if baseline_price_usd_per_gal is not None and baseline_price_usd_per_gal > 0:
        current_price = float(baseline_price_usd_per_gal)
        baseline_source = "user_override"
    else:
        current_price = baseline_from_state if baseline_from_state is not None else float(nearest.get("price", 0.0))
        baseline_source = "state_average_regular" if baseline_from_state is not None else "nearest_station_fallback"

    enriched: List[Dict[str, Any]] = []
    for s in stations:
        dist = _haversine_miles(latitude, longitude, float(s["latitude"]), float(s["longitude"]))
        target_price = float(s.get("price", 0.0))
        minutes_one_way = (dist / avg_speed_mph) * 60.0 if avg_speed_mph > 0 else 0.0
        if one_way_mode:
            net = calculate_net_savings_one_way_detour(
                current_price=current_price,
                target_price=target_price,
                distance_miles=dist,
                car_mpg=car_mpg,
                gallons_needed=gallons_needed,
            )
        else:
            net = calculate_net_savings(
                current_price=current_price,
                target_price=target_price,
                distance_miles=dist,
                car_mpg=car_mpg,
                gallons_needed=gallons_needed,
            )
        within_tolerance = True
        if max_minutes_one_way is not None:
            within_tolerance = minutes_one_way <= max_minutes_one_way
        label = _decision_label(within_tolerance, net, minutes_one_way, max_minutes_one_way)
        enriched.append(
            {
                "id": s.get("id"),
                "name": s.get("name"),
                "price": target_price,
                "distance_miles": round(dist, 3),
                "distance_km": round(dist * 1.609344, 2),
                "time_minutes_one_way": round(minutes_one_way, 1),
                "time_minutes_round_trip": round(minutes_one_way * 2, 1),
                "net_savings": net,
                "within_tolerance": within_tolerance,
                "decision": label,
                "latitude": float(s["latitude"]),
                "longitude": float(s["longitude"]),
            }
        )

    within_list = [x for x in enriched if x["within_tolerance"]]
    outside_positive = [x for x in enriched if (not x["within_tolerance"]) and x["net_savings"] > 0]

    within_sorted = sorted(within_list, key=lambda x: -x["net_savings"])
    top3_within = within_sorted[:3]

    best_outside = None
    if outside_positive:
        best_outside = max(outside_positive, key=lambda x: x["net_savings"])
        top3_ids = {x["id"] for x in top3_within}
        if best_outside["id"] in top3_ids:
            best_outside = None

    max_worth_distance = 0.0
    best_in_range_net = 0.0
    best_in_range = None
    for item in within_list:
        if item["net_savings"] > 0:
            d = float(item["distance_miles"])
            if d > max_worth_distance:
                max_worth_distance = d
            if item["net_savings"] > best_in_range_net:
                best_in_range_net = item["net_savings"]
                best_in_range = item

    map_pins: List[Dict[str, Any]] = [
        {
            "lat": float(e["latitude"]),
            "lng": float(e["longitude"]),
            "name": e.get("name"),
            "net_savings": e["net_savings"],
            "within_tolerance": e["within_tolerance"],
            "highlight": e["id"] in {x["id"] for x in top3_within} or (best_outside and e["id"] == best_outside["id"]),
        }
        for e in enriched
    ]

    return {
        "recommendations": top3_within,
        "recommendations_within_tolerance": top3_within,
        "recommendation_beyond_tolerance": best_outside,
        "map_pins": map_pins,
        "assumptions": {
            "car_mpg": car_mpg,
            "liters_needed": liters_needed,
            "gallons_needed": round(gallons_needed, 3),
            "avg_speed_mph": avg_speed_mph,
            "max_minutes_one_way": max_minutes_one_way,
            "current_price_assumed": current_price,
            "baseline_state_name": state_name,
            "baseline_price_source": baseline_source,
            "trip_mode": "one_way_detour" if one_way_mode else "round_trip",
        },
        "max_worth_distance_miles": round(max_worth_distance, 2),
        "max_worth_distance_km": round(max_worth_distance * 1.609344, 2),
        "max_worth_time_minutes_one_way": round((max_worth_distance / avg_speed_mph) * 60.0, 1) if avg_speed_mph > 0 else 0.0,
        "best_pick_in_range": best_in_range,
    }


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r_miles = 3958.7613
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r_miles * c


def _mock_fetch_nearby_stations(latitude: float, longitude: float) -> List[Dict[str, Any]]:
    """
    Mock function standing in for a RapidAPI call.
    Replace this with a real upstream call later.
    """
    # Deterministic pseudo "nearby" stations around current location
    stations: List[Dict[str, Any]] = []
    offsets = [
        (0.010, 0.005, 3.49, "Chevron"),
        (0.008, -0.006, 3.39, "Shell"),
        (-0.006, 0.010, 3.29, "ARCO"),
        (-0.012, -0.004, 3.45, "Mobil"),
        (0.015, -0.012, 3.25, "Costco Gas"),
        (-0.018, 0.014, 3.55, "76"),
        (0.022, 0.018, 3.31, "Valero"),
        (-0.025, -0.016, 3.42, "Circle K"),
    ]
    for i, (dlat, dlon, price, brand) in enumerate(offsets, start=1):
        stations.append(
            {
                "id": f"mock-{i}",
                "name": f"{brand} #{i}",
                "latitude": latitude + dlat,
                "longitude": longitude + dlon,
                "price": float(price),
                "address": "Mock address",
            }
        )
    return stations


class RecommendRequest(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    car_type: Optional[str] = Field(default=None, description="compact | suv | truck | etc.")
    liters_needed: float = Field(default=40.0, gt=0, description="Planned purchase amount (liters).")
    max_minutes_one_way: Optional[float] = Field(default=15.0, gt=0, description="User travel tolerance (one-way minutes).")
    avg_speed_mph: float = Field(default=30.0, gt=0, description="Assumed average speed (mph).")
    trip_mode: str = Field(default="round_trip", description="round_trip | one_way")
    baseline_price_usd_per_gal: Optional[float] = Field(default=None, gt=0, description="Override baseline $/gal.")


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=500, detail="index.html not found")
    return FileResponse(str(index_path))


@app.get("/about")
def about() -> FileResponse:
    about_path = STATIC_DIR / "about.html"
    if not about_path.exists():
        raise HTTPException(status_code=500, detail="about.html not found")
    return FileResponse(str(about_path))


@app.get("/api/public/app-config")
def public_app_config() -> Dict[str, Optional[str]]:
    """
    Public app configuration for the frontend (non-secret).
    """
    donation_paypal = (os.environ.get("DONATION_PAYPAL_URL") or "").strip()
    donation_coffee = (os.environ.get("DONATION_COFFEE_URL") or "").strip()
    if not donation_paypal or not donation_coffee:
        envv = dotenv_values()
        donation_paypal = donation_paypal or (envv.get("DONATION_PAYPAL_URL") or "").strip()
        donation_coffee = donation_coffee or (envv.get("DONATION_COFFEE_URL") or "").strip()

    return {
        "donationPaypalUrl": donation_paypal or None,
        "donationCoffeeUrl": donation_coffee or None,
    }


@app.get("/api/state-codes")
def get_state_codes(request: Request) -> Any:
    """
    Proxy endpoint for RapidAPI Gas Price API: USA state codes.

    Expects:
      - API_URL: e.g. https://gas-price.p.rapidapi.com/usaStateCode
      - RAPIDAPI_KEY
      - RAPIDAPI_HOST
    """
    _require_app_api_key(request)
    _rate_limit_or_429(request)

    cached = _cache_get("state-codes")
    if cached is not None:
        return cached

    try:
        url = _get_env("API_URL")
        headers = _rapidapi_headers()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    data = _fetch_json(url, headers=headers)

    _cache_set("state-codes", data)
    return data


@app.get("/api/state-prices")
def get_state_prices(state: str, request: Request) -> Any:
    """
    Proxy endpoint for RapidAPI Gas Price API: prices by state code (A).

    Configure one of the following patterns via .env:
      - STATE_PRICES_URL=https://.../state/{state}
      - STATE_PRICES_URL=https://.../statePrice   (uses query param)

    Optional:
      - STATE_PRICES_PARAM_NAME=state  (default)
    """
    _require_app_api_key(request)
    _rate_limit_or_429(request)

    state = state.strip().upper()
    if not state or len(state) != 2:
        raise HTTPException(status_code=400, detail="state must be a 2-letter code (e.g. CA)")

    cache_key = f"state-prices:{state}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        base_url = _get_env("STATE_PRICES_URL")
        headers = _rapidapi_headers()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    param_name = os.environ.get("STATE_PRICES_PARAM_NAME", "state").strip() or "state"

    if "{state}" in base_url:
        url = base_url.replace("{state}", state)
        data = _fetch_json(url, headers=headers)
    else:
        data = _fetch_json(base_url, headers=headers, params={param_name: state})

    _cache_set(cache_key, data)
    return data


@app.get("/api/all-state-prices")
def get_all_state_prices(request: Request) -> Any:
    """
    Return all US state gas prices via RapidAPI allUsaPrice endpoint.

    Expects:
      - ALL_USA_PRICE_URL
      - RAPIDAPI_KEY
      - RAPIDAPI_HOST
    """
    _require_app_api_key(request)
    _rate_limit_or_429(request)

    cache_key = "all-state-prices"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        url = _get_env("ALL_USA_PRICE_URL")
        headers = _rapidapi_headers()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    data = _fetch_json(url, headers=headers)
    _cache_set(cache_key, data)
    return data


@app.post("/api/recommend")
def recommend(payload: RecommendRequest, request: Request) -> Dict[str, Any]:
    """
    Main GPS-based recommendation endpoint.

    Dummy vehicle assumptions for now:
      - car_mpg=25
      - gallons_needed=15
    """
    _require_app_api_key(request)
    _rate_limit_or_429(request)

    return _compute_recommendations(
        float(payload.latitude),
        float(payload.longitude),
        car_type=payload.car_type,
        liters_needed=float(payload.liters_needed),
        max_minutes_one_way=(float(payload.max_minutes_one_way) if payload.max_minutes_one_way is not None else None),
        avg_speed_mph=float(payload.avg_speed_mph),
        trip_mode=payload.trip_mode,
        baseline_price_usd_per_gal=payload.baseline_price_usd_per_gal,
    )


@app.get("/api/public/map-config")
def public_map_config() -> Dict[str, str]:
    """Google Maps JS API key for the web UI (restrict key by HTTP referrer in Cloud Console)."""
    key = (os.environ.get("GOOGLE_MAPS_API_KEY") or "").strip()
    if not key:
        key = (dotenv_values().get("GOOGLE_MAPS_API_KEY") or "").strip()
    return {"googleMapsApiKey": key}


@app.post("/api/public/recommend")
def public_recommend(payload: RecommendRequest, request: Request) -> Dict[str, Any]:
    """
    Public-friendly recommend endpoint for the browser UI.

    - Does NOT require APP_API_KEY (so the key doesn't have to live in frontend code)
    - Still protected by:
      - Rate limiting (RECOMMEND_RATE_LIMIT_PER_MINUTE, default 20)
      - Optional Origin allow-list (if ALLOWED_ORIGINS is set)
    """
    allowed = allowed_origins
    if allowed:
        origin = request.headers.get("origin")
        if not origin or origin not in allowed:
            raise HTTPException(status_code=403, detail="Origin not allowed")

    limit = int(os.environ.get("RECOMMEND_RATE_LIMIT_PER_MINUTE", "20"))
    _rate_limit_or_429_custom(request, limit=limit)

    quota = _consume_daily_quota_or_429(request)

    result = _compute_recommendations(
        float(payload.latitude),
        float(payload.longitude),
        car_type=payload.car_type,
        liters_needed=float(payload.liters_needed),
        max_minutes_one_way=(float(payload.max_minutes_one_way) if payload.max_minutes_one_way is not None else None),
        avg_speed_mph=float(payload.avg_speed_mph),
        trip_mode=payload.trip_mode,
        baseline_price_usd_per_gal=payload.baseline_price_usd_per_gal,
    )
    if isinstance(result, dict):
        result["quota"] = quota
    return result


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("api_server:app", host="0.0.0.0", port=port, reload=True)

