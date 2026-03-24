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
    has_critical = audit["critical_count"] > 0
    top_issue = audit["findings"][0]["pl"] if audit["findings"] else "kilka kwestii technicznych"

    return f"""Jesteś {SENDER_NAME}, {SENDER_TITLE}. Piszesz krótki, szczery cold email do właściciela strony {domain} (branża: {keyword}).

WYNIKI AUDYTU STRONY {domain}:
{findings}
Ogólna ocena: outdated={audit['outdated_score']}/100, bezpieczeństwo={audit['security_score']}/100

TWOJA OFERTA: {sender_offer}

━━━ CEL TEGO EMAILA ━━━
Nie sprzedajesz. Informujesz. Chcesz żeby odbiorca odpowiedział lub zarezerwował 15 min na Zoom żeby ZOBACZYĆ wyniki audytu — to dla nich darmowa wartość.

━━━ ZASADY (ścisłe) ━━━

1. JĘZYK: Wyłącznie po polsku. Każde słowo.

2. OTWIERANIE: Pierwsze zdanie = konkretny fakt z audytu dotyczący {domain}, podany wprost jak obserwacja eksperta. Zero "Zauważyłem że..." — po prostu fakt.
   Dobry przykład: "Na {domain} {'nie ma certyfikatu SSL' if not audit.get('security_score',100) > 50 else 'brakuje kluczowych nagłówków bezpieczeństwa'} — to oznacza że część odwiedzających widzi ostrzeżenie w przeglądarce."
   Zły przykład: "Widziałem Twoją stronę i chciałem zaproponować współpracę..."

3. KONSEKWENCJA BIZNESOWA: Każdy problem techniczny przetłumacz na: utraconych klientów, gorsze pozycje Google, utratę zaufania. Żadnego żargonu bez wyjaśnienia co to znaczy dla biznesu.

4. KONKRETNOŚĆ: Powołaj się na DOKŁADNE dane z audytu (konkretny rok, konkretna technologia, konkretny wynik). Ogólniki lądują w koszu.

5. CTA — WARTOŚĆ DLA NICH: "Mam pełny raport audytu {domain} — mogę Ci go pokazać w 15 min na Zoom, bez żadnych zobowiązań." Oni dostają konkretną wartość (raport), Ty prosisz tylko o 15 minut.
   Link do rezerwacji (wpisz dosłownie): {BOOKING_LINK}

6. DŁUGOŚĆ: Maksymalnie 90 słów w treści. Piszą i czytają na telefonie.

7. STRUKTURA:
   Linia 1: Konkretny fakt z audytu (bez "Dzień dobry" — wchodzisz od razu w temat)
   Linia 2-3: Co to oznacza dla ich biznesu (utraceni klienci / Google / zaufanie)
   Linia 4: "Przeprowadziłem pełny audyt {domain} i mam dla Ciebie konkretne wyniki."
   Linia 5: CTA z linkiem {BOOKING_LINK} w osobnej linii
   Podpis: "{SENDER_NAME}" w osobnej linii, poniżej "{SENDER_TITLE}"

8. TEMAT: Maksymalnie 7 słów. Konkretny, bez wykrzykników i "szybka wiadomość".
   Dobry: "Znalazłem problem z SSL na {domain}"
   Zły: "Twoja strona traci klientów!!!"

9. STOPKA OPT-OUT (obowiązkowa, po pustej linii i "---"):
   Użyj dokładnie tego tekstu: "{opt_out}"

Odpowiedz WYŁĄCZNIE poprawnym JSON, nic poza nim:
{{"subject": "...", "body": "..."}}"""


def _build_followup_prompt(audit: dict, keyword: str, language: str, sender_offer: str,
                            followup_num: int, day: int, original_subject: str) -> str:
    domain = audit["domain"] or "strona"
    findings = _findings_text(audit["findings"], "pl", limit=2)
    top_issue = audit["findings"][0]["pl"] if audit["findings"] else "problemy techniczne strony"
    opt_out = OPT_OUT["pl"]

    strategies = {
        1: f"""Jesteś {SENDER_NAME}, {SENDER_TITLE}. Wysyłasz follow-up #1 (dzień 3) do właściciela {domain} (branża: {keyword}).

Temat poprzedniego emaila: "{original_subject}"

ZNALEZISKA Z AUDYTU:
{findings}

━━━ CEL TEGO EMAILA ━━━
Dajesz DARMOWĄ, konkretną wskazówkę — coś co mogą zrobić sami w 10 minut. Nie sprzedajesz. Pomagasz.

━━━ ZASADY ━━━
1. JĘZYK: Wyłącznie po polsku.
2. STRUKTURA (ścisła):
   Zdanie 1: "Wróciłem do audytu {domain} i pomyślałem, że mogę od razu dać Ci jedną rzecz do naprawienia samemu."
   Zdanie 2: JEDNA konkretna, darmowa wskazówka z audytu — coś technicznie prostego (np. zmiana nagłówka, dodanie meta tagu). Podaj jak dokładnie to zrobić.
   Zdanie 3: "Trudniejsze kwestie z audytu, jak [top issue], wymagają więcej — ale tę jedną możesz zrobić teraz."
   Zdanie 4: Miękkie CTA — "Jeśli chcesz zobaczyć pełny raport, 15 minut wystarczy:" + {BOOKING_LINK}
   Podpis: {SENDER_NAME}
3. DŁUGOŚĆ: Max 80 słów w treści.
4. TEMAT: Krótki, konkretny — np. "Jedna rzecz do naprawienia na {domain} już dziś"
5. STOPKA (obowiązkowa po "---"): "{opt_out}"

Odpowiedz WYŁĄCZNIE JSON:
{{"subject": "...", "body": "..."}}""",

        2: f"""Jesteś {SENDER_NAME}, {SENDER_TITLE}. Wysyłasz follow-up #2 (dzień 7) do właściciela {domain} (branża: {keyword}).

Temat poprzedniego emaila: "{original_subject}"

ZNALEZISKA Z AUDYTU:
{findings}

━━━ CEL TEGO EMAILA ━━━
Jedno bezpośrednie pytanie. Szanujesz ich czas — minimum słów.

━━━ ZASADY ━━━
1. JĘZYK: Wyłącznie po polsku.
2. STRUKTURA (ścisła, max 3 zdania):
   Zdanie 1: "Czy miałeś/-aś chwilę żeby zajrzeć do mojej poprzedniej wiadomości?"
   Zdanie 2: Jedno zdanie — najważniejsze znalezisko z audytu i jego KONKRETNA konsekwencja biznesowa dla {domain}.
   Zdanie 3: "Jeśli to niedobry moment, nie ma problemu — mogę wrócić za jakiś czas. Link do rezerwacji: " + {BOOKING_LINK}
   Podpis: {SENDER_NAME}
3. DŁUGOŚĆ: Max 50 słów. Celowo krótki — szanuje ich czas.
4. TEMAT: 4-5 słów, pytanie lub stwierdzenie — np. "Czy dotarła moja wiadomość?"
5. STOPKA (obowiązkowa po "---"): "{opt_out}"

Odpowiedz WYŁĄCZNIE JSON:
{{"subject": "...", "body": "..."}}""",

        3: f"""Jesteś {SENDER_NAME}, {SENDER_TITLE}. Wysyłasz follow-up #3 (dzień 14) do właściciela {domain} (branża: {keyword}).

Temat poprzedniego emaila: "{original_subject}"

ZNALEZISKA Z AUDYTU:
{findings}

━━━ CEL TEGO EMAILA ━━━
Krótka, realistyczna historia o podobnej firmie z branży {keyword}. Bez przesady, bez wielkich liczb — codzienny przypadek.

━━━ ZASADY ━━━
1. JĘZYK: Wyłącznie po polsku.
2. STRUKTURA (ścisła):
   Zdanie 1: "Ostatnio pracowałem z trenerem/firmą z podobnej branży co {keyword} — mieli dokładnie ten sam problem: {top_issue[:80]}."
   Zdanie 2: "Po naprawieniu go w ciągu 2 tygodni — [realistyczny efekt, np. 'przestali tracić klientów przez ostrzeżenie Chrome' / 'poprawili widoczność w Google Maps']."
   Zdanie 3: "Zostawiam link do raportu z {domain} — może i Tobie się przyda:" + {BOOKING_LINK}
   Podpis: {SENDER_NAME}
3. DŁUGOŚĆ: Max 70 słów.
4. EFEKT W HISTORII: Realistyczny i konkretny, nie "zwiększyli sprzedaż o 300%". Pisz jak człowiek, nie jak reklama.
5. TEMAT: Np. "Podobny przypadek z branży {keyword}"
6. STOPKA (obowiązkowa po "---"): "{opt_out}"

Odpowiedz WYŁĄCZNIE JSON:
{{"subject": "...", "body": "..."}}""",

        4: f"""Jesteś {SENDER_NAME}, {SENDER_TITLE}. Wysyłasz ostatni follow-up #4 (dzień 21) do właściciela {domain} (branża: {keyword}).

Temat poprzedniego emaila: "{original_subject}"

━━━ CEL TEGO EMAILA ━━━
Ostatnia wiadomość. Ciepła, bez presji. Zostawiasz drzwi otwarte — bez sprzedawania.

━━━ ZASADY ━━━
1. JĘZYK: Wyłącznie po polsku.
2. STRUKTURA (ścisła):
   Zdanie 1: "To moja ostatnia wiadomość w tej sprawie — nie chcę być uciążliwy/-a."
   Zdanie 2: "Zostawiam raport z audytu {domain} — może się przydać kiedyś, kiedy będzie dobry moment:" + {BOOKING_LINK}
   Zdanie 3: "Powodzenia z {domain} — jeśli kiedyś będziesz potrzebować pomocy z bezpieczeństwem strony, wiesz gdzie mnie szukać."
   Podpis: {SENDER_NAME}
3. DŁUGOŚĆ: Max 60 słów. Ciepły ton, ludzki, ostateczny.
4. TEMAT: Np. "Ostatnia wiadomość w sprawie {domain}"
5. STOPKA (obowiązkowa po "---"): "{opt_out}"

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
