"""Stripe billing integration."""
import os
from typing import Optional
import stripe

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

PLANS = {
    "free": {
        "name": "Free",
        "max_campaigns_month": 2,
        "max_sites": 50,
        "ai_emails_month": 0,
        "csv_export": False,
        "bulk_email": False,
        "price_id": None,
        "price_pln": 0,
        "popular": False,
        "features": [
            "2 kampanie miesięcznie",
            "50 stron na kampanię",
            "Analiza SSL i bezpieczeństwa",
            "Wykrywanie przestarzałych technologii",
            "Ekstrakcja kontaktów",
        ],
        "missing": ["Eksport CSV", "AI Cold Emaile", "Bulk generowanie"],
    },
    "starter": {
        "name": "Starter",
        "max_campaigns_month": 20,
        "max_sites": 500,
        "ai_emails_month": 100,
        "csv_export": True,
        "bulk_email": False,
        "price_id": os.environ.get("STRIPE_STARTER_PRICE_ID", ""),
        "price_pln": 49,
        "popular": False,
        "features": [
            "20 kampanii miesięcznie",
            "500 stron na kampanię",
            "100 AI cold emaili miesięcznie",
            "Eksport do CSV",
            "Wszystkie źródła discovery",
            "Priorytetowe wsparcie",
        ],
        "missing": ["Bulk generowanie emaili"],
    },
    "pro": {
        "name": "Pro",
        "max_campaigns_month": 9999,
        "max_sites": 5000,
        "ai_emails_month": 9999,
        "csv_export": True,
        "bulk_email": True,
        "price_id": os.environ.get("STRIPE_PRO_PRICE_ID", ""),
        "price_pln": 149,
        "popular": True,
        "features": [
            "Nieograniczone kampanie",
            "5000 stron na kampanię",
            "Nieograniczone AI cold emaile",
            "Bulk generowanie emaili",
            "Eksport CSV + priorytet",
            "Wsparcie 1:1",
        ],
        "missing": [],
    },
}


def get_plan(plan_name: str) -> dict:
    return PLANS.get(plan_name, PLANS["free"])


def create_checkout_session(
    user_id: int,
    user_email: str,
    stripe_customer_id: Optional[str],
    plan: str,
    base_url: str,
) -> object:
    price_id = PLANS[plan]["price_id"]
    if not price_id:
        raise ValueError(f"No price ID configured for plan: {plan}")
    params = {
        "payment_method_types": ["card"],
        "line_items": [{"price": price_id, "quantity": 1}],
        "mode": "subscription",
        "success_url": f"{base_url}/app?upgraded=1&plan={plan}",
        "cancel_url": f"{base_url}/app?cancelled=1",
        "allow_promotion_codes": True,
        "metadata": {"user_id": str(user_id), "plan": plan},
    }
    if stripe_customer_id:
        params["customer"] = stripe_customer_id
    else:
        params["customer_email"] = user_email
    return stripe.checkout.Session.create(**params)


def create_portal_session(customer_id: str, base_url: str) -> object:
    return stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=f"{base_url}/app?billing=1",
    )


def handle_webhook_event(payload: bytes, sig_header: str):
    """Returns (event_type, data_dict) or raises."""
    event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
    return event["type"], event["data"]["object"]
