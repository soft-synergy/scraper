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

SENDER_NAME = "Antoni Seba"
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


def _build_main_prompt(audit: dict, keyword: str, language: str, sender_offer: str) -> str:
    domain = audit["domain"] or "strona"
    findings = _findings_text(audit["findings"], "pl")
    opt_out = OPT_OUT["pl"]
    top_issue = audit["findings"][0]["pl"] if audit["findings"] else "kilka kwestii technicznych"

    return f"""Jesteś {SENDER_NAME}, {SENDER_TITLE}. Piszesz cold email do właściciela strony {domain} (branża: {keyword}).

WYNIKI AUDYTU:
{findings}
Ocena: outdated={audit['outdated_score']}/100, bezpieczeństwo={audit['security_score']}/100

TWOJA OFERTA: {sender_offer}

━━━ KONTEKST KULTUROWY ━━━
Piszesz do polskiego przedsiębiorcy. W Polsce zimne maile sprzedażowe są odbierane bardzo sceptycznie.
Skuteczny email w polskich realiach to NIE "alarm sprzedażowy" — to krótka, rzeczowa wiadomość od eksperta,
który dzieli się czymś konkretnie przydatnym. Żadnej presji, żadnego hype'u, żadnych wykrzykników.
Ton: spokojny, profesjonalny, pomocny. Jakbyś pisał do znajomego z branży.

━━━ ZASADY ━━━

1. JĘZYK: Wyłącznie po polsku.

2. OTWIERANIE: Zacznij od "Dzień dobry," — to jest standard w polskiej korespondencji biznesowej.
   Drugie zdanie: krótko kim jesteś i skąd masz info ("przeprowadziłem rutynowy audyt stron w branży {keyword}").

3. KONKRETNY FAKT: Wspomnij 1-2 rzeczy z audytu — spokojnie, jak obserwacja, nie alarm.
   Np. "Zauważyłem, że strona nie ma certyfikatu SSL" lub "copyright wskazuje na {audit.get('copyright_year', 'stary rok')} — strona może sprawiać wrażenie nieaktualnej".
   Przetłumacz na praktyczny efekt dla ich firmy (nie strasz, informuj).

4. PROPOZYCJA: Zaproponuj 15-minutową rozmowę żeby pokazać pełne wyniki audytu — bez zobowiązań.
   Sformułuj to jako "jeśli byłoby to pomocne" — nie naciskaj.
   Link: {BOOKING_LINK}

5. DŁUGOŚĆ: Max 90 słów w treści. Polacy nie czytają długich maili od nieznajomych.

6. TEMAT: 5-7 słów, rzeczowy, bez wykrzykników i straszenia.
   Dobry: "Audyt bezpieczeństwa strony {domain}" lub "Kilka uwag po analizie {domain}"
   Zły: "UWAGA: Twoja strona traci klientów!"

7. PODPIS: Imię i nazwisko, stanowisko — bez linków w podpisie.

8. STOPKA (obowiązkowa po pustej linii i "---"):
   "{opt_out}"

Odpowiedz WYŁĄCZNIE poprawnym JSON:
{{"subject": "...", "body": "..."}}"""


def _build_followup_prompt(audit: dict, keyword: str, language: str, sender_offer: str,
                            followup_num: int, day: int, original_subject: str) -> str:
    domain = audit["domain"] or "strona"
    findings = _findings_text(audit["findings"], "pl", limit=2)
    top_issue = audit["findings"][0]["pl"] if audit["findings"] else "problemy techniczne strony"
    opt_out = OPT_OUT["pl"]

    strategies = {
        1: f"""Jesteś {SENDER_NAME}, {SENDER_TITLE}. Wysyłasz follow-up #1 (3 dni po pierwszym mailu) do właściciela {domain} (branża: {keyword}).

Temat poprzedniego emaila: "{original_subject}"

ZNALEZISKA Z AUDYTU:
{findings}

━━━ KONTEKST ━━━
Polski przedsiębiorca, który nie odpowiedział na pierwszego maila. Nie ignoruj tego — po prostu jest zajęty.
Nie naciskaj. Daj mu coś konkretnie użytecznego, za darmo, bez oczekiwania czegoś w zamian.

━━━ ZASADY ━━━
1. JĘZYK: Wyłącznie po polsku.
2. Zacznij od "Dzień dobry," — krótko nawiąż do poprzedniej wiadomości.
3. Podaj JEDNĄ praktyczną wskazówkę z audytu którą właściciel może zastosować samodzielnie — opisz konkretnie jak.
4. Na końcu wspomnij miękko że masz pełen raport, jeśli byłoby pomocne: {BOOKING_LINK}
5. Żadnej presji, żadnego "ostatnia szansa". Ton: pomocny kolega z branży.
6. Max 80 słów. Temat: rzeczowy, np. "Mała wskazówka dot. {domain}"
7. STOPKA po "---": "{opt_out}"

Odpowiedz WYŁĄCZNIE JSON:
{{"subject": "...", "body": "..."}}""",

        2: f"""Jesteś {SENDER_NAME}, {SENDER_TITLE}. Wysyłasz follow-up #2 (7 dni po pierwszym mailu) do właściciela {domain} (branża: {keyword}).

Temat poprzedniego emaila: "{original_subject}"

ZNALEZISKA Z AUDYTU:
{findings}

━━━ KONTEKST ━━━
Dwa maile bez odpowiedzi. Szanujesz to — po prostu krótko przypominasz o sobie.
W Polsce nachalność jest bardzo źle odbierana. Ten mail ma być krótki i bez presji.

━━━ ZASADY ━━━
1. JĘZYK: Wyłącznie po polsku.
2. Zacznij od "Dzień dobry," — jedno zdanie że wracasz do tematu.
3. Przypomnij w jednym zdaniu najważniejsze znalezisko z audytu — spokojnie, bez alarmu.
4. Zaproponuj krótką rozmowę jeśli temat jest aktualny: {BOOKING_LINK}
5. Wyraźnie zaznacz że rozumiesz jeśli to nie jest dobry moment — bez urazy.
6. Max 50 słów. Temat: "Nawiązanie do poprzedniej wiadomości" lub podobny.
7. STOPKA po "---": "{opt_out}"

Odpowiedz WYŁĄCZNIE JSON:
{{"subject": "...", "body": "..."}}""",

        3: f"""Jesteś {SENDER_NAME}, {SENDER_TITLE}. Wysyłasz follow-up #3 (14 dni po pierwszym mailu) do właściciela {domain} (branża: {keyword}).

Temat poprzedniego emaila: "{original_subject}"

ZNALEZISKA Z AUDYTU:
{findings}

━━━ KONTEKST ━━━
Trzy maile bez odpowiedzi. Zamiast kolejnego przypomnienia — podziel się czymś wartościowym.
Krótka historia z praktyki. Bez wielkich liczb, bez reklamy — zwykły przypadek z pracy.

━━━ ZASADY ━━━
1. JĘZYK: Wyłącznie po polsku.
2. Zacznij od "Dzień dobry," — jedno zdanie wprowadzenia.
3. Opisz krótko case z podobnej branży ({keyword}) — konkretny problem, konkretny efekt po naprawieniu. Realistycznie, nie "300% wzrostu".
4. Wspomnij że {domain} ma podobny problem: {top_issue[:80]}.
5. Zostaw link bez nacisku: {BOOKING_LINK}
6. Max 70 słów. Temat: np. "Przypadek z branży {keyword}"
7. STOPKA po "---": "{opt_out}"

Odpowiedz WYŁĄCZNIE JSON:
{{"subject": "...", "body": "..."}}""",

        4: f"""Jesteś {SENDER_NAME}, {SENDER_TITLE}. Wysyłasz ostatni follow-up #4 (21 dni po pierwszym mailu) do właściciela {domain} (branża: {keyword}).

Temat poprzedniego emaila: "{original_subject}"

━━━ KONTEKST ━━━
To ostatnia wiadomość. W Polsce docenia się gdy ktoś szanuje granice — powiedz wprost że to ostatni mail.
Zostaw drzwi otwarte, bez żalu, bez presji. Ciepło i po ludzku.

━━━ ZASADY ━━━
1. JĘZYK: Wyłącznie po polsku.
2. Zacznij od "Dzień dobry," — powiedz że to ostatnia wiadomość w tej sprawie.
3. Zostaw link do raportu na przyszłość gdy przyjdzie odpowiedni moment: {BOOKING_LINK}
4. Życz powodzenia z {domain} — szczerze, bez ironii.
5. Max 55 słów. Ton: spokojny, życzliwy, definitywny.
6. Temat: np. "Ostatnia wiadomość — {domain}"
7. STOPKA po "---": "{opt_out}"

Odpowiedz WYŁĄCZNIE JSON:
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
        resp.raise_for_status()

    raw = resp.json()
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

    # Generate main email
    main_prompt = _build_main_prompt(audit, keyword, language, sender_offer)
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
