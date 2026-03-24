"""
Campaign orchestrator - manages the full scrape lifecycle.
"""
import asyncio
import json
import random
from datetime import datetime
from typing import List
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from scraper.discovery import discover_websites, get_domain
from scraper.checks.outdated import check_outdated
from scraper.checks.security import check_security
from scraper.checks.technology import check_technology
from scraper.checks.contact import extract_contacts
from scraper.scoring import calculate_outdated_score, calculate_security_score
from database import SessionLocal
import models

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]

CONCURRENT_LIMIT = 5  # 5 concurrent site analyses (thread pool safe)


async def _analyze_website(website_id: int, semaphore: asyncio.Semaphore):
    """Analyze a single website. All DB interactions use a fresh session."""
    async with semaphore:
        db = SessionLocal()
        try:
            site = db.query(models.Website).filter(models.Website.id == website_id).first()
            if not site:
                return

            site.status = "analyzing"
            db.commit()

            url = site.url
            domain = site.domain

            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(20.0, connect=8.0),
                headers={
                    "User-Agent": random.choice(USER_AGENTS),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                verify=False,   # We check SSL validity separately via stdlib
            ) as client:
                try:
                    resp = await client.get(url)
                    html = resp.text
                    final_url = str(resp.url)
                    headers = dict(resp.headers)

                    # Update URL if redirected
                    if final_url != url:
                        site.url = final_url
                        site.domain = get_domain(final_url)

                    is_https = final_url.startswith("https://")

                    soup = BeautifulSoup(html, "lxml")

                    # Extract title
                    title_tag = soup.find("title")
                    if title_tag:
                        site.title = title_tag.get_text(strip=True)[:500]

                    # Run all checks concurrently
                    outdated_data, security_data, tech_data, contacts = await asyncio.gather(
                        check_outdated(soup, headers, html),
                        check_security(final_url, headers, soup, is_https),
                        check_technology(soup, headers, html),
                        extract_contacts(soup, final_url, client),
                        return_exceptions=True
                    )

                    # Handle exceptions from individual checks
                    if isinstance(outdated_data, Exception):
                        outdated_data = {"issues": [str(outdated_data)]}
                    if isinstance(security_data, Exception):
                        security_data = {"is_https": is_https, "issues": [str(security_data)]}
                    if isinstance(tech_data, Exception):
                        tech_data = {}
                    if isinstance(contacts, Exception):
                        contacts = []

                    # Calculate scores
                    site.outdated_score = calculate_outdated_score(outdated_data)
                    site.security_score = calculate_security_score(security_data)

                    # Persist results
                    outdated_result = models.OutdatedResult(
                        website_id=site.id,
                        copyright_year=outdated_data.get("copyright_year"),
                        last_modified_header=outdated_data.get("last_modified_header"),
                        cms_name=outdated_data.get("cms_name"),
                        cms_version=outdated_data.get("cms_version"),
                        jquery_version=outdated_data.get("jquery_version"),
                        has_flash=outdated_data.get("has_flash", False),
                        has_viewport_meta=outdated_data.get("has_viewport_meta", True),
                        uses_http_only=not is_https,
                        issues=outdated_data.get("issues", []),
                    )
                    db.add(outdated_result)

                    security_result = models.SecurityResult(
                        website_id=site.id,
                        is_https=security_data.get("is_https", is_https),
                        ssl_valid=security_data.get("ssl_valid"),
                        ssl_expiry_days=security_data.get("ssl_expiry_days"),
                        ssl_issuer=security_data.get("ssl_issuer"),
                        has_hsts=security_data.get("has_hsts", False),
                        has_x_frame_options=security_data.get("has_x_frame_options", False),
                        has_csp=security_data.get("has_csp", False),
                        has_x_content_type=security_data.get("has_x_content_type", False),
                        has_referrer_policy=security_data.get("has_referrer_policy", False),
                        has_mixed_content=security_data.get("has_mixed_content", False),
                        issues=security_data.get("issues", []),
                    )
                    db.add(security_result)

                    tech_result = models.TechResult(
                        website_id=site.id,
                        server_header=tech_data.get("server_header"),
                        x_powered_by=tech_data.get("x_powered_by"),
                        detected_cms=tech_data.get("detected_cms"),
                        detected_framework=tech_data.get("detected_framework"),
                        detected_cdn=tech_data.get("detected_cdn"),
                    )
                    db.add(tech_result)

                    for contact in contacts:
                        db.add(models.ContactInfo(
                            website_id=site.id,
                            type=contact.get("type", "unknown"),
                            value=contact.get("value", ""),
                            source_url=contact.get("source_url"),
                        ))

                    site.status = "done"

                except httpx.TimeoutException:
                    site.status = "error"
                    site.error_message = "Connection timed out"
                except httpx.TooManyRedirects:
                    site.status = "error"
                    site.error_message = "Too many redirects"
                except Exception as e:
                    site.status = "error"
                    site.error_message = str(e)[:500]

        except Exception as outer_e:
            # Fallback error handling
            try:
                site = db.query(models.Website).filter(models.Website.id == website_id).first()
                if site:
                    site.status = "error"
                    site.error_message = f"Unexpected error: {str(outer_e)[:400]}"
            except Exception:
                pass

        finally:
            site_ref = db.query(models.Website).filter(models.Website.id == website_id).first()
            if site_ref:
                site_ref.analyzed_at = datetime.utcnow()
            try:
                db.commit()
            except Exception:
                db.rollback()
            db.close()

        # Polite delay between requests
        await asyncio.sleep(random.uniform(0.5, 1.5))


async def run_campaign(campaign_id: int):
    """
    Main campaign runner. Called as a FastAPI background task.
    Phase 1: Discover websites.
    Phase 2: Analyze each website concurrently (max 5 at a time).
    """
    db = SessionLocal()
    try:
        campaign = db.query(models.Campaign).filter(models.Campaign.id == campaign_id).first()
        if not campaign:
            return

        # Phase 0: Mark as discovering (before any websites are in DB)
        campaign.status = "discovering"
        db.commit()

        keyword = campaign.keyword
        max_sites = campaign.max_sites
        country = getattr(campaign, "country", "auto") or "auto"

        print(f"[Campaign {campaign_id}] Starting discovery for: {keyword} (country={country})")

        # Live log callback — writes messages to campaign.discovery_log in DB
        _log_messages = []

        async def discovery_log(msg: str):
            _log_messages.append(msg)
            log_db = SessionLocal()
            try:
                c = log_db.query(models.Campaign).filter(models.Campaign.id == campaign_id).first()
                if c:
                    c.discovery_log = json.dumps(_log_messages[-30:], ensure_ascii=False)
                    log_db.commit()
            except Exception:
                pass
            finally:
                log_db.close()

        # Phase 1: Discovery
        try:
            urls = await discover_websites(keyword, max_results=max_sites, country=country, log_fn=discovery_log)
        except Exception as e:
            print(f"[Campaign {campaign_id}] Discovery failed: {e}")
            await discovery_log(f"BŁĄD discovery: {e}")
            urls = []

        print(f"[Campaign {campaign_id}] Discovered {len(urls)} websites")

        if not urls:
            campaign.status = "completed"
            campaign.completed_at = datetime.utcnow()
            db.commit()
            return

        # Switch to "running" once websites are queued
        campaign.status = "running"

        # Create Website records in bulk
        website_ids = []
        for url in urls:
            site = models.Website(
                campaign_id=campaign_id,
                url=url,
                domain=get_domain(url),
                status="pending",
            )
            db.add(site)
            db.flush()
            website_ids.append(site.id)
        db.commit()

        db.close()
        db = None  # Let individual tasks use their own sessions

        # Phase 2: Analysis with bounded concurrency
        semaphore = asyncio.Semaphore(CONCURRENT_LIMIT)

        tasks = [_analyze_website(wid, semaphore) for wid in website_ids]
        await asyncio.gather(*tasks, return_exceptions=True)

        print(f"[Campaign {campaign_id}] Analysis complete")

    except Exception as e:
        print(f"[Campaign {campaign_id}] Fatal error: {e}")

    finally:
        if db is None:
            db = SessionLocal()

        try:
            campaign = db.query(models.Campaign).filter(models.Campaign.id == campaign_id).first()
            if campaign:
                # Check if any sites failed
                done_count = db.query(models.Website).filter(
                    models.Website.campaign_id == campaign_id,
                    models.Website.status == "done"
                ).count()
                campaign.status = "completed" if done_count > 0 else "failed"
                campaign.completed_at = datetime.utcnow()
                db.commit()
        except Exception:
            pass
        finally:
            db.close()
