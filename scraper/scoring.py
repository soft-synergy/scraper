"""
Scoring algorithms for outdated and security scores.
"""
from datetime import datetime
from typing import Dict, Any

CURRENT_YEAR = datetime.now().year


def calculate_outdated_score(data: Dict[str, Any]) -> int:
    """
    Returns 0-100 where higher = MORE outdated.
    """
    score = 0

    # Copyright year
    copyright_year = data.get("copyright_year")
    if copyright_year:
        years_old = CURRENT_YEAR - copyright_year
        if years_old >= 4:
            score += 30
        elif years_old >= 3:
            score += 20
        elif years_old >= 2:
            score += 10
    else:
        score += 5  # No copyright = slight concern

    # CMS version outdatedness (detected in check)
    cms_issues = [i for i in data.get("issues", []) if "outdated" in i.lower() and any(
        cms in i for cms in ["WordPress", "Joomla", "Drupal"]
    )]
    if cms_issues:
        score += 25
    elif data.get("cms_name") and not data.get("cms_version"):
        score += 10  # CMS detected but version unknown = can't rule out outdated

    # jQuery version
    jquery_version = data.get("jquery_version")
    if jquery_version:
        try:
            major = int(jquery_version.split(".")[0])
            if major == 1:
                score += 20
            elif major == 2:
                score += 10
        except (ValueError, IndexError):
            pass

    # Flash
    if data.get("has_flash"):
        score += 25

    # No mobile viewport
    if not data.get("has_viewport_meta", True):
        score += 15

    # Last-Modified header old
    lm_issues = [i for i in data.get("issues", []) if "Last-Modified" in i]
    if lm_issues:
        score += 10

    return min(100, score)


def calculate_security_score(data: Dict[str, Any]) -> int:
    """
    Returns 0-100 where higher = MORE secure.
    """
    score = 100

    # No HTTPS
    if not data.get("is_https"):
        score -= 40

    # SSL validity
    ssl_valid = data.get("ssl_valid")
    if ssl_valid is False:
        score -= 25
    elif ssl_valid is True:
        expiry_days = data.get("ssl_expiry_days")
        if expiry_days is not None:
            if expiry_days < 0:
                score -= 20  # Already expired
            elif expiry_days < 14:
                score -= 15
            elif expiry_days < 30:
                score -= 8

    # Security headers
    if not data.get("has_hsts"):
        score -= 10
    if not data.get("has_x_frame_options"):
        score -= 8
    if not data.get("has_csp"):
        score -= 10
    if not data.get("has_x_content_type"):
        score -= 7
    if not data.get("has_referrer_policy"):
        score -= 5

    # Mixed content
    if data.get("has_mixed_content"):
        score -= 15

    return max(0, score)


def get_outdated_label(score: int) -> str:
    if score >= 80:
        return "Critical"
    elif score >= 60:
        return "Significantly Outdated"
    elif score >= 40:
        return "Moderately Outdated"
    elif score >= 20:
        return "Slightly Outdated"
    return "Modern"


def get_security_label(score: int) -> str:
    if score >= 80:
        return "Secure"
    elif score >= 60:
        return "Mostly Secure"
    elif score >= 40:
        return "Moderate Risk"
    elif score >= 20:
        return "High Risk"
    return "Critical Risk"
