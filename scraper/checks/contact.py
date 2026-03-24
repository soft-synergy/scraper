"""
Contact information extraction: emails, phones, social links, addresses.
"""
import re
import asyncio
import json
import html as html_module
from typing import List, Dict, Any, Set
from urllib.parse import urlparse, urljoin

import httpx
from bs4 import BeautifulSoup

# Social media platforms to detect
SOCIAL_PLATFORMS = {
    "facebook.com": "social_facebook",
    "fb.com": "social_facebook",
    "twitter.com": "social_twitter",
    "x.com": "social_twitter",
    "instagram.com": "social_instagram",
    "linkedin.com": "social_linkedin",
    "youtube.com": "social_youtube",
    "tiktok.com": "social_tiktok",
    "pinterest.com": "social_pinterest",
    "snapchat.com": "social_snapchat",
}

# Email patterns to exclude
EMAIL_BLACKLIST = {
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "privacy", "support@example", "info@example", "user@example",
    "test@", "sample@", "email@email", "name@domain", "you@",
    "your@", "someone@",
}

EMAIL_REGEX = re.compile(
    r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}',
    re.IGNORECASE
)

PHONE_PATTERNS = [
    # US/Canada format
    re.compile(r'(?:\+?1[\s.\-]?)?(?:\(?\d{3}\)?[\s.\-])\d{3}[\s.\-]\d{4}'),
    # International format
    re.compile(r'\+\d{1,3}[\s\-\.]\d[\d\s\-\.]{6,14}\d'),
    # Simple 10-digit
    re.compile(r'\b\d{3}[\-\.]\d{3}[\-\.]\d{4}\b'),
]

OBFUSCATED_EMAIL_REGEX = re.compile(
    r'([a-zA-Z0-9._%+\-]+)\s*\[?(?:AT|at)\]?\s*([a-zA-Z0-9.\-]+)\s*\[?(?:DOT|dot)\]?\s*([a-zA-Z]{2,})',
    re.IGNORECASE
)


def _normalize_email(email: str) -> str:
    return email.lower().strip().rstrip(".")


def _is_valid_email(email: str) -> bool:
    email_lower = email.lower()
    for blacklisted in EMAIL_BLACKLIST:
        if email_lower.startswith(blacklisted):
            return False
    # Must have valid TLD
    parts = email.split("@")
    if len(parts) != 2:
        return False
    domain = parts[1]
    if "." not in domain:
        return False
    # Skip image/file extensions accidentally caught
    for ext in [".png", ".jpg", ".gif", ".svg", ".css", ".js"]:
        if email_lower.endswith(ext):
            return False
    return True


def _extract_emails_from_text(text: str) -> Set[str]:
    emails = set()

    # Decode HTML entities first
    text_decoded = html_module.unescape(text)

    # Standard email regex
    for match in EMAIL_REGEX.finditer(text_decoded):
        email = _normalize_email(match.group())
        if _is_valid_email(email):
            emails.add(email)

    # Obfuscated "user [at] domain [dot] com"
    for match in OBFUSCATED_EMAIL_REGEX.finditer(text_decoded):
        email = f"{match.group(1)}@{match.group(2)}.{match.group(3)}"
        email = _normalize_email(email)
        if _is_valid_email(email):
            emails.add(email)

    return emails


def _extract_emails_from_soup(soup: BeautifulSoup) -> Set[str]:
    emails = set()

    # mailto: links
    for a in soup.find_all("a", href=re.compile(r'^mailto:', re.I)):
        href = a.get("href", "")
        email_part = href[7:].split("?")[0]  # Remove mailto: and query params
        email_part = _normalize_email(email_part)
        if _is_valid_email(email_part):
            emails.add(email_part)

    # Text content
    text = soup.get_text(" ", strip=True)
    emails.update(_extract_emails_from_text(text))

    # JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.get_text())
            # Flatten JSON and search
            flat_str = json.dumps(data)
            emails.update(_extract_emails_from_text(flat_str))
        except Exception:
            pass

    return emails


def _extract_phones_from_text(text: str) -> Set[str]:
    phones = set()
    # Also try tel: links in raw HTML
    tel_pattern = re.compile(r'tel:[+\d\s\-.()\[\]]+', re.I)
    for match in tel_pattern.finditer(text):
        phone = match.group().replace("tel:", "").strip()
        if len(re.sub(r'\D', '', phone)) >= 7:
            phones.add(phone)

    for pattern in PHONE_PATTERNS:
        for match in pattern.finditer(text):
            phone = match.group().strip()
            digits_only = re.sub(r'\D', '', phone)
            if 7 <= len(digits_only) <= 15:
                phones.add(phone)

    return phones


def _extract_phones_from_soup(soup: BeautifulSoup) -> Set[str]:
    phones = set()

    # tel: links
    for a in soup.find_all("a", href=re.compile(r'^tel:', re.I)):
        href = a.get("href", "")
        phone = href[4:].strip()
        digits_only = re.sub(r'\D', '', phone)
        if 7 <= len(digits_only) <= 15:
            phones.add(phone)

    # Text content
    text = soup.get_text(" ", strip=True)
    phones.update(_extract_phones_from_text(text))

    return phones


def _extract_social_links(soup: BeautifulSoup) -> List[Dict[str, str]]:
    socials = {}

    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        for domain, contact_type in SOCIAL_PLATFORMS.items():
            if domain in href:
                parsed = urlparse(href)
                # Make sure it's a full URL
                if parsed.scheme in ("http", "https"):
                    # Normalize: remove trailing slashes
                    clean_url = href.rstrip("/")
                    if contact_type not in socials:
                        socials[contact_type] = clean_url
                break

    return [{"type": k, "value": v} for k, v in socials.items()]


def _extract_address(soup: BeautifulSoup) -> List[str]:
    addresses = []

    # Schema.org microdata
    street = soup.find(attrs={"itemprop": "streetAddress"})
    locality = soup.find(attrs={"itemprop": "addressLocality"})
    region = soup.find(attrs={"itemprop": "addressRegion"})
    postal = soup.find(attrs={"itemprop": "postalCode"})

    if street:
        parts = [street.get_text(strip=True)]
        if locality:
            parts.append(locality.get_text(strip=True))
        if region:
            parts.append(region.get_text(strip=True))
        if postal:
            parts.append(postal.get_text(strip=True))
        addresses.append(", ".join(parts))

    # JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.get_text())
            if isinstance(data, list):
                data = data[0]
            if isinstance(data, dict):
                # Recurse into nested objects
                def find_postal_address(obj):
                    if isinstance(obj, dict):
                        if obj.get("@type") == "PostalAddress":
                            parts = []
                            for field in ["streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry"]:
                                val = obj.get(field)
                                if val:
                                    parts.append(str(val))
                            if parts:
                                addresses.append(", ".join(parts))
                        for v in obj.values():
                            find_postal_address(v)
                    elif isinstance(obj, list):
                        for item in obj:
                            find_postal_address(item)

                find_postal_address(data)
        except Exception:
            pass

    # Google Maps embed
    for iframe in soup.find_all("iframe", src=re.compile(r'google.com/maps', re.I)):
        src = iframe.get("src", "")
        q_match = re.search(r'[?&]q=([^&]+)', src)
        if q_match:
            from urllib.parse import unquote
            address = unquote(q_match.group(1).replace("+", " "))
            if address and len(address) > 5:
                addresses.append(address)

    return list(dict.fromkeys(addresses))  # Deduplicate while preserving order


def _get_contact_page_urls(soup: BeautifulSoup, base_url: str) -> List[str]:
    """Find likely contact/about page URLs."""
    keywords = ["contact", "about", "team", "staff", "reach", "get-in-touch", "locations", "offices"]
    found = []

    base_domain = urlparse(base_url).netloc

    for a in soup.find_all("a", href=True):
        href = a.get("href", "").lower()
        text = a.get_text(strip=True).lower()

        if any(kw in href or kw in text for kw in keywords):
            full_url = urljoin(base_url, a.get("href", ""))
            parsed = urlparse(full_url)
            # Same domain only
            if parsed.netloc == base_domain and full_url not in found:
                found.append(full_url)
                if len(found) >= 3:
                    break

    return found


async def extract_contacts(
    soup: BeautifulSoup,
    base_url: str,
    client: httpx.AsyncClient
) -> List[Dict[str, Any]]:
    """Extract all contact information from a website (homepage + contact pages)."""
    contacts = []

    # Get emails, phones, socials from homepage
    emails = _extract_emails_from_soup(soup)
    phones = _extract_phones_from_soup(soup)
    socials = _extract_social_links(soup)
    addresses = _extract_address(soup)

    # Find and scrape contact pages
    contact_page_urls = _get_contact_page_urls(soup, base_url)

    for url in contact_page_urls:
        try:
            resp = await client.get(url, timeout=10.0)
            if resp.status_code == 200:
                page_soup = BeautifulSoup(resp.text, "lxml")
                emails.update(_extract_emails_from_soup(page_soup))
                phones.update(_extract_phones_from_soup(page_soup))
                # Merge socials
                for s in _extract_social_links(page_soup):
                    if not any(existing["type"] == s["type"] for existing in socials):
                        socials.append(s)
                # Merge addresses
                for addr in _extract_address(page_soup):
                    if addr not in addresses:
                        addresses.append(addr)
        except Exception:
            pass

    # Build contact records
    for email in sorted(emails):
        contacts.append({
            "type": "email",
            "value": email,
            "source_url": base_url,
        })

    for phone in sorted(phones):
        contacts.append({
            "type": "phone",
            "value": phone.strip(),
            "source_url": base_url,
        })

    for social in socials:
        contacts.append({
            "type": social["type"],
            "value": social["value"],
            "source_url": base_url,
        })

    for address in addresses:
        contacts.append({
            "type": "address",
            "value": address,
            "source_url": base_url,
        })

    return contacts
