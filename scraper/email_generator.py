"""
AI-powered cold email generator — "Helpful Expert" strategy.
Model: GPT-4o-mini via OpenRouter (cost-effective).
Sender: Antoni Seba, specjalista od bezpieczeństwa stron internetowych.

Strategy: 5-touch sequence — audit alert → free value → direct question
          → social proof → door closer. Never pushy. Always valuable.
"""
import os
import json
import re
from datetime import datetime
from typing import Optional

import httpx

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "xiaomi/mimo-v2-omni"

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
    "pl": "Polish", "cs": "Czech", "de": "German", "fr": "French",
    "es": "Spanish", "it": "Italian", "nl": "Dutch", "en": "English",
}

OPT_OUT = {
    "pl": "Jeśli nie chcesz otrzymywać takich wiadomości, odpisz 'nie' — od razu Cię usunę.",
    "en": "If you'd rather not hear from me, just reply 'no' — I'll remove you right away.",
    "de": "Falls Sie keine weiteren E-Mails wünschen, antworten Sie 'nein' — ich entferne Sie sofort.",
    "cs": "Pokud nechcete další zprávy, odpovězte 'ne' — okamžitě vás odstraním.",
}


def _detect_language(keyword: str, country: str, domain: str = "") -> str:
    # Always Polish — target market is Poland
    return "pl"


def _build_page_context(site_data: dict) -> str:
    """Build a human-readable summary of what the page is about."""
    parts = []
    title = site_data.get("title")
    desc = site_data.get("page_description")
    headings_raw = site_data.get("page_headings")
    tech = site_data.get("tech_result") or {}

    if title:
        parts.append(f"Tytuł strony: {title}")
    if desc:
        parts.append(f"Opis (meta): {desc}")
    if headings_raw:
        try:
            import json as _json
            headings = _json.loads(headings_raw)
            if headings:
                parts.append("Nagłówki H1/H2: " + " | ".join(headings))
        except Exception:
            pass
    cms = tech.get("detected_cms") or tech.get("detected_framework")
    if cms:
        parts.append(f"Technologia: {cms}")
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
        return "  - (brak krytycznych błędów — email skup się na drobnych optymalizacjach)"
    return "\n".join(
        f"  - [{f['severity'].upper()}] {f.get(lang_key, f.get('en', ''))}"
        for f in findings[:limit]
    )


def _build_main_prompt(audit: dict, keyword: str, language: str, sender_offer: str, page_context: str = "") -> str:
    domain = audit["domain"] or "strona"
    findings = _findings_text(audit["findings"], "pl")
    opt_out = OPT_OUT["pl"]
    top_issue = audit["findings"][0]["pl"] if audit["findings"] else "kilka kwestii technicznych"
    page_ctx_section = f"\nKONTEKST STRONY (co wiemy o tym biznesie):\n{page_context}\n" if page_context else ""

    return f"""Jesteś {SENDER_NAME}, {SENDER_TITLE}. Piszesz cold email do właściciela strony {domain} (branża: {keyword}).
{page_ctx_section}

WYNIKI AUDYTU:
{findings}
Ocena: outdated={audit['outdated_score']}/100, bezpieczeństwo={audit['security_score']}/100

TWOJA OFERTA: {sender_offer}

━━━ FILOZOFIA TEGO EMAILA ━━━
Odbiorca to polski przedsiębiorca (np. trener, właściciel salonu, usługodawca). Ma gdzieś techniczne żargony.
Ale NIE ma gdzieś tego czy traci klientów przez kiepską stronę.

Twoje zadanie: wziąć suchy wynik techniczny z audytu i przetłumaczyć go na język jego biznesu.
Nie "brak nagłówka HSTS" — tylko "klient wchodzi na stronę, Chrome wyświetla ostrzeżenie, wychodzi do konkurencji".
Nie "copyright 2018" — tylko "strona wygląda jak porzucona, nowy klient to widzi i szuka kogoś aktywnego".

Ton: bezpośredni, konkretny, bez hype'u. Polski styl — kulturalny, żaden wykrzyknik, żadne CAPS.
Ale wartość pokazana ostro — tak żeby odbiorca poczuł że coś traci.

━━━ ZASADY ━━━

1. JĘZYK: Wyłącznie po polsku.

2. OTWIERANIE: "Dzień dobry," — obowiązkowo. Następnie 1 zdanie: kim jesteś i że przejrzałeś stronę w ramach analizy branży {keyword}.

3. SERCE EMAILA — WARTOŚĆ BIZNESOWA:
   Weź 1-2 najważniejsze znaleziska z audytu. Opisz je językiem strat biznesowych dla tej konkretnej branży ({keyword}):
   - Co przez to traci? (klienci, zapytania, pozycja w Google, zaufanie)
   - Jak to wygląda oczami potencjalnego klienta tej osoby?
   Żadnego technicznego żargonu bez tłumaczenia. Żadnych ogólników — tylko to co dotyczy {domain}.

4. CTA: Masz pełen raport z audytu — zaproponuj 15 min żeby go pokazać. Konkretnie i bez owijania w bawełnę.
   Link: {BOOKING_LINK}

5. DŁUGOŚĆ: Max 90 słów. Czytają na telefonie między klientami.

6. TEMAT: Konkretny, nawiązuje do ich branży lub strony. Bez wykrzykników.
   Dobry: "Znalazłem problem na {domain} — dotyczy pozyskiwania klientów"
   Zły: "Twoja strona TRACI klientów!!!"

7. PODPIS: {SENDER_NAME}, {SENDER_TITLE}

8. STOPKA po "---": "{opt_out}"

Odpowiedz WYŁĄCZNIE poprawnym JSON:
{{"subject": "...", "body": "..."}}"""


def _build_followup_prompt(audit: dict, keyword: str, language: str, sender_offer: str,
                            followup_num: int, day: int, original_subject: str) -> str:
    domain = audit["domain"] or "strona"
    findings = _findings_text(audit["findings"], "pl", limit=2)
    top_issue = audit["findings"][0]["pl"] if audit["findings"] else "problemy techniczne strony"
    opt_out = OPT_OUT["pl"]

    strategies = {
        1: f"""Jesteś {SENDER_NAME}, {SENDER_TITLE}. Wysyłasz follow-up #1 (dzień 3) do właściciela {domain} (branża: {keyword}).

Poprzedni temat: "{original_subject}"
Znaleziska: {findings}

━━━ ZADANIE ━━━
Nie pisz przypomnienia. Daj im coś użytecznego — jedną konkretną rzecz którą mogą zrobić sami dziś, żeby poprawić sytuację swojego biznesu online. Pokaż że rozumiesz ich branżę ({keyword}), nie tylko technikalia.

Opisz tę jedną rzecz językiem efektu biznesowego (co zyskają/przestaną tracić), nie językiem technicznym.
Na końcu zostaw link do pełnego raportu bez nacisku: {BOOKING_LINK}

Dzień dobry na początku. Max 80 słów. Bez wykrzykników. Temat: konkretny, nawiązuje do wartości.
STOPKA po "---": "{opt_out}"

JSON:
{{"subject": "...", "body": "..."}}""",

        2: f"""Jesteś {SENDER_NAME}, {SENDER_TITLE}. Wysyłasz follow-up #2 (dzień 7) do właściciela {domain} (branża: {keyword}).

Poprzedni temat: "{original_subject}"
Znaleziska: {findings}

━━━ ZADANIE ━━━
Krótki, wartościowy mail. Nie przypomnienie — jeden konkretny fakt który ich zaskoczy lub uświadomi coś o ich biznesie.
Np. jak Google ocenia strony z ich problemem technicznym, albo jak potencjalny klient widzi ich stronę vs konkurencję.
Konkretnie i po polsku — kulturalnie, ale bez owijania w bawełnę.
Krótkie CTA: {BOOKING_LINK}

Dzień dobry na początku. Max 60 słów. Temat: konkretny fakt lub pytanie retoryczne.
STOPKA po "---": "{opt_out}"

JSON:
{{"subject": "...", "body": "..."}}""",

        3: f"""Jesteś {SENDER_NAME}, {SENDER_TITLE}. Wysyłasz follow-up #3 (dzień 14) do właściciela {domain} (branża: {keyword}).

Poprzedni temat: "{original_subject}"
Główny problem: {top_issue[:100]}

━━━ ZADANIE ━━━
Krótka historia z praktyki — case z branży {keyword}. Ktoś miał ten sam problem co {domain}.
Co się zmieniło po naprawieniu? Realistycznie — nie "wzrost 300%" tylko "przestał tracić zapytania przez X" albo "Google zaczął go pokazywać wyżej".
Zostaw link: {BOOKING_LINK}

Dzień dobry na początku. Max 70 słów. Pisz jak człowiek, nie jak case study. Temat: nawiązuje do branży.
STOPKA po "---": "{opt_out}"

JSON:
{{"subject": "...", "body": "..."}}""",

        4: f"""Jesteś {SENDER_NAME}, {SENDER_TITLE}. Wysyłasz ostatni follow-up #4 (dzień 21) do właściciela {domain} (branża: {keyword}).

Poprzedni temat: "{original_subject}"

━━━ ZADANIE ━━━
Ostatnia wiadomość — powiedz to wprost na początku. W Polsce to jest cenione.
Zostaw im raport jako prezent — coś konkretnego czego mogą użyć kiedy przyjdzie czas: {BOOKING_LINK}
Życz powodzenia. Zostaw drzwi otwarte. Zero presji, zero żalu.

Dzień dobry na początku. Max 55 słów. Ciepły ale definitywny ton.
STOPKA po "---": "{opt_out}"

JSON:
{{"subject": "...", "body": "..."}}""",
    }

    return strategies[followup_num]


async def _call_llm(prompt: str) -> dict:
    api_key = os.environ.get("OPENROUTER_API_KEY", OPENROUTER_API_KEY)
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
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
                "max_tokens": 800,
                "temperature": 0.65,
            },
        )
        if not resp.is_success:
            raise ValueError(f"OpenRouter HTTP {resp.status_code}: {resp.text[:300]}")

    raw = resp.json()
    if "error" in raw:
        raise ValueError(f"OpenRouter error: {raw['error']}")
    text = raw["choices"][0]["message"].get("content") or ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    json_match = re.search(r'\{[^{}]*"subject"[^{}]*"body"[^{}]*\}', text, re.DOTALL)
    if json_match:
        return json.loads(json_match.group())
    return json.loads(text.strip())


_BOOKING_PLACEHOLDERS = [
    "[booking link]", "[rezervujte si konzultaci](booking link)", "[odkaz na rezervaci]",
    "[reservation link]", "[link rezerwacyjny]", "[link]", "[buchen]",
    "[calendly link]", "[calendar link]", "[cal.com link]",
]


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
    language = _detect_language(keyword, country, domain)
    audit = _build_audit_summary(site_data)
    page_context = _build_page_context(site_data)

    # Generate main email
    main_prompt = _build_main_prompt(audit, keyword, language, sender_offer, page_context)
    result = await _call_llm(main_prompt)
    result = _ensure_footer(result, language)
    result["language"] = language

    original_subject = result.get("subject", "")

    # Generate 4 follow-ups in parallel
    followup_schedule = [(1, 3), (2, 7), (3, 14), (4, 21)]
    import asyncio
    followup_tasks = [
        _call_llm(_build_followup_prompt(audit, keyword, language, sender_offer, num, day, original_subject))
        for num, day in followup_schedule
    ]
    followup_results = await asyncio.gather(*followup_tasks, return_exceptions=True)

    follow_ups = []
    for (num, day), fu_result in zip(followup_schedule, followup_results):
        if isinstance(fu_result, Exception):
            continue
        fu_result = _ensure_footer(fu_result, language)
        follow_ups.append({
            "follow_up_number": num,
            "send_on_day": day,
            "subject": fu_result.get("subject", ""),
            "body": fu_result.get("body", ""),
        })

    result["follow_ups"] = follow_ups

    # Recipient
    result["recipient_email"] = audit["emails"][0] if audit["emails"] else None

    return result
