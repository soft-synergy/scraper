"""
Technology stack detection: server, CMS, framework, CDN.
"""
import re
from typing import Dict, Any, Optional

from bs4 import BeautifulSoup


def _detect_server(headers: Dict[str, str]) -> Optional[str]:
    server = headers.get("server") or headers.get("Server")
    if server:
        return server[:200]
    return None


def _detect_powered_by(headers: Dict[str, str]) -> Optional[str]:
    powered = headers.get("x-powered-by") or headers.get("X-Powered-By")
    if powered:
        return powered[:200]
    return None


def _detect_framework(soup: BeautifulSoup, html: str) -> Optional[str]:
    # Next.js
    if soup.find(id="__NEXT_DATA__") or "__NEXT_DATA__" in html:
        return "Next.js"
    # Gatsby
    if soup.find(id="gatsby-focus-wrapper") or "gatsby" in html.lower()[:5000]:
        return "Gatsby"
    # Nuxt.js
    if "__NUXT__" in html or "nuxt" in html.lower()[:2000]:
        return "Nuxt.js"
    # React (generic)
    if soup.find(id="root") and ("react" in html.lower()[:10000] or "React" in html[:10000]):
        return "React"
    # Vue.js
    if 'id="app"' in html and ("vue" in html.lower()[:10000] or "Vue" in html[:10000]):
        return "Vue.js"
    # Angular
    if "ng-version" in html or "angular" in html.lower()[:5000]:
        return "Angular"
    # Laravel
    if soup.find("meta", {"name": "csrf-token"}) and "laravel" in html.lower()[:5000]:
        return "Laravel"
    # Django
    if "csrfmiddlewaretoken" in html:
        return "Django"
    # Ruby on Rails
    if soup.find("meta", {"name": "csrf-param", "content": "authenticity_token"}):
        return "Ruby on Rails"
    # ASP.NET
    if "__VIEWSTATE" in html:
        return "ASP.NET WebForms"
    return None


def _detect_cdn(headers: Dict[str, str]) -> Optional[str]:
    h = {k.lower(): v for k, v in headers.items()}

    if "cf-ray" in h or h.get("server", "").lower() == "cloudflare":
        return "Cloudflare"
    if "x-amz-cf-id" in h or "x-amz-cf-pop" in h:
        return "AWS CloudFront"
    if "x-fastly-request-id" in h or "fastly" in h.get("via", "").lower():
        return "Fastly"
    if "x-akamai-request-id" in h or "akamaighhost" in h.get("server", "").lower():
        return "Akamai"
    if "x-cache" in h and "varnish" in h.get("x-cache", "").lower():
        return "Varnish"
    return None


def _detect_cms_from_tech(soup: BeautifulSoup, html: str) -> Optional[str]:
    """Simple CMS detection for tech card (full detection is in outdated.py)."""
    generator = soup.find("meta", {"name": re.compile(r"generator", re.I)})
    if generator:
        content = generator.get("content", "")
        if re.search(r"WordPress", content, re.I):
            return "WordPress"
        if re.search(r"Joomla", content, re.I):
            return "Joomla"
        if re.search(r"Drupal", content, re.I):
            return "Drupal"

    if "/wp-content/" in html:
        return "WordPress"
    if "squarespace" in html.lower()[:3000]:
        return "Squarespace"
    if "data-wix-" in html or "wix.com/dplugins" in html:
        return "Wix"
    if "shopify" in html.lower()[:3000] and "cdn.shopify.com" in html:
        return "Shopify"
    if "webflow" in html.lower()[:3000]:
        return "Webflow"
    return None


async def check_technology(soup: BeautifulSoup, headers: Dict[str, str], html: str) -> Dict[str, Any]:
    server = _detect_server(headers)
    x_powered_by = _detect_powered_by(headers)
    detected_framework = _detect_framework(soup, html)
    detected_cdn = _detect_cdn(headers)
    detected_cms = _detect_cms_from_tech(soup, html)

    return {
        "server_header": server,
        "x_powered_by": x_powered_by,
        "detected_cms": detected_cms,
        "detected_framework": detected_framework,
        "detected_cdn": detected_cdn,
    }
