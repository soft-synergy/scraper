"""Plan limit enforcement."""
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy.orm import Session

import models
from stripe_client import get_plan


def _current_month_start() -> datetime:
    now = datetime.utcnow()
    return datetime(now.year, now.month, 1)


def check_campaign_limit(user: models.User, db: Session):
    plan = get_plan(user.plan)
    limit = plan["max_campaigns_month"]
    if limit < 0:
        return  # unlimited
    month_start = _current_month_start()
    count = db.query(models.Campaign).filter(
        models.Campaign.user_id == user.id,
        models.Campaign.created_at >= month_start,
    ).count()
    if count >= limit:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "LIMIT_CAMPAIGNS",
                "plan": user.plan,
                "limit": limit,
                "used": count,
                "message": f"Osiągnąłeś limit {limit} kampanii miesięcznie dla planu {user.plan.title()}. Uaktualnij plan aby tworzyć więcej.",
            },
        )


def check_email_limit(user: models.User, db: Session):
    plan = get_plan(user.plan)
    limit = plan["ai_emails_month"]
    if limit < 0 or limit == 9999:
        return  # unlimited
    if limit == 0:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "LIMIT_EMAILS",
                "plan": user.plan,
                "limit": 0,
                "used": 0,
                "message": "AI cold emaile dostępne od planu Starter. Uaktualnij aby generować spersonalizowane wiadomości.",
            },
        )
    month_start = _current_month_start()
    count = db.query(models.GeneratedEmail).join(models.Website).join(models.Campaign).filter(
        models.Campaign.user_id == user.id,
        models.GeneratedEmail.generated_at >= month_start,
    ).count()
    if count >= limit:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "LIMIT_EMAILS",
                "plan": user.plan,
                "limit": limit,
                "used": count,
                "message": f"Wyczerpałeś limit {limit} AI emaili miesięcznie. Uaktualnij plan Pro dla nielimitowanych wiadomości.",
            },
        )


def check_feature(user: models.User, feature: str):
    plan = get_plan(user.plan)
    if not plan.get(feature, False):
        labels = {
            "csv_export": "Eksport CSV dostępny od planu Starter.",
            "bulk_email": "Bulk generowanie emaili dostępne tylko w planie Pro.",
        }
        raise HTTPException(
            status_code=402,
            detail={
                "code": f"LIMIT_FEATURE_{feature.upper()}",
                "plan": user.plan,
                "feature": feature,
                "message": labels.get(feature, f"Funkcja '{feature}' niedostępna w planie {user.plan.title()}."),
            },
        )


def get_max_sites(user: models.User) -> int:
    return get_plan(user.plan)["max_sites"]


def get_usage(user: models.User, db: Session) -> dict:
    plan = get_plan(user.plan)
    month_start = _current_month_start()
    campaigns_used = db.query(models.Campaign).filter(
        models.Campaign.user_id == user.id,
        models.Campaign.created_at >= month_start,
    ).count()
    emails_used = db.query(models.GeneratedEmail).join(models.Website).join(models.Campaign).filter(
        models.Campaign.user_id == user.id,
        models.GeneratedEmail.generated_at >= month_start,
    ).count()
    return {
        "campaigns_used": campaigns_used,
        "campaigns_limit": plan["max_campaigns_month"],
        "emails_used": emails_used,
        "emails_limit": plan["ai_emails_month"],
        "max_sites": plan["max_sites"],
        "csv_export": plan["csv_export"],
        "bulk_email": plan["bulk_email"],
    }
