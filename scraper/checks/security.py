"""
Security checks: HTTPS, SSL certificate, security headers, mixed content.
"""
import ssl
import socket
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple
from urllib.parse import urlparse

from bs4 import BeautifulSoup


def _check_ssl_cert(hostname: str) -> Dict[str, Any]:
    """Check SSL certificate validity using stdlib ssl module."""
    result = {"ssl_valid": None, "ssl_expiry_days": None, "ssl_issuer": None}
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((hostname, 443), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()

        expiry_str = cert.get("notAfter", "")
        if expiry_str:
            expiry_dt = datetime.strptime(expiry_str, "%b %d %H:%M:%S %Y %Z")
            expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
            days_left = (expiry_dt - datetime.now(timezone.utc)).days
            result["ssl_expiry_days"] = days_left

        issuer_parts = dict(x[0] for x in cert.get("issuer", []))
        result["ssl_issuer"] = issuer_parts.get("organizationName", issuer_parts.get("commonName"))
        result["ssl_valid"] = True

    except ssl.SSLCertVerificationError as e:
        result["ssl_valid"] = False
    except (socket.timeout, ConnectionRefusedError, OSError, socket.gaierror):
        result["ssl_valid"] = None  # Could not connect

    return result


def _check_security_headers(headers: Dict[str, str]) -> Tuple[Dict[str, bool], List[str]]:
    headers_lower = {k.lower(): v for k, v in headers.items()}
    issues = []

    has_hsts = "strict-transport-security" in headers_lower
    has_xfo = "x-frame-options" in headers_lower
    has_csp = "content-security-policy" in headers_lower
    has_xcto = headers_lower.get("x-content-type-options", "").lower() == "nosniff"
    has_rp = "referrer-policy" in headers_lower

    if not has_hsts:
        issues.append("Missing HSTS header (HTTP Strict Transport Security)")
    if not has_xfo:
        issues.append("Missing X-Frame-Options (vulnerable to clickjacking)")
    if not has_csp:
        issues.append("Missing Content-Security-Policy (XSS risk)")
    if not has_xcto:
        issues.append("Missing X-Content-Type-Options: nosniff (MIME sniffing risk)")
    if not has_rp:
        issues.append("Missing Referrer-Policy")

    return {
        "has_hsts": has_hsts,
        "has_x_frame_options": has_xfo,
        "has_csp": has_csp,
        "has_x_content_type": has_xcto,
        "has_referrer_policy": has_rp,
    }, issues


def _check_mixed_content(soup: BeautifulSoup, is_https: bool) -> Tuple[bool, List[str]]:
    if not is_https:
        return False, []

    issues = []
    mixed = False

    http_selectors = [
        ("img", "src"),
        ("script", "src"),
        ("link", "href"),
        ("iframe", "src"),
        ("audio", "src"),
        ("video", "src"),
        ("source", "src"),
    ]

    for tag, attr in http_selectors:
        for el in soup.find_all(tag, **{attr: True}):
            val = el.get(attr, "")
            if val.startswith("http://"):
                mixed = True
                break
        if mixed:
            break

    # Also check inline styles
    if not mixed:
        for style in soup.find_all("style"):
            if "http://" in style.get_text():
                mixed = True
                break

    if mixed:
        issues.append("Mixed content detected (HTTP resources loaded on HTTPS page)")

    return mixed, issues


async def check_security(
    url: str,
    headers: Dict[str, str],
    soup: BeautifulSoup,
    is_https: bool
) -> Dict[str, Any]:
    """Run all security checks."""
    all_issues = []

    if not is_https:
        all_issues.append("Site does not use HTTPS")

    # SSL cert check (run in thread to not block event loop)
    ssl_result = {"ssl_valid": None, "ssl_expiry_days": None, "ssl_issuer": None}
    if is_https:
        try:
            hostname = urlparse(url).hostname
            if hostname:
                ssl_result = await asyncio.to_thread(_check_ssl_cert, hostname)
                if ssl_result["ssl_valid"] is False:
                    all_issues.append("SSL certificate is invalid or untrusted")
                elif ssl_result["ssl_expiry_days"] is not None:
                    if ssl_result["ssl_expiry_days"] < 0:
                        all_issues.append(f"SSL certificate has EXPIRED {abs(ssl_result['ssl_expiry_days'])} days ago")
                    elif ssl_result["ssl_expiry_days"] < 30:
                        all_issues.append(f"SSL certificate expires in {ssl_result['ssl_expiry_days']} days")
        except Exception:
            pass

    header_results, header_issues = _check_security_headers(headers)
    all_issues.extend(header_issues)

    has_mixed, mixed_issues = _check_mixed_content(soup, is_https)
    all_issues.extend(mixed_issues)

    return {
        "is_https": is_https,
        "ssl_valid": ssl_result["ssl_valid"],
        "ssl_expiry_days": ssl_result["ssl_expiry_days"],
        "ssl_issuer": ssl_result["ssl_issuer"],
        "has_hsts": header_results["has_hsts"],
        "has_x_frame_options": header_results["has_x_frame_options"],
        "has_csp": header_results["has_csp"],
        "has_x_content_type": header_results["has_x_content_type"],
        "has_referrer_policy": header_results["has_referrer_policy"],
        "has_mixed_content": has_mixed,
        "issues": all_issues,
    }
