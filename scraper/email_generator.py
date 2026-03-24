"""
AI-powered cold email generator using OpenRouter (DeepSeek R1).
Generates highly personalized, conversion-focused cold emails based on real website audit data.
"""
import os
import json
import re
from datetime import datetime
from typing import Optional

import httpx

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "deepseek/deepseek-v3.2"


LANG_BY_TLD = {
    "pl": "pl", "cz": "cs", "sk": "sk", "de": "de", "at": "de", "ch": "de",
    "fr": "fr", "es": "es", "it": "it", "nl": "nl", "be": "nl",
    "hu": "hu", "ro": "ro", "bg": "bg", "hr": "hr", "rs": "rs",
    "ua": "uk", "ru": "ru",
    "uk": "en", "us": "en", "com": "en", "net": "en", "org": "en",
}

LANG_NAMES = {
    "pl": "Polish", "cs": "Czech", "sk": "Slovak", "de": "German",
    "fr": "French", "es": "Spanish", "it": "Italian", "nl": "Dutch",
    "hu": "Hungarian", "ro": "Romanian", "uk": "Ukrainian", "ru": "Russian",
    "en": "English",
}


def _detect_language(keyword: str, country: str, domain: str = "") -> str:
    """Detect language from domain TLD first, then country/keyword fallback."""
    # TLD from domain is the most reliable signal
    if domain:
        tld = domain.rsplit(".", 1)[-1].lower().split("/")[0].split(":")[0]
        if tld in LANG_BY_TLD:
            return LANG_BY_TLD[tld]

    if country == "us":
        return "en"
    if country == "pl":
        return "pl"

    pl_roots = [
        "dentysta", "dental", "mechanik", "prawnik", "adwokat", "lekarz",
        "fryzjer", "kosmetyczka", "hotel", "restauracja", "sklep", "firma",
        "usługi", "warsztat", "gabinet", "kancelaria", "agencja", "biuro",
        "polska", "kraków", "warszawa", "poznań", "wrocław", "łódź", "gdańsk",
    ]
    if any(root in keyword.lower() for root in pl_roots):
        return "pl"
    return "en"


def _build_audit_summary(site_data: dict) -> dict:
    """Extract the most impactful audit findings to use in the email."""
    outdated = site_data.get("outdated_result") or {}
    security = site_data.get("security_result") or {}
    contacts = site_data.get("contacts") or []

    current_year = datetime.now().year
    copyright_year = outdated.get("copyright_year")
    years_old = (current_year - copyright_year) if copyright_year else None

    findings = []

    if not security.get("is_https"):
        findings.append({
            "severity": "critical",
            "pl": "Strona działa na HTTP (nie HTTPS) — dane odwiedzających nie są szyfrowane",
            "en": "Site runs on HTTP (not HTTPS) — visitor data is unencrypted",
        })

    ssl_expiry = security.get("ssl_expiry_days")
    if ssl_expiry is not None and ssl_expiry < 30:
        findings.append({
            "severity": "critical",
            "pl": f"Certyfikat SSL wygasa za {ssl_expiry} dni — strona wkrótce wyświetli ostrzeżenie",
            "en": f"SSL certificate expires in {ssl_expiry} days — site will show security warning soon",
        })
    elif security.get("ssl_valid") is False:
        findings.append({
            "severity": "critical",
            "pl": "Certyfikat SSL jest nieprawidłowy — przeglądarki ostrzegają przed wejściem",
            "en": "SSL certificate is invalid — browsers warn visitors before entering",
        })

    missing_headers = []
    if not security.get("has_hsts"):
        missing_headers.append("HSTS")
    if not security.get("has_csp"):
        missing_headers.append("Content-Security-Policy")
    if not security.get("has_x_frame_options"):
        missing_headers.append("X-Frame-Options")
    if not security.get("has_x_content_type"):
        missing_headers.append("X-Content-Type-Options")
    if missing_headers:
        findings.append({
            "severity": "warning",
            "pl": f"Brakuje {len(missing_headers)} nagłówków bezpieczeństwa ({', '.join(missing_headers)})",
            "en": f"Missing {len(missing_headers)} security headers ({', '.join(missing_headers)})",
        })

    if copyright_year and years_old and years_old >= 3:
        findings.append({
            "severity": "warning",
            "pl": f"Strona nie była aktualizowana od {years_old} lat (copyright {copyright_year})",
            "en": f"Site hasn't been updated in {years_old} years (copyright {copyright_year})",
        })

    cms_name = outdated.get("cms_name")
    cms_version = outdated.get("cms_version")
    if cms_name:
        label = f"{cms_name} {cms_version}" if cms_version else cms_name
        findings.append({
            "severity": "info",
            "pl": f"Strona używa {label}",
            "en": f"Site runs on {label}",
        })

    jquery_version = outdated.get("jquery_version")
    if jquery_version:
        findings.append({
            "severity": "warning",
            "pl": f"Przestarzała wersja jQuery {jquery_version}",
            "en": f"Outdated jQuery {jquery_version}",
        })

    if outdated.get("has_flash"):
        findings.append({
            "severity": "critical",
            "pl": "Wykryto Flash — nie działa w żadnej nowoczesnej przeglądarce od 2020",
            "en": "Flash detected — broken in all modern browsers since 2020",
        })

    if not outdated.get("has_viewport_meta", True):
        findings.append({
            "severity": "warning",
            "pl": "Strona nie jest mobilna (brak viewport meta)",
            "en": "Site is not mobile-responsive (missing viewport meta)",
        })

    emails = [c["value"] for c in contacts if c.get("type") == "email"]

    return {
        "findings": findings,
        "outdated_score": site_data.get("outdated_score"),
        "security_score": site_data.get("security_score"),
        "cms_name": cms_name,
        "copyright_year": copyright_year,
        "years_old": years_old,
        "emails": emails,
        "domain": site_data.get("domain"),
        "title": site_data.get("title"),
        "is_https": security.get("is_https", False),
        "critical_count": sum(1 for f in findings if f["severity"] == "critical"),
        "warning_count": sum(1 for f in findings if f["severity"] == "warning"),
    }


def _build_prompt(site_data: dict, keyword: str, language: str, sender_name: str, sender_offer: str) -> str:
    audit = _build_audit_summary(site_data)
    domain = audit["domain"] or "their website"
    title = audit["title"] or domain
    findings = audit["findings"]
    lang_key = "pl" if language == "pl" else "en"
    lang_name = LANG_NAMES.get(language, "English")

    findings_text = "\n".join(f"  - [{f['severity'].upper()}] {f[lang_key if lang_key in f else 'en']}" for f in findings[:6]) if findings else \
        "  - (no critical issues found)"

    outdated_score = audit["outdated_score"] or 0
    security_score = audit["security_score"] or 100
    critical_count = audit["critical_count"]
    warning_count = audit["warning_count"]

    # Pattern interrupt examples by language
    interrupt_examples = {
        "pl": [
            f"Znalazłem coś niepokojącego na {domain} — zajmie Ci to 2 minuty żeby sprawdzić.",
            f"3 rzeczy na {domain} które aktywnie blokują nowych klientów przez Google.",
            f"Przeprowadziłem szybki audyt {domain} i szczerze mówiąc — wyniki mnie zaskoczyły.",
            f"Piszę, bo {domain} ma problem który prawdopodobnie kosztuje Was klientów każdego miesiąca.",
        ],
        "cs": [
            f"Našel jsem něco znepokojivého na {domain} — trvá to jen 2 minuty.",
            f"3 technické problémy na {domain} které blokují nové pacienty z Google.",
        ],
        "de": [
            f"Ich habe etwas Beunruhigendes auf {domain} gefunden — dauert 2 Minuten zu prüfen.",
            f"3 Probleme auf {domain}, die aktiv neue Kunden von Google blockieren.",
        ],
        "en": [
            f"Found something on {domain} that's likely costing you clients every month.",
            f"3 technical issues on {domain} that are actively blocking new customers from Google.",
            f"Quick audit of {domain} — honestly, the results surprised me.",
        ],
    }
    examples = interrupt_examples.get(language, interrupt_examples["en"])
    examples_text = "\n".join(f'  "{e}"' for e in examples)

    return f"""You are a world-class B2B cold email copywriter. You write SHORT, hyper-personalized cold emails with extremely high reply rates.

WEBSITE AUDIT DATA:
- Domain: {domain}
- Page title: {title}
- Niche: {keyword}
- Outdated Score: {outdated_score}/100 (higher = more outdated)
- Security Score: {security_score}/100 (higher = more secure)
- Critical issues: {critical_count} | Warnings: {warning_count}

REAL TECHNICAL FINDINGS FROM SCAN:
{findings_text}

SENDER: {sender_name} | Service: {sender_offer}

━━━ CRITICAL RULES (non-negotiable) ━━━

RULE 1 — LANGUAGE: Write ENTIRELY in {lang_name} ({language}). Every single word. Do not mix languages.

RULE 2 — NO PERSONAL NAMES: NEVER address by first name (not "Anna", not "Panie Kowalski"). The email goes to a receptionist or office manager, not the owner directly. Use neutral openings: "Dzień dobry," / "Hej," / "Hallo," / "Hello," etc.

RULE 3 — PATTERN INTERRUPT OPENING: The VERY FIRST sentence must be a pattern interrupt — a shocking, curiosity-provoking, or fear-of-loss statement. It must make the reader stop scrolling. NOT "I noticed your website..." — that's dead on arrival.

Examples of good pattern interrupt openers for this site:
{examples_text}

RULE 4 — BUSINESS PAIN, NOT TECH JARGON: Translate every technical finding into a BUSINESS consequence. Never say "missing HSTS header" — say "hackers can hijack your patients' sessions". Never say "outdated copyright" — say "potential patients judge your professionalism by your website and this one looks abandoned since {audit.get('copyright_year', 'years ago')}". The PAIN must be: lost patients, lost revenue, damaged reputation, legal risk, or Google ranking drop.

RULE 5 — SHOW VALUE (the "so what"): For each problem, hint at the upside of fixing it. Example: "Once SSL is fixed, Google re-indexes within 2 weeks. Our last dental client saw 30% more calls." Give them a reason to act NOW.

RULE 6 — SPECIFICITY: Reference EXACT data from the audit (specific score, specific year, specific technology). Generic = trash can.

RULE 7 — LENGTH: MAX 120 words in body. People read on phones.

RULE 8 — STRUCTURE:
  Line 1: Pattern interrupt — 1 sentence that makes them stop. About THEIR specific situation. Fear of loss or shocking stat.
  Lines 2-3: 2 specific findings from the audit → each = one concrete business consequence (lost patient, Google penalty, Chrome warning blocking visitors)
  Line 4: "Zrobiłem pełny audyt [domena] — jest więcej." / "I ran a full audit of [domain] — there's more."
  Line 5: CTA — invite them to a 15-min call specifically TO SEE THE AUDIT RESULTS. Frame it as: "I'll show you everything I found + how to fix it — 15 min on Zoom, no cost." They get VALUE (seeing the audit) by booking. NOT "let's chat" — "I'll SHOW YOU the full report." End with the booking link on its own line: https://cal.com/soft-synergy/30min

RULE 9 — SUBJECT LINE: Must trigger curiosity or fear of loss. Max 8 words. Reference their domain or a specific problem. Not: "Website improvements" — yes: "3 things losing {domain} patients this week".

Reply with ONLY a JSON object, zero text outside it:
{{"subject": "...", "body": "...", "language": "{language}"}}"""


async def generate_email(
    site_data: dict,
    keyword: str,
    country: str = "auto",
    sender_name: str = "Your Name",
    sender_offer: str = "professional website redesign & security fixes",
) -> dict:
    """Generate a personalized cold email via OpenRouter DeepSeek R1."""
    domain = site_data.get("domain", "")
    language = _detect_language(keyword, country, domain)
    prompt = _build_prompt(site_data, keyword, language, sender_name, sender_offer)

    api_key = os.environ.get("OPENROUTER_API_KEY", OPENROUTER_API_KEY)
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        resp = await client.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://webleadscraper.pl",
                "X-Title": "WebLeadScraper",
            },
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1024,
                "temperature": 0.7,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    text = data["choices"][0]["message"]["content"]

    # Strip DeepSeek <think>...</think> reasoning block if present
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    # Extract JSON
    json_match = re.search(r'\{[^{}]*"subject"[^{}]*"body"[^{}]*\}', text, re.DOTALL)
    if json_match:
        result = json.loads(json_match.group())
    else:
        result = json.loads(text.strip())

    audit = _build_audit_summary(site_data)
    result["recipient_email"] = audit["emails"][0] if audit["emails"] else None
    result["language"] = language

    # Always append booking link (don't rely on LLM to include it)
    BOOKING_LINK = "https://cal.com/soft-synergy/30min"
    if BOOKING_LINK not in result.get("body", ""):
        result["body"] = result["body"].rstrip() + f"\n\n{BOOKING_LINK}"

    return result
