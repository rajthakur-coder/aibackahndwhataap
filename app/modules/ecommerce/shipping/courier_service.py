import re

import requests

from app.config import settings


REQUEST_TIMEOUT = 15


def fetch_courier_tracking(*, awb: str | None = None, order_id: str | None = None) -> dict | None:
    awb = str(awb or "").strip()
    order_id = str(order_id or "").strip()
    if not awb and not order_id:
        return None

    shiprocket = _fetch_shiprocket_tracking(awb=awb, order_id=order_id)
    if shiprocket:
        return shiprocket
    return None


def _fetch_shiprocket_tracking(*, awb: str, order_id: str) -> dict | None:
    token = getattr(settings, "SHIPROCKET_TOKEN", None)
    if not token:
        return None

    headers = {"Authorization": f"Bearer {token}"}
    candidates = []
    if awb:
        candidates.append(f"https://apiv2.shiprocket.in/v1/external/courier/track/awb/{awb}")
    if order_id and _looks_numeric(order_id):
        candidates.append(f"https://apiv2.shiprocket.in/v1/external/courier/track?order_id={order_id}")

    for url in candidates:
        try:
            response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            if response.status_code == 404:
                continue
            response.raise_for_status()
            normalized = _normalize_shiprocket_response(response.json())
            if normalized:
                return normalized
        except Exception:
            continue
    return None


def _normalize_shiprocket_response(payload) -> dict | None:
    data = payload
    if isinstance(payload, dict):
        data = payload.get("tracking_data") or payload.get("data") or payload
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return None

    shipment_track = data.get("shipment_track")
    if isinstance(shipment_track, list) and shipment_track:
        data = {**data, **shipment_track[0]}

    shipment_status = data.get("shipment_status") or data.get("current_status") or data.get("status")
    tracking_url = data.get("track_url") or data.get("tracking_url")
    awb = data.get("awb_code") or data.get("awb") or data.get("tracking_number")
    courier = data.get("courier_name") or data.get("courier_company") or data.get("courier")
    eta = data.get("edd") or data.get("expected_delivery_date") or data.get("eta")
    current_location = data.get("current_location") or data.get("location")

    if not any((shipment_status, tracking_url, awb, courier, eta, current_location)):
        return None
    return {
        "provider": "shiprocket",
        "status": shipment_status,
        "tracking_url": tracking_url,
        "tracking_number": awb,
        "courier_company": courier,
        "eta": eta,
        "current_location": current_location,
        "raw": payload,
    }


def _looks_numeric(value: str) -> bool:
    return bool(re.fullmatch(r"\d+", value or ""))
