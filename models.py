from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Text,
    ForeignKey, JSON
)
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    name = Column(String, nullable=True)
    plan = Column(String, default="pro")  # free|starter|pro
    stripe_customer_id = Column(String, nullable=True)
    stripe_subscription_id = Column(String, nullable=True)
    subscription_period_end = Column(DateTime, nullable=True)
    cancel_at_period_end = Column(Boolean, default=False)
    onboarding_done = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    campaigns = relationship("Campaign", back_populates="owner", cascade="all, delete-orphan")


class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, nullable=False)
    source = Column(String, default="landing")  # landing|exit_popup
    created_at = Column(DateTime, default=datetime.utcnow)


class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # nullable for migration
    name = Column(String, nullable=False)
    keyword = Column(String, nullable=False)
    country = Column(String, default="auto")  # auto|us|pl|eu
    status = Column(String, default="pending")  # pending|discovering|running|completed|failed
    max_sites = Column(Integer, default=200)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    discovery_log = Column(Text, nullable=True)  # JSON list of progress messages

    owner = relationship("User", back_populates="campaigns")
    websites = relationship("Website", back_populates="campaign", cascade="all, delete-orphan")


class Website(Base):
    __tablename__ = "websites"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False)
    url = Column(String, nullable=False)
    domain = Column(String, nullable=False)
    title = Column(String, nullable=True)
    page_description = Column(Text, nullable=True)
    page_headings = Column(Text, nullable=True)  # JSON array of h1/h2 texts
    status = Column(String, default="pending")  # pending|analyzing|done|error
    error_message = Column(Text, nullable=True)

    outdated_score = Column(Integer, nullable=True)   # 0-100, higher = more outdated
    security_score = Column(Integer, nullable=True)   # 0-100, higher = more secure

    discovered_at = Column(DateTime, default=datetime.utcnow)
    analyzed_at = Column(DateTime, nullable=True)

    campaign = relationship("Campaign", back_populates="websites")
    security_result = relationship("SecurityResult", back_populates="website", uselist=False, cascade="all, delete-orphan")
    outdated_result = relationship("OutdatedResult", back_populates="website", uselist=False, cascade="all, delete-orphan")
    tech_result = relationship("TechResult", back_populates="website", uselist=False, cascade="all, delete-orphan")
    contacts = relationship("ContactInfo", back_populates="website", cascade="all, delete-orphan")


class SecurityResult(Base):
    __tablename__ = "security_results"

    id = Column(Integer, primary_key=True, index=True)
    website_id = Column(Integer, ForeignKey("websites.id"), unique=True, nullable=False)

    is_https = Column(Boolean, default=False)
    ssl_valid = Column(Boolean, nullable=True)
    ssl_expiry_days = Column(Integer, nullable=True)
    ssl_issuer = Column(String, nullable=True)

    has_hsts = Column(Boolean, default=False)
    has_x_frame_options = Column(Boolean, default=False)
    has_csp = Column(Boolean, default=False)
    has_x_content_type = Column(Boolean, default=False)
    has_referrer_policy = Column(Boolean, default=False)
    has_mixed_content = Column(Boolean, default=False)

    issues = Column(JSON, default=list)  # list of issue strings

    website = relationship("Website", back_populates="security_result")


class OutdatedResult(Base):
    __tablename__ = "outdated_results"

    id = Column(Integer, primary_key=True, index=True)
    website_id = Column(Integer, ForeignKey("websites.id"), unique=True, nullable=False)

    copyright_year = Column(Integer, nullable=True)
    last_modified_header = Column(String, nullable=True)
    cms_name = Column(String, nullable=True)
    cms_version = Column(String, nullable=True)
    jquery_version = Column(String, nullable=True)
    has_flash = Column(Boolean, default=False)
    has_viewport_meta = Column(Boolean, default=True)
    uses_http_only = Column(Boolean, default=False)

    issues = Column(JSON, default=list)  # list of issue strings

    website = relationship("Website", back_populates="outdated_result")


class TechResult(Base):
    __tablename__ = "tech_results"

    id = Column(Integer, primary_key=True, index=True)
    website_id = Column(Integer, ForeignKey("websites.id"), unique=True, nullable=False)

    server_header = Column(String, nullable=True)
    x_powered_by = Column(String, nullable=True)
    detected_cms = Column(String, nullable=True)
    detected_framework = Column(String, nullable=True)
    detected_cdn = Column(String, nullable=True)

    website = relationship("Website", back_populates="tech_result")


class ContactInfo(Base):
    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True, index=True)
    website_id = Column(Integer, ForeignKey("websites.id"), nullable=False)
    type = Column(String, nullable=False)   # email|phone|social_*|address
    value = Column(String, nullable=False)
    source_url = Column(String, nullable=True)

    website = relationship("Website", back_populates="contacts")


class GeneratedEmail(Base):
    __tablename__ = "generated_emails"

    id = Column(Integer, primary_key=True, index=True)
    website_id = Column(Integer, ForeignKey("websites.id"), nullable=False)
    subject = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    language = Column(String, default="pl")  # pl|en
    recipient_email = Column(String, nullable=True)
    status = Column(String, default="draft")  # draft|sent
    follow_ups = Column(Text, nullable=True)  # JSON array of follow-up emails
    generated_at = Column(DateTime, default=datetime.utcnow)

    website = relationship("Website", backref="generated_emails")
    scheduled_followups = relationship("ScheduledFollowup", back_populates="email", cascade="all, delete-orphan")


class ScheduledFollowup(Base):
    __tablename__ = "scheduled_followups"

    id = Column(Integer, primary_key=True, index=True)
    email_id = Column(Integer, ForeignKey("generated_emails.id"), nullable=False)
    follow_up_number = Column(Integer, nullable=False)   # 1-4
    send_on_day = Column(Integer, nullable=False)         # 3, 7, 14, 21
    send_at = Column(DateTime, nullable=False)            # absolute datetime
    recipient = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    status = Column(String, default="pending")            # pending|sent|failed
    sent_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)

    email = relationship("GeneratedEmail", back_populates="scheduled_followups")
