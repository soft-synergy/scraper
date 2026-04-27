"""
AI-powered cold email generator — "Helpful Expert" strategy.
Model: GPT-4o-mini via OpenRouter (cost-effective).
Sender: Antoni Seba, specjalista od bezpieczeństwa stron internetowych.

Strategy: 5-touch sequence — audit alert → free value → direct question
          → social proof → door closer. Never pushy. Always valuable.
"""
import asyncio
import os
import json
import re
from datetime import datetime
from typing import Optional

import httpx

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "google/gemini-3-flash-preview"

SENDER_NAME = "Piotr Serczynski"
SENDER_TITLE = "specjalista ds. bezpieczeństwa stron internetowych"
BOOKING_LINK = "https://cal.com/soft-synergy/30min"

LANG_BY_TLD = {
    "pl": "pl", "cz": "cs", "sk": "sk", "de": "de", "at": "de", "ch": "de",
    "fr": "fr", "es": "es", "it": "it", "nl": "nl",
    "hu": "hu", "ro": "ro", "ua": "uk",
    "uk": "en", "us": "en", "com": "en", "net": "en", "org": "en",
}

LANG_NAMES = {
    "pl": "Polish",
    "cs": "Czech",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "nl": "Dutch",
    "en": "English",
}

COUNTRY_TO_LANG = {
    "pl": "pl",
    "cz": "cs",
    "sk": "cs",
    "de": "de",
    "at": "de",
    "ch": "de",
    "fr": "fr",
    "es": "es",
    "it": "it",
    "nl": "nl",
    "uk": "en",
    "us": "en",
}

LANGUAGE_HINTS = {
    "pl": [
        " i ", " na ", " jest ", " oraz ", " kontakt ", " oferta ", " uslugi ",
        " strona ", " witamy ", " firma ", " klient ",
    ],
    "cs": [
        " a ", " na ", " je ", " kontakt ", " sluzby ", " sluzby ", " vitejte ",
        " firma ", " zakaznik ", " sluzby ", " web ",
    ],
    "de": [
        " und ", " ist ", " kontakt ", " angebot ", " dienstleistungen ", " willkommen ",
        " unternehmen ", " kunde ", " sicherheit ",
    ],
    "fr": [
        " et ", " est ", " contact ", " offre ", " services ", " bienvenue ",
        " entreprise ", " client ", " securite ",
    ],
    "es": [
        " y ", " contacto ", " oferta ", " servicios ", " bienvenida ", " empresa ",
        " cliente ", " seguridad ", " sitio ",
    ],
    "it": [
        " e ", " contatti ", " offerta ", " servizi ", " benvenuti ", " azienda ",
        " cliente ", " sicurezza ", " sito ",
    ],
    "nl": [
        " en ", " contact ", " aanbod ", " diensten ", " welkom ", " bedrijf ",
        " klant ", " beveiliging ", " website ",
    ],
    "en": [
        " and ", " the ", " contact ", " services ", " welcome ", " business ",
        " client ", " security ", " website ",
    ],
}

OPT_OUT = {
    "pl": "Jeśli nie chcesz otrzymywać takich wiadomości, odpisz 'nie' — od razu Cię usunę.",
    "en": "If you'd rather not hear from me, just reply 'no' — I'll remove you right away.",
    "de": "Falls Sie keine weiteren E-Mails wünschen, antworten Sie 'nein' — ich entferne Sie sofort.",
    "cs": "Pokud nechcete další zprávy, odpovězte 'ne' — okamžitě vás odstraním.",
}


def _normalize_lang_text(value: str) -> str:
    value = (value or "").lower()
    replacements = {
        "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n", "ó": "o", "ś": "s", "ź": "z", "ż": "z",
        "á": "a", "à": "a", "ä": "a", "â": "a",
        "é": "e", "è": "e", "ë": "e", "ê": "e",
        "í": "i", "ì": "i", "ï": "i", "î": "i",
        "ó": "o", "ò": "o", "ö": "o", "ô": "o",
        "ú": "u", "ù": "u", "ü": "u", "û": "u",
        "ý": "y", "č": "c", "ď": "d", "ě": "e", "ň": "n", "ř": "r", "š": "s", "ť": "t", "ž": "z",
    }
    for src, dst in replacements.items():
        value = value.replace(src, dst)
    return f" {value} "


def _safe_headings_list(headings_raw) -> list[str]:
    if not headings_raw:
        return []
    if isinstance(headings_raw, list):
        return [str(h) for h in headings_raw if h]
    try:
        parsed = json.loads(headings_raw)
        if isinstance(parsed, list):
            return [str(h) for h in parsed if h]
    except Exception:
        return []
    return []


def _detect_language(site_data: dict, keyword: str, country: str, domain: str = "") -> str:
    text_parts = [
        site_data.get("title", ""),
        site_data.get("page_description", ""),
        " ".join(_safe_headings_list(site_data.get("page_headings"))),
        keyword or "",
    ]
    normalized = _normalize_lang_text(" ".join(part for part in text_parts if part))

    scores = {lang: 0 for lang in LANG_NAMES}
    for lang, hints in LANGUAGE_HINTS.items():
        for hint in hints:
            scores[lang] += normalized.count(hint)

    best_lang = max(scores, key=scores.get)
    if scores[best_lang] > 0:
        return best_lang

    tld = domain.rsplit(".", 1)[-1].lower() if "." in (domain or "") else ""
    if tld in LANG_BY_TLD:
        return LANG_BY_TLD[tld]

    country_lang = COUNTRY_TO_LANG.get((country or "").lower())
    if country_lang:
        return country_lang

    return "en"


def _build_page_context(site_data: dict) -> str:
    """Build a human-readable summary of what the page is about."""
    parts = []
    title = site_data.get("title")
    desc = site_data.get("page_description")
    headings_raw = site_data.get("page_headings")
    tech = site_data.get("tech_result") or {}

    if title:
        parts.append(f"Page title: {title}")
    if desc:
        parts.append(f"Meta description: {desc}")
    if headings_raw:
        headings = _safe_headings_list(headings_raw)
        if headings:
            parts.append("Headings (H1/H2): " + " | ".join(headings))
    cms = tech.get("detected_cms") or tech.get("detected_framework")
    if cms:
        parts.append(f"Technology: {cms}")
    cdn = tech.get("detected_cdn")
    if cdn:
        parts.append(f"CDN: {cdn}")

    return "\n".join(parts) if parts else ""


def _build_audit_summary(site_data: dict) -> dict:
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
            "pl": "Strona działa na HTTP — dane odwiedzających nie są szyfrowane, Chrome wyświetla ostrzeżenie",
            "en": "Site runs on HTTP — visitor data unencrypted, Chrome shows security warning",
        })

    ssl_expiry = security.get("ssl_expiry_days")
    if ssl_expiry is not None and ssl_expiry < 30:
        findings.append({
            "severity": "critical",
            "pl": f"Certyfikat SSL wygasa za {ssl_expiry} dni — strona wkrótce stanie się niedostępna",
            "en": f"SSL expires in {ssl_expiry} days — site will go down soon",
        })
    elif security.get("ssl_valid") is False:
        findings.append({
            "severity": "critical",
            "pl": "Certyfikat SSL jest nieprawidłowy — przeglądarki blokują dostęp do strony",
            "en": "SSL certificate invalid — browsers block access to the site",
        })

    missing_headers = []
    if not security.get("has_hsts"):
        missing_headers.append("HSTS")
    if not security.get("has_csp"):
        missing_headers.append("CSP")
    if not security.get("has_x_frame_options"):
        missing_headers.append("X-Frame-Options")
    if missing_headers:
        findings.append({
            "severity": "warning",
            "pl": f"Brak nagłówków bezpieczeństwa ({', '.join(missing_headers)}) — strona podatna na clickjacking i ataki XSS",
            "en": f"Missing security headers ({', '.join(missing_headers)}) — site vulnerable to clickjacking and XSS",
        })

    if copyright_year and years_old and years_old >= 3:
        findings.append({
            "severity": "warning",
            "pl": f"Strona wygląda na nieaktualizowaną od {years_old} lat (copyright {copyright_year}) — to sygnał braku profesjonalizmu dla potencjalnych klientów",
            "en": f"Site looks abandoned for {years_old} years (copyright {copyright_year}) — signals lack of professionalism",
        })

    cms_name = outdated.get("cms_name")
    cms_version = outdated.get("cms_version")
    if cms_name and cms_version:
        findings.append({
            "severity": "warning",
            "pl": f"{cms_name} {cms_version} — prawdopodobnie niezaktualizowany, znane luki bezpieczeństwa",
            "en": f"{cms_name} {cms_version} — likely unpatched, known vulnerabilities",
        })

    if outdated.get("has_flash"):
        findings.append({
            "severity": "critical",
            "pl": "Flash na stronie — nie działa w żadnej nowoczesnej przeglądarce od 2020 roku",
            "en": "Flash detected — broken in all modern browsers since 2020",
        })

    if not outdated.get("has_viewport_meta", True):
        findings.append({
            "severity": "warning",
            "pl": "Strona nie jest responsywna (brak viewport meta) — słabo wygląda na telefonach, Google obniża pozycję",
            "en": "Not mobile-responsive — looks bad on phones, Google penalizes ranking",
        })

    emails = [c["value"] for c in contacts if c.get("type") == "email"]

    return {
        "findings": findings,
        "outdated_score": site_data.get("outdated_score") or 0,
        "security_score": site_data.get("security_score") or 100,
        "cms_name": cms_name,
        "copyright_year": copyright_year,
        "years_old": years_old,
        "emails": emails,
        "domain": site_data.get("domain", ""),
        "title": site_data.get("title", ""),
        "critical_count": sum(1 for f in findings if f["severity"] == "critical"),
        "warning_count": sum(1 for f in findings if f["severity"] == "warning"),
    }


def _findings_text(findings: list, lang_key: str, limit: int = 4) -> str:
    if not findings:
        return "  - No major issues found. Focus on a small but credible improvement."
    return "\n".join(
        f"  - [{f['severity'].upper()}] {f.get(lang_key, f.get('en', ''))}"
        for f in findings[:limit]
    )


def _build_main_prompt(
    audit: dict,
    keyword: str,
    language: str,
    sender_name: str,
    sender_title: str,
    sender_offer: str,
    page_context: str = "",
) -> str:
    domain = audit["domain"] or "strona"
    findings = _findings_text(audit["findings"], "en")
    opt_out = OPT_OUT.get(language, OPT_OUT["en"])
    language_name = LANG_NAMES.get(language, "English")
    page_ctx_section = f"\nWEBSITE CONTEXT:\n{page_context}\n" if page_context else ""

    return f"""You are {sender_name}, {sender_title}. Write a cold email to the owner of {domain}. Business niche: {keyword or 'unknown'}.
{page_ctx_section}

AUDIT FINDINGS:
{findings}
Scores: outdated={audit['outdated_score']}/100, security={audit['security_score']}/100

YOUR OFFER: {sender_offer}

RULES:
1. Write the entire email in {language_name}. Match the language used on the website. Do not default to Polish unless the website is clearly Polish.
2. Use the WEBSITE CONTEXT as primary context about the business. Reference what the site actually offers, not generic assumptions.
3. Translate technical findings into business impact. Show what they may be losing: trust, leads, conversions, search visibility, or credibility.
4. Mention 1-2 strongest issues only. Keep it specific to this site and this business niche.
5. Tone: direct, calm, credible, human. No hype, no exclamation marks, no ALL CAPS.
6. Keep the body under 90 words.
7. Include a concrete CTA offering a short 15-minute walkthrough of the report and include this link: {BOOKING_LINK}
8. Sign EXACTLY as: {sender_name}, {sender_title} — NEVER write "Your Name", "[Name]", or any placeholder. The sender name is {sender_name}.
9. After a separator line '---', include exactly this opt-out line in {language_name} if available: "{opt_out}"
10. Return valid JSON only.

Output JSON:
{{"subject": "...", "body": "..."}}"""


def _build_followup_prompt(
    audit: dict,
    keyword: str,
    language: str,
    sender_name: str,
    sender_title: str,
    sender_offer: str,
    followup_num: int,
    day: int,
    original_subject: str,
    page_context: str = "",
) -> str:
    domain = audit["domain"] or "strona"
    findings = _findings_text(audit["findings"], "en", limit=2)
    top_issue = audit["findings"][0].get("en", "website issues") if audit["findings"] else "website issues"
    opt_out = OPT_OUT.get(language, OPT_OUT["en"])
    language_name = LANG_NAMES.get(language, "English")
    page_ctx_section = f"\nWEBSITE CONTEXT:\n{page_context}\n" if page_context else ""

    strategies = {
        1: f"""You are {sender_name}, {sender_title}. Write follow-up #1 for day {day} to the owner of {domain}. Niche: {keyword or 'unknown'}.
Previous subject: "{original_subject}"
{page_ctx_section}
FINDINGS:
{findings}

Write in {language_name}, matching the website language.
Do not write a reminder. Share one practical thing they can improve today and explain the business effect in plain language.
Keep it under 80 words, calm and useful, with a soft CTA linking to {BOOKING_LINK}.
Sign EXACTLY as: {sender_name}, {sender_title} — NEVER use "Your Name" or any placeholder.
After '---', include: "{opt_out}"
Return JSON only.

JSON:
{{"subject": "...", "body": "..."}}""",

        2: f"""You are {sender_name}, {sender_title}. Write follow-up #2 for day {day} to the owner of {domain}. Niche: {keyword or 'unknown'}.
Previous subject: "{original_subject}"
{page_ctx_section}
FINDINGS:
{findings}

Write in {language_name}, matching the website language.
Keep it short and valuable. Share one concrete observation about how this issue can affect visibility, trust, or conversions for this type of business.
Keep it under 60 words and include a brief CTA with {BOOKING_LINK}.
Sign EXACTLY as: {sender_name}, {sender_title} — NEVER use "Your Name" or any placeholder.
After '---', include: "{opt_out}"
Return JSON only.

JSON:
{{"subject": "...", "body": "..."}}""",

        3: f"""You are {sender_name}, {sender_title}. Write follow-up #3 for day {day} to the owner of {domain}. Niche: {keyword or 'unknown'}.
Previous subject: "{original_subject}"
{page_ctx_section}
Main issue: {top_issue[:120]}

Write in {language_name}, matching the website language.
Use a short realistic example from a similar business. Explain what improved after fixing a similar issue without making exaggerated claims.
Keep it under 70 words and include {BOOKING_LINK}.
Sign EXACTLY as: {sender_name}, {sender_title} — NEVER use "Your Name" or any placeholder.
After '---', include: "{opt_out}"
Return JSON only.

JSON:
{{"subject": "...", "body": "..."}}""",

        4: f"""You are {sender_name}, {sender_title}. Write the final follow-up #4 for day {day} to the owner of {domain}. Niche: {keyword or 'unknown'}.
Previous subject: "{original_subject}"
{page_ctx_section}

Write in {language_name}, matching the website language.
Make it clear this is the last note. Leave the report as a useful resource for later, wish them well, and keep the door open without pressure.
Keep it under 55 words and include {BOOKING_LINK}.
Sign EXACTLY as: {sender_name}, {sender_title} — NEVER use "Your Name" or any placeholder.
After '---', include: "{opt_out}"
Return JSON only.

JSON:
{{"subject": "...", "body": "..."}}""",
    }

    return strategies[followup_num]


async def _call_llm(prompt: str) -> dict:
    api_key = os.environ.get("OPENROUTER_API_KEY", OPENROUTER_API_KEY)
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    last_error = None
    for attempt in range(3):
        if attempt > 0:
            await asyncio.sleep(2 ** attempt)  # 2s, 4s
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            resp = await client.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://webleadscraper.pl",
                    "X-Title": "Piotr Serczynski",
                },
                json={
                    "model": MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 4096,
                    "temperature": 0.65,
                },
            )
        if resp.status_code in (500, 502, 503, 504):
            last_error = ValueError(f"OpenRouter HTTP {resp.status_code}: {resp.text[:300]}")
            continue
        if not resp.is_success:
            raise ValueError(f"OpenRouter HTTP {resp.status_code}: {resp.text[:300]}")
        break
    else:
        raise last_error

    raw = resp.json()

    if "error" in raw:
        raise ValueError(f"OpenRouter error: {raw['error']}")
    text = raw["choices"][0]["message"].get("content") or ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    if not text:
        raise ValueError(f"OpenRouter returned empty content. Raw: {str(raw)[:300]}")

    # Find outermost JSON object (handles nested braces in body)
    start = text.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i+1])
                    except json.JSONDecodeError:
                        break

    return json.loads(text)


_BOOKING_PLACEHOLDERS = [
    "[booking link]", "[rezervujte si konzultaci](booking link)", "[odkaz na rezervaci]",
    "[reservation link]", "[link rezerwacyjny]", "[link]", "[buchen]",
    "[calendly link]", "[calendar link]", "[cal.com link]",
]


_NAME_PLACEHOLDERS = [
    "Your Name", "[Your Name]", "[Name]", "[Imię]", "[Imię i nazwisko]",
    "[Ihr Name]", "[Votre nom]", "[Su nombre]", "[Il tuo nome]",
]


def _fix_sender_name(result: dict, sender_name: str, sender_title: str) -> dict:
    body = result.get("body", "")
    subject = result.get("subject", "")
    for placeholder in _NAME_PLACEHOLDERS:
        body = body.replace(placeholder, sender_name)
        subject = subject.replace(placeholder, sender_name)
    result["body"] = body
    result["subject"] = subject
    return result


def _ensure_footer(result: dict, language: str) -> dict:
    """Guarantee opt-out footer and real booking link are present."""
    body = result.get("body", "")

    # Replace any placeholder booking links with the real URL
    for placeholder in _BOOKING_PLACEHOLDERS:
        body = body.replace(placeholder, BOOKING_LINK)

    # Append booking link if still missing
    if BOOKING_LINK not in body:
        body = body.rstrip() + f"\n\n{BOOKING_LINK}"

    # Append opt-out if missing
    opt_out = OPT_OUT.get(language, OPT_OUT["en"])
    opt_out_markers = ["usunę", "usuniemy", "remove you", "entfernen", "odstraním", "odstraním", "odstranem"]
    if not any(m in body for m in opt_out_markers):
        body = body.rstrip() + f"\n\n---\n{opt_out}"

    result["body"] = body
    return result


async def generate_email(
    site_data: dict,
    keyword: str,
    country: str = "auto",
    sender_name: str = SENDER_NAME,
    sender_offer: str = "audyt i naprawa bezpieczeństwa strony internetowej",
) -> dict:
    """Generate main cold email + 4 follow-ups."""
    domain = site_data.get("domain", "")
    language = _detect_language(site_data, keyword, country, domain)
    audit = _build_audit_summary(site_data)
    page_context = _build_page_context(site_data)

    # Generate main email
    main_prompt = _build_main_prompt(
        audit,
        keyword,
        language,
        sender_name,
        SENDER_TITLE,
        sender_offer,
        page_context,
    )
    result = await _call_llm(main_prompt)
    result = _fix_sender_name(result, sender_name, SENDER_TITLE)
    result = _ensure_footer(result, language)
    result["language"] = language

    original_subject = result.get("subject", "")

    # Generate 4 follow-ups sequentially to avoid rate limiting
    followup_schedule = [(1, 3), (2, 7), (3, 14), (4, 21)]
    follow_ups = []
    for num, day in followup_schedule:
        try:
            fu_result = await _call_llm(_build_followup_prompt(
                audit,
                keyword,
                language,
                sender_name,
                SENDER_TITLE,
                sender_offer,
                num,
                day,
                original_subject,
                page_context,
            ))
            fu_result = _fix_sender_name(fu_result, sender_name, SENDER_TITLE)
            fu_result = _ensure_footer(fu_result, language)
            follow_ups.append({
                "follow_up_number": num,
                "send_on_day": day,
                "subject": fu_result.get("subject", ""),
                "body": fu_result.get("body", ""),
            })
        except Exception:
            pass

    result["follow_ups"] = follow_ups

    # Recipient
    result["recipient_email"] = audit["emails"][0] if audit["emails"] else None

    return result
