"""
WebLeadScraper — Full SaaS Backend
"""
import asyncio
import csv
import io
import json as _json
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional, Any, Dict

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.orm import Session, joinedload

from database import engine, get_db, Base, SQLALCHEMY_DATABASE_URL, SessionLocal
import models
from auth import get_current_user, get_optional_user, hash_password, verify_password, create_access_token
from limits import check_campaign_limit, check_email_limit, check_feature, get_max_sites, get_usage
from stripe_client import PLANS, get_plan, create_checkout_session, create_portal_session, handle_webhook_event
from scraper.orchestrator import run_campaign
from scraper.email_generator import generate_email
from scraper.mailer import send_email, get_user_smtp_config


# ─── DB Init + Migrations ─────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    # SQLite column migrations (idempotent)
    import sqlite3 as _sqlite3
    db_path = SQLALCHEMY_DATABASE_URL.replace("sqlite:///", "")
    con = _sqlite3.connect(db_path)
    for sql in [
        "ALTER TABLE campaigns ADD COLUMN discovery_log TEXT",
        "ALTER TABLE campaigns ADD COLUMN user_id INTEGER",
        "ALTER TABLE generated_emails ADD COLUMN follow_ups TEXT",
        "ALTER TABLE websites ADD COLUMN page_description TEXT",
        "ALTER TABLE websites ADD COLUMN page_headings TEXT",
        "ALTER TABLE users ADD COLUMN smtp_host TEXT",
        "ALTER TABLE users ADD COLUMN smtp_port INTEGER",
        "ALTER TABLE users ADD COLUMN smtp_login TEXT",
        "ALTER TABLE users ADD COLUMN smtp_password TEXT",
        "ALTER TABLE users ADD COLUMN from_email TEXT",
        "ALTER TABLE users ADD COLUMN from_name TEXT",
    ]:
        try:
            con.execute(sql)
            con.commit()
        except Exception:
            pass
    # Reset stuck campaigns — background tasks don't survive server restarts
    con.execute("UPDATE campaigns SET status='failed' WHERE status IN ('running', 'discovering')")
    con.commit()
    con.close()

    # Start follow-up scheduler background loop
    scheduler_task = asyncio.create_task(_followup_scheduler())
    yield
    scheduler_task.cancel()


app = FastAPI(title="WebLeadScraper", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


# ─── Pydantic Schemas ─────────────────────────────────────────────────────────
class RegisterRequest(BaseModel):
    email: str
    password: str
    name: Optional[str] = None


class LoginRequest(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    name: Optional[str]
    plan: str
    onboarding_done: bool
    created_at: datetime

    class Config:
        from_attributes = True


class CampaignCreate(BaseModel):
    name: str
    keyword: str
    max_sites: int = 200
    country: str = "auto"


class CampaignOut(BaseModel):
    id: int
    name: str
    keyword: str
    country: str = "auto"
    status: str
    max_sites: int
    created_at: datetime
    completed_at: Optional[datetime]
    total_sites: int = 0
    done_sites: int = 0
    error_sites: int = 0

    class Config:
        from_attributes = True


class ContactOut(BaseModel):
    id: int
    type: str
    value: str
    source_url: Optional[str]

    class Config:
        from_attributes = True


class SecurityResultOut(BaseModel):
    is_https: bool
    ssl_valid: Optional[bool]
    ssl_expiry_days: Optional[int]
    ssl_issuer: Optional[str]
    has_hsts: bool
    has_x_frame_options: bool
    has_csp: bool
    has_x_content_type: bool
    has_referrer_policy: bool
    has_mixed_content: bool
    issues: Optional[List[str]]

    class Config:
        from_attributes = True


class OutdatedResultOut(BaseModel):
    copyright_year: Optional[int]
    last_modified_header: Optional[str]
    cms_name: Optional[str]
    cms_version: Optional[str]
    jquery_version: Optional[str]
    has_flash: bool
    has_viewport_meta: bool
    uses_http_only: bool
    issues: Optional[List[str]]

    class Config:
        from_attributes = True


class TechResultOut(BaseModel):
    server_header: Optional[str]
    x_powered_by: Optional[str]
    detected_cms: Optional[str]
    detected_framework: Optional[str]
    detected_cdn: Optional[str]

    class Config:
        from_attributes = True


class WebsiteOut(BaseModel):
    id: int
    campaign_id: int
    url: str
    domain: str
    title: Optional[str]
    status: str
    error_message: Optional[str]
    outdated_score: Optional[int]
    security_score: Optional[int]
    discovered_at: datetime
    analyzed_at: Optional[datetime]
    contacts: List[ContactOut] = []
    security_result: Optional[SecurityResultOut] = None
    outdated_result: Optional[OutdatedResultOut] = None
    tech_result: Optional[TechResultOut] = None

    class Config:
        from_attributes = True


class EmailGenerateRequest(BaseModel):
    sender_name: str = "Your Name"
    sender_offer: str = "profesjonalna strona internetowa"


class EmailOut(BaseModel):
    id: int
    website_id: int
    subject: str
    body: str
    language: str
    recipient_email: Optional[str]
    status: str
    follow_ups: Optional[list] = None
    generated_at: datetime

    class Config:
        from_attributes = True

    @field_validator("follow_ups", mode="before")
    @classmethod
    def parse_follow_ups(cls, v):
        import json as _json
        if isinstance(v, str):
            try:
                return _json.loads(v)
            except Exception:
                return []
        return v or []


class CheckoutRequest(BaseModel):
    plan: str


class LeadRequest(BaseModel):
    email: str
    source: str = "landing"


class SmtpSettingsIn(BaseModel):
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_login: Optional[str] = None
    smtp_password: Optional[str] = None
    from_email: Optional[str] = None
    from_name: Optional[str] = None


class SmtpSettingsOut(BaseModel):
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_login: Optional[str] = None
    smtp_password: Optional[str] = None
    from_email: Optional[str] = None
    from_name: Optional[str] = None


# ─── Helpers ──────────────────────────────────────────────────────────────────
def campaign_to_out(campaign: models.Campaign, db: Session) -> Dict[str, Any]:
    total = db.query(models.Website).filter(models.Website.campaign_id == campaign.id).count()
    done = db.query(models.Website).filter(
        models.Website.campaign_id == campaign.id,
        models.Website.status == "done"
    ).count()
    error = db.query(models.Website).filter(
        models.Website.campaign_id == campaign.id,
        models.Website.status == "error"
    ).count()
    return {
        "id": campaign.id,
        "name": campaign.name,
        "keyword": campaign.keyword,
        "country": getattr(campaign, "country", "auto") or "auto",
        "status": campaign.status,
        "max_sites": campaign.max_sites,
        "created_at": campaign.created_at,
        "completed_at": campaign.completed_at,
        "total_sites": total,
        "done_sites": done,
        "error_sites": error,
    }


def _assert_owns(campaign: models.Campaign, user: models.User):
    if campaign.user_id and campaign.user_id != user.id:
        raise HTTPException(status_code=403, detail="Brak dostępu do tej kampanii")


# ─── Pages ────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def landing():
    with open("static/landing.html") as f:
        return f.read()


@app.get("/app", response_class=HTMLResponse)
async def app_page():
    with open("static/app.html") as f:
        return f.read()


@app.get("/manifest.json")
async def manifest():
    return FileResponse("static/manifest.json", media_type="application/manifest+json")


@app.get("/sw.js")
async def service_worker():
    return FileResponse("static/sw.js", media_type="application/javascript", headers={"Service-Worker-Allowed": "/"})


@app.get("/onboarding", response_class=HTMLResponse)
async def onboarding_page():
    with open("static/onboarding.html") as f:
        return f.read()


# ─── Auth ─────────────────────────────────────────────────────────────────────
@app.post("/api/auth/register")
async def register(body: RegisterRequest, db: Session = Depends(get_db)):
    if db.query(models.User).filter(models.User.email == body.email.lower()).first():
        raise HTTPException(status_code=400, detail="Email już zarejestrowany")
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Hasło musi mieć co najmniej 6 znaków")
    user = models.User(
        email=body.email.lower().strip(),
        hashed_password=hash_password(body.password),
        name=body.name,
        plan="pro",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token(user.id)
    return {"token": token, "user": UserOut.model_validate(user)}


@app.post("/api/auth/login")
async def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == body.email.lower()).first()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Nieprawidłowy email lub hasło")
    token = create_access_token(user.id)
    return {"token": token, "user": UserOut.model_validate(user)}


@app.get("/api/auth/me")
async def me(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    usage = get_usage(current_user, db)
    return {
        **UserOut.model_validate(current_user).model_dump(),
        "usage": usage,
        "plan_info": get_plan(current_user.plan),
    }


@app.patch("/api/auth/me")
async def update_me(
    body: dict,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if "onboarding_done" in body:
        current_user.onboarding_done = body["onboarding_done"]
    if "name" in body:
        current_user.name = body["name"]
    db.commit()
    return {"ok": True}


# ─── SMTP Settings ────────────────────────────────────────────────────────────
@app.get("/api/settings/smtp")
async def get_smtp_settings(
    current_user: models.User = Depends(get_current_user),
):
    return SmtpSettingsOut(
        smtp_host=current_user.smtp_host,
        smtp_port=current_user.smtp_port,
        smtp_login=current_user.smtp_login,
        smtp_password=current_user.smtp_password,
        from_email=current_user.from_email,
        from_name=current_user.from_name,
    )


@app.patch("/api/settings/smtp")
async def update_smtp_settings(
    data: SmtpSettingsIn,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(current_user, field, value)
    db.commit()
    return {"ok": True}


# ─── Billing ──────────────────────────────────────────────────────────────────
@app.post("/api/billing/checkout")
async def billing_checkout(
    body: CheckoutRequest,
    request: Request,
    current_user: models.User = Depends(get_current_user),
):
    if body.plan not in ("starter", "pro"):
        raise HTTPException(status_code=400, detail="Plan must be starter or pro")
    if not os.environ.get("STRIPE_SECRET_KEY"):
        raise HTTPException(status_code=503, detail="Stripe not configured. Set STRIPE_SECRET_KEY.")
    base_url = str(request.base_url).rstrip("/")
    try:
        session = create_checkout_session(
            user_id=current_user.id,
            user_email=current_user.email,
            stripe_customer_id=current_user.stripe_customer_id,
            plan=body.plan,
            base_url=base_url,
        )
        return {"url": session.url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stripe error: {str(e)}")


@app.post("/api/billing/portal")
async def billing_portal(
    request: Request,
    current_user: models.User = Depends(get_current_user),
):
    if not current_user.stripe_customer_id:
        raise HTTPException(status_code=400, detail="Brak subskrypcji Stripe")
    base_url = str(request.base_url).rstrip("/")
    try:
        session = create_portal_session(current_user.stripe_customer_id, base_url)
        return {"url": session.url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/billing/webhook")
async def billing_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        event_type, obj = handle_webhook_event(payload, sig_header)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if event_type == "checkout.session.completed":
        user_id = int(obj.get("metadata", {}).get("user_id", 0))
        plan = obj.get("metadata", {}).get("plan", "starter")
        customer_id = obj.get("customer")
        subscription_id = obj.get("subscription")
        if user_id:
            user = db.query(models.User).filter(models.User.id == user_id).first()
            if user:
                user.plan = plan
                user.stripe_customer_id = customer_id
                user.stripe_subscription_id = subscription_id
                db.commit()

    elif event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
        customer_id = obj.get("customer")
        user = db.query(models.User).filter(models.User.stripe_customer_id == customer_id).first()
        if user:
            status = obj.get("status")
            if event_type == "customer.subscription.deleted" or status in ("canceled", "unpaid"):
                user.plan = "free"
                user.stripe_subscription_id = None
            elif status == "active":
                # Keep plan from metadata if available
                pass
            db.commit()

    return {"ok": True}


@app.get("/api/billing/plans")
async def billing_plans():
    return {k: {kk: vv for kk, vv in v.items() if kk != "price_id"} for k, v in PLANS.items()}


# ─── Leads ────────────────────────────────────────────────────────────────────
@app.post("/api/leads")
async def capture_lead(body: LeadRequest, db: Session = Depends(get_db)):
    lead = models.Lead(email=body.email.lower().strip(), source=body.source)
    db.add(lead)
    db.commit()
    return {"ok": True}


# ─── Campaigns ────────────────────────────────────────────────────────────────
@app.post("/api/campaigns")
async def create_campaign(
    body: CampaignCreate,
    background_tasks: BackgroundTasks,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    check_campaign_limit(current_user, db)
    max_sites = min(body.max_sites, get_max_sites(current_user))
    campaign = models.Campaign(
        user_id=current_user.id,
        name=body.name,
        keyword=body.keyword,
        country=body.country,
        max_sites=max_sites,
        status="pending",
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    background_tasks.add_task(run_campaign, campaign.id)
    return campaign_to_out(campaign, db)


@app.get("/api/campaigns")
async def list_campaigns(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    campaigns = db.query(models.Campaign).filter(
        models.Campaign.user_id == current_user.id
    ).order_by(models.Campaign.created_at.desc()).all()
    return [campaign_to_out(c, db) for c in campaigns]


@app.get("/api/campaigns/{campaign_id}")
async def get_campaign(
    campaign_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    campaign = db.query(models.Campaign).filter(models.Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    _assert_owns(campaign, current_user)
    return campaign_to_out(campaign, db)


@app.delete("/api/campaigns/{campaign_id}")
async def delete_campaign(
    campaign_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    campaign = db.query(models.Campaign).filter(models.Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    _assert_owns(campaign, current_user)
    db.delete(campaign)
    db.commit()
    return {"ok": True}


@app.get("/api/campaigns/{campaign_id}/progress")
async def campaign_progress(
    campaign_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    campaign = db.query(models.Campaign).filter(models.Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    _assert_owns(campaign, current_user)

    total = db.query(models.Website).filter(models.Website.campaign_id == campaign_id).count()
    pending = db.query(models.Website).filter(models.Website.campaign_id == campaign_id, models.Website.status == "pending").count()
    analyzing = db.query(models.Website).filter(models.Website.campaign_id == campaign_id, models.Website.status == "analyzing").count()
    done = db.query(models.Website).filter(models.Website.campaign_id == campaign_id, models.Website.status == "done").count()
    error = db.query(models.Website).filter(models.Website.campaign_id == campaign_id, models.Website.status == "error").count()
    percent = round((done + error) / total * 100) if total > 0 else 0

    raw_log = getattr(campaign, "discovery_log", None)
    discovery_log = []
    if raw_log:
        try:
            discovery_log = _json.loads(raw_log)
        except Exception:
            pass

    return {
        "campaign_id": campaign_id,
        "status": campaign.status,
        "total": total,
        "pending": pending,
        "analyzing": analyzing,
        "done": done,
        "error": error,
        "percent_complete": percent,
        "is_discovering": campaign.status == "discovering",
        "discovery_log": discovery_log,
    }


# ─── Websites ─────────────────────────────────────────────────────────────────
@app.get("/api/campaigns/{campaign_id}/websites")
async def list_websites(
    campaign_id: int,
    status: Optional[str] = None,
    has_email: Optional[bool] = None,
    min_outdated: Optional[int] = None,
    max_outdated: Optional[int] = None,
    min_security: Optional[int] = None,
    max_security: Optional[int] = None,
    cms: Optional[str] = None,
    search: Optional[str] = None,
    sort: str = "outdated_score",
    order: str = "desc",
    page: int = 1,
    page_size: int = 25,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    campaign = db.query(models.Campaign).filter(models.Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Not found")
    _assert_owns(campaign, current_user)

    query = db.query(models.Website).filter(models.Website.campaign_id == campaign_id)
    if status:
        query = query.filter(models.Website.status == status)
    if min_outdated is not None:
        query = query.filter(models.Website.outdated_score >= min_outdated)
    if max_outdated is not None:
        query = query.filter(models.Website.outdated_score <= max_outdated)
    if min_security is not None:
        query = query.filter(models.Website.security_score >= min_security)
    if max_security is not None:
        query = query.filter(models.Website.security_score <= max_security)
    if cms:
        if cms == "none":
            query = query.join(models.OutdatedResult).filter(models.OutdatedResult.cms_name.is_(None))
        else:
            query = query.join(models.OutdatedResult).filter(models.OutdatedResult.cms_name.ilike(f"%{cms}%"))
    if search:
        s = f"%{search}%"
        from sqlalchemy import or_
        query = query.filter(
            or_(
                models.Website.domain.ilike(s),
                models.Website.url.ilike(s),
                models.Website.id.in_(
                    db.query(models.ContactInfo.website_id).filter(models.ContactInfo.value.ilike(s))
                )
            )
        )

    sort_col = {
        "security_score": models.Website.security_score,
        "domain": models.Website.domain,
        "analyzed_at": models.Website.analyzed_at,
    }.get(sort, models.Website.outdated_score)
    query = query.order_by(sort_col.asc().nulls_last() if order == "asc" else sort_col.desc().nulls_last())

    total_count = query.count()
    websites = query.offset((page - 1) * page_size).limit(page_size).options(
        joinedload(models.Website.contacts),
        joinedload(models.Website.security_result),
        joinedload(models.Website.outdated_result),
        joinedload(models.Website.tech_result),
    ).all()

    if has_email is not None:
        websites = [w for w in websites if (any(c.type == "email" for c in w.contacts)) == has_email]

    return {"total": total_count, "page": page, "page_size": page_size, "websites": [WebsiteOut.model_validate(w) for w in websites]}


@app.get("/api/websites/{website_id}")
async def get_website(
    website_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    site = db.query(models.Website).options(
        joinedload(models.Website.contacts),
        joinedload(models.Website.security_result),
        joinedload(models.Website.outdated_result),
        joinedload(models.Website.tech_result),
    ).filter(models.Website.id == website_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Not found")
    campaign = db.query(models.Campaign).filter(models.Campaign.id == site.campaign_id).first()
    if campaign:
        _assert_owns(campaign, current_user)
    return WebsiteOut.model_validate(site)


# ─── Retry pending sites ──────────────────────────────────────────────────────
@app.post("/api/campaigns/{campaign_id}/retry")
async def retry_campaign(
    campaign_id: int,
    background_tasks: BackgroundTasks,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    campaign = db.query(models.Campaign).filter(models.Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    _assert_owns(campaign, current_user)
    if campaign.status in ("running", "discovering"):
        raise HTTPException(status_code=409, detail="Campaign already running")

    pending_count = db.query(models.Website).filter(
        models.Website.campaign_id == campaign_id,
        models.Website.status == "pending",
    ).count()
    if pending_count == 0:
        raise HTTPException(status_code=400, detail="No pending sites to analyze")

    # Reset stuck analyzing → pending
    db.query(models.Website).filter(
        models.Website.campaign_id == campaign_id,
        models.Website.status == "analyzing",
    ).update({"status": "pending"})
    campaign.status = "running"
    db.commit()

    background_tasks.add_task(_resume_analysis, campaign_id)
    return {"queued": pending_count}


async def _resume_analysis(campaign_id: int):
    from scraper.orchestrator import _analyze_website, CONCURRENT_LIMIT
    db = SessionLocal()
    try:
        pending = db.query(models.Website).filter(
            models.Website.campaign_id == campaign_id,
            models.Website.status == "pending",
        ).all()
        website_ids = [w.id for w in pending]
        db.close()
        db = None

        semaphore = asyncio.Semaphore(CONCURRENT_LIMIT)
        await asyncio.gather(*[_analyze_website(wid, semaphore) for wid in website_ids], return_exceptions=True)

        db = SessionLocal()
        campaign = db.query(models.Campaign).filter(models.Campaign.id == campaign_id).first()
        if campaign:
            done = db.query(models.Website).filter(models.Website.campaign_id == campaign_id, models.Website.status == "done").count()
            campaign.status = "completed" if done > 0 else "failed"
            campaign.completed_at = datetime.utcnow()
            db.commit()
    except Exception as e:
        print(f"[Retry {campaign_id}] Error: {e}")
    finally:
        if db:
            db.close()


# ─── Stats ────────────────────────────────────────────────────────────────────
@app.get("/api/campaigns/{campaign_id}/stats")
async def campaign_stats(
    campaign_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    campaign = db.query(models.Campaign).filter(models.Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Not found")
    _assert_owns(campaign, current_user)

    done_sites = db.query(models.Website).filter(
        models.Website.campaign_id == campaign_id,
        models.Website.status == "done"
    ).all()
    total_done = len(done_sites)
    if total_done == 0:
        return {"avg_outdated_score": 0, "avg_security_score": 0, "total_emails": 0, "total_phones": 0, "cms_breakdown": {}, "high_risk_count": 0, "no_https_count": 0}

    avg_outdated = sum(s.outdated_score or 0 for s in done_sites) / total_done
    avg_security = sum(s.security_score or 0 for s in done_sites) / total_done
    total_emails = db.query(models.ContactInfo).join(models.Website).filter(models.Website.campaign_id == campaign_id, models.ContactInfo.type == "email").count()
    total_phones = db.query(models.ContactInfo).join(models.Website).filter(models.Website.campaign_id == campaign_id, models.ContactInfo.type == "phone").count()
    cms_counts = {}
    for s in done_sites:
        cms = (s.outdated_result.cms_name if s.outdated_result else None) or "Unknown"
        cms_counts[cms] = cms_counts.get(cms, 0) + 1
    high_risk = sum(1 for s in done_sites if (s.security_score or 100) < 40)
    no_https = db.query(models.SecurityResult).join(models.Website).filter(models.Website.campaign_id == campaign_id, models.SecurityResult.is_https == False).count()

    return {"avg_outdated_score": round(avg_outdated, 1), "avg_security_score": round(avg_security, 1), "total_emails": total_emails, "total_phones": total_phones, "cms_breakdown": cms_counts, "high_risk_count": high_risk, "no_https_count": no_https}


# ─── Export ───────────────────────────────────────────────────────────────────
@app.get("/api/campaigns/{campaign_id}/export/csv")
async def export_csv(
    campaign_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    check_feature(current_user, "csv_export")
    campaign = db.query(models.Campaign).filter(models.Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Not found")
    _assert_owns(campaign, current_user)

    websites = db.query(models.Website).options(
        joinedload(models.Website.contacts),
        joinedload(models.Website.security_result),
        joinedload(models.Website.outdated_result),
        joinedload(models.Website.tech_result),
    ).filter(models.Website.campaign_id == campaign_id, models.Website.status == "done").all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["domain", "url", "title", "outdated_score", "security_score", "is_https", "ssl_valid", "ssl_expiry_days", "cms", "cms_version", "jquery_version", "has_flash", "copyright_year", "missing_headers", "emails", "phones", "analyzed_at"])
    for site in websites:
        sr = site.security_result
        or_ = site.outdated_result
        contacts = site.contacts or []
        emails = "; ".join(c.value for c in contacts if c.type == "email")
        phones = "; ".join(c.value for c in contacts if c.type == "phone")
        missing = []
        if sr:
            if not sr.has_hsts: missing.append("HSTS")
            if not sr.has_csp: missing.append("CSP")
            if not sr.has_x_frame_options: missing.append("X-Frame")
        writer.writerow([site.domain, site.url, site.title or "", site.outdated_score or "", site.security_score or "", sr.is_https if sr else "", sr.ssl_valid if sr else "", sr.ssl_expiry_days if sr else "", or_.cms_name if or_ else "", or_.cms_version if or_ else "", or_.jquery_version if or_ else "", or_.has_flash if or_ else "", or_.copyright_year if or_ else "", ", ".join(missing), emails, phones, site.analyzed_at.isoformat() if site.analyzed_at else ""])

    output.seek(0)
    filename = f"campaign_{campaign_id}_{campaign.keyword.replace(' ', '_')[:20]}.csv"
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})


# ─── Global Stats (public) ────────────────────────────────────────────────────
@app.get("/api/stats")
async def global_stats(db: Session = Depends(get_db)):
    total_campaigns = db.query(models.Campaign).count()
    total_websites = db.query(models.Website).filter(models.Website.status == "done").count()
    total_emails = db.query(models.ContactInfo).filter(models.ContactInfo.type == "email").count()
    total_phones = db.query(models.ContactInfo).filter(models.ContactInfo.type == "phone").count()
    total_users = db.query(models.User).count()
    return {"total_campaigns": total_campaigns, "total_websites": total_websites, "total_emails": total_emails, "total_phones": total_phones, "total_users": total_users}


# ─── Email Generation ──────────────────────────────────────────────────────────
@app.post("/api/websites/{website_id}/generate-email")
async def generate_website_email(
    website_id: int,
    body: EmailGenerateRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    check_email_limit(current_user, db)
    site = db.query(models.Website).options(
        joinedload(models.Website.contacts),
        joinedload(models.Website.security_result),
        joinedload(models.Website.outdated_result),
        joinedload(models.Website.tech_result),
    ).filter(models.Website.id == website_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Website not found")
    campaign = db.query(models.Campaign).filter(models.Campaign.id == site.campaign_id).first()
    if campaign:
        _assert_owns(campaign, current_user)

    country = getattr(campaign, "country", "auto") or "auto"
    keyword = campaign.keyword if campaign else ""
    site_data = {
        "domain": site.domain, "title": site.title, "url": site.url,
        "page_description": site.page_description,
        "page_headings": site.page_headings,
        "outdated_score": site.outdated_score, "security_score": site.security_score,
        "contacts": [{"type": c.type, "value": c.value} for c in (site.contacts or [])],
        "outdated_result": {"copyright_year": site.outdated_result.copyright_year if site.outdated_result else None, "cms_name": site.outdated_result.cms_name if site.outdated_result else None, "cms_version": site.outdated_result.cms_version if site.outdated_result else None, "jquery_version": site.outdated_result.jquery_version if site.outdated_result else None, "has_flash": site.outdated_result.has_flash if site.outdated_result else False, "has_viewport_meta": site.outdated_result.has_viewport_meta if site.outdated_result else True, "uses_http_only": site.outdated_result.uses_http_only if site.outdated_result else False, "issues": site.outdated_result.issues if site.outdated_result else []} if site.outdated_result else None,
        "security_result": {"is_https": site.security_result.is_https if site.security_result else False, "ssl_valid": site.security_result.ssl_valid if site.security_result else None, "ssl_expiry_days": site.security_result.ssl_expiry_days if site.security_result else None, "ssl_issuer": site.security_result.ssl_issuer if site.security_result else None, "has_hsts": site.security_result.has_hsts if site.security_result else False, "has_csp": site.security_result.has_csp if site.security_result else False, "has_x_frame_options": site.security_result.has_x_frame_options if site.security_result else False, "has_x_content_type": site.security_result.has_x_content_type if site.security_result else False, "has_referrer_policy": site.security_result.has_referrer_policy if site.security_result else False, "has_mixed_content": site.security_result.has_mixed_content if site.security_result else False, "issues": site.security_result.issues if site.security_result else []} if site.security_result else None,
        "tech_result": {"detected_cms": site.tech_result.detected_cms if site.tech_result else None, "detected_framework": site.tech_result.detected_framework if site.tech_result else None, "detected_cdn": site.tech_result.detected_cdn if site.tech_result else None, "server_header": site.tech_result.server_header if site.tech_result else None} if site.tech_result else None,
    }
    try:
        result = await generate_email(site_data=site_data, keyword=keyword, country=country, sender_name=body.sender_name, sender_offer=body.sender_offer)
    except Exception as e:
        err = str(e)
        if "api_key" in err.lower() or "authentication" in err.lower():
            raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY nie jest ustawiony.")
        raise HTTPException(status_code=500, detail=f"Generowanie emaila nieudane: {err}")

    import json as _json
    follow_ups_json = _json.dumps(result.get("follow_ups", []), ensure_ascii=False)
    email_record = models.GeneratedEmail(website_id=website_id, subject=result.get("subject", ""), body=result.get("body", ""), language=result.get("language", "pl"), recipient_email=result.get("recipient_email"), status="draft", follow_ups=follow_ups_json)
    db.add(email_record)
    db.commit()
    db.refresh(email_record)
    return EmailOut.model_validate(email_record)


@app.get("/api/websites/{website_id}/emails")
async def list_website_emails(
    website_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    emails = db.query(models.GeneratedEmail).filter(models.GeneratedEmail.website_id == website_id).order_by(models.GeneratedEmail.generated_at.desc()).all()
    return [EmailOut.model_validate(e) for e in emails]


@app.patch("/api/emails/{email_id}/status")
async def update_email_status(
    email_id: int,
    status: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    email = db.query(models.GeneratedEmail).filter(models.GeneratedEmail.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Not found")
    if status not in ("draft", "sent"):
        raise HTTPException(status_code=400, detail="Invalid status")
    email.status = status
    db.commit()
    return {"ok": True}


@app.delete("/api/emails/{email_id}")
async def delete_email(
    email_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    email = db.query(models.GeneratedEmail).filter(models.GeneratedEmail.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(email)
    db.commit()
    return {"ok": True}


@app.delete("/api/emails/{email_id}/recipient")
async def remove_email_recipient(
    email_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    email = db.query(models.GeneratedEmail).filter(models.GeneratedEmail.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Not found")
    email.recipient_email = None
    db.commit()
    return {"ok": True}


@app.get("/api/admin/email-logs")
async def get_email_logs(
    page: int = 1,
    page_size: int = 50,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """All sent emails — main + follow-ups."""
    sent_main = db.query(models.GeneratedEmail).join(
        models.Website, models.GeneratedEmail.website_id == models.Website.id
    ).join(
        models.Campaign, models.Website.campaign_id == models.Campaign.id
    ).filter(
        models.Campaign.user_id == current_user.id,
        models.GeneratedEmail.status == "sent",
    ).order_by(models.GeneratedEmail.generated_at.desc()).all()

    sent_followups = db.query(models.ScheduledFollowup).join(
        models.GeneratedEmail, models.ScheduledFollowup.email_id == models.GeneratedEmail.id
    ).join(
        models.Website, models.GeneratedEmail.website_id == models.Website.id
    ).join(
        models.Campaign, models.Website.campaign_id == models.Campaign.id
    ).filter(
        models.Campaign.user_id == current_user.id,
        models.ScheduledFollowup.status.in_(["sent", "failed"]),
    ).order_by(models.ScheduledFollowup.sent_at.desc().nulls_last()).all()

    main_logs = [
        {
            "type": "main",
            "email_id": e.id,
            "recipient": e.recipient_email,
            "subject": e.subject,
            "status": e.status,
            "sent_at": e.generated_at.isoformat() if e.generated_at else None,
            "website_id": e.website_id,
        }
        for e in sent_main
    ]
    followup_logs = [
        {
            "type": f"followup_{f.follow_up_number}",
            "email_id": f.email_id,
            "recipient": f.recipient,
            "subject": f.subject,
            "status": f.status,
            "sent_at": f.sent_at.isoformat() if f.sent_at else None,
            "error": f.error,
            "website_id": None,
        }
        for f in sent_followups
    ]

    all_logs = sorted(main_logs + followup_logs, key=lambda x: x["sent_at"] or "", reverse=True)
    total = len(all_logs)
    start = (page - 1) * page_size
    return {"total": total, "page": page, "page_size": page_size, "logs": all_logs[start:start + page_size]}


@app.get("/api/admin/scheduler")
async def get_scheduler_status(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Scheduler health stats."""
    now = datetime.utcnow()
    pending = db.query(models.ScheduledFollowup).join(
        models.GeneratedEmail
    ).join(models.Website).join(models.Campaign).filter(
        models.Campaign.user_id == current_user.id,
        models.ScheduledFollowup.status == "pending",
    ).count()

    overdue = db.query(models.ScheduledFollowup).join(
        models.GeneratedEmail
    ).join(models.Website).join(models.Campaign).filter(
        models.Campaign.user_id == current_user.id,
        models.ScheduledFollowup.status == "pending",
        models.ScheduledFollowup.send_at <= now,
    ).count()

    sent_total = db.query(models.ScheduledFollowup).join(
        models.GeneratedEmail
    ).join(models.Website).join(models.Campaign).filter(
        models.Campaign.user_id == current_user.id,
        models.ScheduledFollowup.status == "sent",
    ).count()

    failed_total = db.query(models.ScheduledFollowup).join(
        models.GeneratedEmail
    ).join(models.Website).join(models.Campaign).filter(
        models.Campaign.user_id == current_user.id,
        models.ScheduledFollowup.status == "failed",
    ).count()

    last_sent = db.query(models.ScheduledFollowup).join(
        models.GeneratedEmail
    ).join(models.Website).join(models.Campaign).filter(
        models.Campaign.user_id == current_user.id,
        models.ScheduledFollowup.status == "sent",
    ).order_by(models.ScheduledFollowup.sent_at.desc()).first()

    next_due = db.query(models.ScheduledFollowup).join(
        models.GeneratedEmail
    ).join(models.Website).join(models.Campaign).filter(
        models.Campaign.user_id == current_user.id,
        models.ScheduledFollowup.status == "pending",
        models.ScheduledFollowup.send_at > now,
    ).order_by(models.ScheduledFollowup.send_at.asc()).first()

    return {
        "pending_count": pending,
        "overdue_count": overdue,
        "sent_total": sent_total,
        "failed_total": failed_total,
        "last_sent_at": last_sent.sent_at.isoformat() if last_sent and last_sent.sent_at else None,
        "last_sent_to": last_sent.recipient if last_sent else None,
        "next_due_at": next_due.send_at.isoformat() if next_due else None,
        "next_due_to": next_due.recipient if next_due else None,
        "scheduler_interval_minutes": 5,
    }


@app.get("/api/admin/scheduled-emails")
async def get_scheduled_emails(
    page: int = 1,
    page_size: int = 50,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """All pending future follow-ups."""
    query = db.query(models.ScheduledFollowup).join(
        models.GeneratedEmail
    ).join(models.Website).join(models.Campaign).filter(
        models.Campaign.user_id == current_user.id,
        models.ScheduledFollowup.status == "pending",
    ).order_by(models.ScheduledFollowup.send_at.asc())

    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": f.id,
                "follow_up_number": f.follow_up_number,
                "send_at": f.send_at.isoformat(),
                "recipient": f.recipient,
                "subject": f.subject,
                "status": f.status,
            }
            for f in items
        ],
    }


class FollowupOut(BaseModel):
    id: int
    follow_up_number: int
    send_on_day: int
    send_at: datetime
    recipient: str
    subject: str
    status: str
    sent_at: Optional[datetime]
    error: Optional[str]

    class Config:
        from_attributes = True


@app.post("/api/emails/{email_id}/send")
async def send_email_now(
    email_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Send the main email immediately and schedule all follow-ups."""
    email = db.query(models.GeneratedEmail).filter(models.GeneratedEmail.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Not found")
    if not email.recipient_email:
        raise HTTPException(status_code=400, detail="Brak adresu email odbiorcy")
    if email.status == "sent":
        raise HTTPException(status_code=409, detail="Ten email został już wysłany")

    # Send main email
    smtp_cfg = get_user_smtp_config(current_user)
    try:
        await asyncio.get_running_loop().run_in_executor(
            None, send_email, email.recipient_email, email.subject, email.body, smtp_cfg
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Błąd wysyłki: {exc}")

    email.status = "sent"

    # Schedule follow-ups (skip if already scheduled)
    existing = db.query(models.ScheduledFollowup).filter(models.ScheduledFollowup.email_id == email_id).count()
    if existing == 0 and email.follow_ups:
        follow_ups = _json.loads(email.follow_ups) if isinstance(email.follow_ups, str) else (email.follow_ups or [])
        now = datetime.utcnow()
        for fu in follow_ups:
            day = fu.get("send_on_day", 3)
            send_at = now.replace(hour=9, minute=0, second=0, microsecond=0)
            from datetime import timedelta
            send_at = send_at + timedelta(days=day)
            record = models.ScheduledFollowup(
                email_id=email_id,
                follow_up_number=fu.get("follow_up_number", 1),
                send_on_day=day,
                send_at=send_at,
                recipient=email.recipient_email,
                subject=fu.get("subject", ""),
                body=fu.get("body", ""),
                status="pending",
            )
            db.add(record)

    db.commit()
    followups = db.query(models.ScheduledFollowup).filter(models.ScheduledFollowup.email_id == email_id).all()
    return {
        "ok": True,
        "sent_to": email.recipient_email,
        "follow_ups_scheduled": len(followups),
        "follow_ups": [FollowupOut.model_validate(f) for f in followups],
    }


@app.get("/api/emails/{email_id}/followups")
async def get_followups(
    email_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    followups = db.query(models.ScheduledFollowup).filter(
        models.ScheduledFollowup.email_id == email_id
    ).order_by(models.ScheduledFollowup.send_on_day).all()
    return [FollowupOut.model_validate(f) for f in followups]


async def _followup_scheduler():
    """Background loop: every 5 minutes check for due follow-ups and send them."""
    import logging
    logger = logging.getLogger("followup_scheduler")
    while True:
        try:
            await asyncio.sleep(300)  # 5 minutes
            db = SessionLocal()
            try:
                now = datetime.utcnow()
                due = db.query(models.ScheduledFollowup).options(
                    joinedload(models.ScheduledFollowup.email)
                    .joinedload(models.GeneratedEmail.website)
                    .joinedload(models.Website.campaign)
                    .joinedload(models.Campaign.owner)
                ).filter(
                    models.ScheduledFollowup.status == "pending",
                    models.ScheduledFollowup.send_at <= now,
                ).all()
                for fu in due:
                    try:
                        owner = fu.email.website.campaign.owner if fu.email and fu.email.website and fu.email.website.campaign else None
                        smtp_cfg = get_user_smtp_config(owner) if owner else {}
                        await asyncio.get_running_loop().run_in_executor(
                            None, send_email, fu.recipient, fu.subject, fu.body, smtp_cfg
                        )
                        fu.status = "sent"
                        fu.sent_at = datetime.utcnow()
                        logger.info(f"Follow-up #{fu.follow_up_number} sent to {fu.recipient}")
                    except Exception as exc:
                        fu.status = "failed"
                        fu.error = str(exc)
                        logger.error(f"Follow-up #{fu.follow_up_number} failed: {exc}")
                if due:
                    db.commit()
            finally:
                db.close()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            import logging as _log
            _log.getLogger("followup_scheduler").error(f"Scheduler error: {exc}")


@app.post("/api/campaigns/{campaign_id}/generate-emails-bulk")
async def bulk_generate_emails(
    campaign_id: int,
    body: EmailGenerateRequest,
    background_tasks: BackgroundTasks,
    limit: int = Query(default=20, le=100),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    check_feature(current_user, "bulk_email")
    campaign = db.query(models.Campaign).filter(models.Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Not found")
    _assert_owns(campaign, current_user)

    websites_with_emails = db.query(models.Website).join(
        models.ContactInfo,
        (models.ContactInfo.website_id == models.Website.id) & (models.ContactInfo.type == "email")
    ).filter(models.Website.campaign_id == campaign_id, models.Website.status == "done").order_by(models.Website.outdated_score.desc().nulls_last()).distinct().limit(limit).all()

    website_ids = [site.id for site in websites_with_emails]
    background_tasks.add_task(_bulk_generate_task, website_ids, campaign.keyword, getattr(campaign, "country", "auto") or "auto", body.sender_name, body.sender_offer)
    return {"queued": len(website_ids), "message": f"Generuję {len(website_ids)} emaili w tle"}


async def _bulk_generate_task(website_ids, keyword, country, sender_name, sender_offer):
    from database import SessionLocal
    semaphore = asyncio.Semaphore(3)

    async def gen_one(website_id):
        async with semaphore:
            db = SessionLocal()
            try:
                site = db.query(models.Website).options(joinedload(models.Website.contacts), joinedload(models.Website.security_result), joinedload(models.Website.outdated_result), joinedload(models.Website.tech_result)).filter(models.Website.id == website_id).first()
                if not site:
                    return
                existing = db.query(models.GeneratedEmail).filter(models.GeneratedEmail.website_id == website_id).first()
                if existing:
                    return
                site_data = {"domain": site.domain, "title": site.title, "url": site.url, "page_description": site.page_description, "page_headings": site.page_headings, "outdated_score": site.outdated_score, "security_score": site.security_score, "contacts": [{"type": c.type, "value": c.value} for c in (site.contacts or [])], "outdated_result": {"copyright_year": site.outdated_result.copyright_year if site.outdated_result else None, "cms_name": site.outdated_result.cms_name if site.outdated_result else None, "cms_version": site.outdated_result.cms_version if site.outdated_result else None, "jquery_version": site.outdated_result.jquery_version if site.outdated_result else None, "has_flash": site.outdated_result.has_flash if site.outdated_result else False, "has_viewport_meta": site.outdated_result.has_viewport_meta if site.outdated_result else True, "uses_http_only": site.outdated_result.uses_http_only if site.outdated_result else False, "issues": []} if site.outdated_result else None, "security_result": {"is_https": site.security_result.is_https if site.security_result else False, "ssl_valid": site.security_result.ssl_valid if site.security_result else None, "ssl_expiry_days": site.security_result.ssl_expiry_days if site.security_result else None, "has_hsts": site.security_result.has_hsts if site.security_result else False, "has_csp": site.security_result.has_csp if site.security_result else False, "has_x_frame_options": site.security_result.has_x_frame_options if site.security_result else False, "has_x_content_type": site.security_result.has_x_content_type if site.security_result else False, "has_referrer_policy": site.security_result.has_referrer_policy if site.security_result else False, "has_mixed_content": site.security_result.has_mixed_content if site.security_result else False} if site.security_result else None, "tech_result": {"detected_cms": site.tech_result.detected_cms if site.tech_result else None, "detected_framework": site.tech_result.detected_framework if site.tech_result else None, "detected_cdn": site.tech_result.detected_cdn if site.tech_result else None} if site.tech_result else None}
                result = await generate_email(site_data=site_data, keyword=keyword, country=country, sender_name=sender_name, sender_offer=sender_offer)
                email_record = models.GeneratedEmail(website_id=website_id, subject=result.get("subject", ""), body=result.get("body", ""), language=result.get("language", "pl"), recipient_email=result.get("recipient_email"), status="draft")
                db.add(email_record)
                db.commit()
            except Exception as e:
                print(f"[BulkEmail] Failed {website_id}: {e}")
            finally:
                db.close()
            await asyncio.sleep(0.5)

    await asyncio.gather(*[gen_one(wid) for wid in website_ids], return_exceptions=True)
