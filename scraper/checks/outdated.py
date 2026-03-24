"""
Checks for outdated website indicators.
"""
import re
from datetime import datetime
from typing import Dict, Any, Tuple, List
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup

CURRENT_YEAR = datetime.now().year

# CMS version thresholds (versions below these are considered outdated)
CMS_THRESHOLDS = {
    "wordpress": (6, 4),   # 6.4.x is current-ish
    "joomla": (5, 0),
    "drupal": (10, 0),
}


def _extract_copyright_year(soup: BeautifulSoup) -> Tuple[int | None, List[str]]:
    issues = []
    text = soup.get_text(" ", strip=True)

    patterns = [
        r'(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?(\d{4})',
        r'(\d{4})\s*(?:©|&copy;)',
    ]

    years = []
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        years.extend(int(y) for y in matches if 1990 <= int(y) <= CURRENT_YEAR + 1)

    # Also check meta
    meta_copy = soup.find("meta", {"name": re.compile(r"copyright", re.I)})
    if meta_copy:
        yr = re.search(r"\d{4}", meta_copy.get("content", ""))
        if yr:
            years.append(int(yr.group()))

    if not years:
        return None, []

    max_year = max(years)
    years_old = CURRENT_YEAR - max_year

    if years_old >= 3:
        issues.append(f"Copyright year {max_year} is {years_old} years old")
    elif years_old >= 2:
        issues.append(f"Copyright year {max_year} (slightly outdated)")

    return max_year, issues


def _check_last_modified(headers: Dict[str, str]) -> Tuple[str | None, List[str]]:
    issues = []
    lm = headers.get("last-modified") or headers.get("Last-Modified")
    if not lm:
        return None, []

    try:
        lm_dt = parsedate_to_datetime(lm)
        age_days = (datetime.now(lm_dt.tzinfo) - lm_dt).days
        if age_days > 730:
            issues.append(f"Last-Modified header is {age_days // 365} year(s) ago")
        return lm, issues
    except Exception:
        return lm, []


def _detect_cms(soup: BeautifulSoup, html: str) -> Tuple[str | None, str | None, List[str]]:
    """Returns (cms_name, cms_version, issues)"""
    issues = []
    cms_name = None
    cms_version = None

    # WordPress
    wp_patterns = [
        r'<meta[^>]+generator[^>]+WordPress\s*([\d.]*)',
        r'/wp-content/',
        r'/wp-includes/',
    ]
    generator = soup.find("meta", {"name": re.compile(r"generator", re.I)})
    if generator:
        content = generator.get("content", "")
        wp_match = re.search(r'WordPress\s*([\d.]+)?', content, re.I)
        if wp_match:
            cms_name = "WordPress"
            cms_version = wp_match.group(1) if wp_match.group(1) else None
        joomla_match = re.search(r'Joomla!?\s*([\d.]+)?', content, re.I)
        if joomla_match:
            cms_name = "Joomla"
            cms_version = joomla_match.group(1) if joomla_match.group(1) else None
        drupal_match = re.search(r'Drupal\s*([\d.]+)?', content, re.I)
        if drupal_match:
            cms_name = "Drupal"
            cms_version = drupal_match.group(1) if drupal_match.group(1) else None

    if not cms_name:
        if "/wp-content/" in html or "/wp-includes/" in html:
            cms_name = "WordPress"
        elif "/components/com_" in html or "/administrator/index.php" in html:
            cms_name = "Joomla"
        elif "/sites/default/files/" in html or "Drupal.settings" in html:
            cms_name = "Drupal"
        elif "squarespace" in html.lower():
            cms_name = "Squarespace"
        elif "data-wix-" in html or "wix.com" in html:
            cms_name = "Wix"
        elif "shopify" in html.lower() and "Shopify.theme" in html:
            cms_name = "Shopify"

    # Version outdatedness check
    if cms_name and cms_version:
        key = cms_name.lower()
        if key in CMS_THRESHOLDS:
            try:
                parts = cms_version.split(".")
                major = int(parts[0])
                minor = int(parts[1]) if len(parts) > 1 else 0
                threshold_major, threshold_minor = CMS_THRESHOLDS[key]

                if major < threshold_major or (major == threshold_major and minor < threshold_minor):
                    issues.append(f"{cms_name} {cms_version} is outdated (current: {threshold_major}.x)")
            except (ValueError, IndexError):
                pass

    return cms_name, cms_version, issues


def _detect_jquery(soup: BeautifulSoup) -> Tuple[str | None, List[str]]:
    issues = []
    jquery_version = None

    for script in soup.find_all("script", src=True):
        src = script.get("src", "")
        match = re.search(r'jquery[.-]([\d.]+)(?:\.min)?\.js', src, re.I)
        if match:
            jquery_version = match.group(1)
            break

    if not jquery_version:
        # Check inline scripts for jQuery version declaration
        for script in soup.find_all("script", src=False):
            text = script.get_text()
            match = re.search(r'jQuery\s+v?([\d.]+)', text) or \
                    re.search(r'"jquery"\s*:\s*"([\d.]+)"', text)
            if match:
                jquery_version = match.group(1)
                break

    if jquery_version:
        try:
            parts = jquery_version.split(".")
            major = int(parts[0])
            minor = int(parts[1]) if len(parts) > 1 else 0
            if major == 1:
                issues.append(f"jQuery {jquery_version} is very outdated (EOL, current: 3.x)")
            elif major == 2:
                issues.append(f"jQuery {jquery_version} is outdated (EOL, current: 3.x)")
        except (ValueError, IndexError):
            pass

    return jquery_version, issues


def _check_flash(soup: BeautifulSoup, html: str) -> Tuple[bool, List[str]]:
    issues = []
    has_flash = False

    if (soup.find("object", {"type": "application/x-shockwave-flash"}) or
            soup.find("embed", {"type": "application/x-shockwave-flash"}) or
            ".swf" in html.lower()):
        has_flash = True
        issues.append("Uses Adobe Flash (discontinued in 2020, unsupported by all browsers)")

    return has_flash, issues


def _check_viewport(soup: BeautifulSoup) -> Tuple[bool, List[str]]:
    issues = []
    viewport = soup.find("meta", {"name": re.compile(r"viewport", re.I)})
    has_viewport = bool(viewport and "width" in viewport.get("content", "").lower())

    if not has_viewport:
        issues.append("No mobile viewport meta tag (site is not mobile-friendly)")

    return has_viewport, issues


async def check_outdated(soup: BeautifulSoup, headers: Dict[str, str], html: str) -> Dict[str, Any]:
    """Run all outdated checks. Returns a dict matching OutdatedResult fields + issues list."""
    all_issues = []

    copyright_year, c_issues = _extract_copyright_year(soup)
    all_issues.extend(c_issues)

    last_modified, lm_issues = _check_last_modified(headers)
    all_issues.extend(lm_issues)

    cms_name, cms_version, cms_issues = _detect_cms(soup, html)
    all_issues.extend(cms_issues)

    jquery_version, jq_issues = _detect_jquery(soup)
    all_issues.extend(jq_issues)

    has_flash, flash_issues = _check_flash(soup, html)
    all_issues.extend(flash_issues)

    has_viewport, vp_issues = _check_viewport(soup)
    all_issues.extend(vp_issues)

    return {
        "copyright_year": copyright_year,
        "last_modified_header": last_modified,
        "cms_name": cms_name,
        "cms_version": cms_version,
        "jquery_version": jquery_version,
        "has_flash": has_flash,
        "has_viewport_meta": has_viewport,
        "uses_http_only": not headers.get("strict-transport-security") and not headers.get("Strict-Transport-Security"),
        "issues": all_issues,
    }
