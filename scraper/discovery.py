"""
Massive-scale website discovery.
Strategy for tens of thousands of sites:
  1. OSM Overpass API - free, returns ALL businesses in a country with website URLs
  2. Search engines (DDG + Bing) with ALL cities in the target country
  3. Polish directories: panoramafirm.pl, aleo.com, firmy.net
  4. US: Yellow Pages with top 100 cities
"""
import asyncio
import json
import random
import re
from urllib.parse import urlparse, parse_qs, unquote, urljoin
from typing import List, Dict, Optional

import httpx
from bs4 import BeautifulSoup

BLOCKED_DOMAINS = {
    "yelp.com", "yellowpages.com", "facebook.com", "instagram.com",
    "linkedin.com", "twitter.com", "x.com", "google.com", "google.pl",
    "google.co", "bbb.org", "angi.com", "thumbtack.com", "houzz.com",
    "angieslist.com", "wikipedia.org", "reddit.com", "amazon.com",
    "youtube.com", "tripadvisor.com", "foursquare.com", "manta.com",
    "mapquest.com", "bing.com", "duckduckgo.com", "yahoo.com",
    "whitepages.com", "superpages.com", "bizapedia.com",
    "chamberofcommerce.com", "findlaw.com", "avvo.com", "healthgrades.com",
    "vitals.com", "zocdoc.com", "bark.com", "trustpilot.com",
    "glassdoor.com", "indeed.com", "craigslist.org", "nextdoor.com",
    "panoramafirm.pl", "zumi.pl", "aleo.com", "firma.pl", "pkt.pl",
    "firmy.net", "targeo.pl", "emitent.pl", "olx.pl", "cylex.pl",
    "baza-firm.com.pl", "nfz.gov.pl", "webmd.com",
    "homeadvisor.com", "porch.com", "fixr.com",
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]

# OSM tag mappings
OSM_TAG_MAP = {
    "mechanik": [("shop", "car_repair"), ("shop", "vehicle")],
    "dentysta": [("amenity", "dentist")],
    "lekarz": [("amenity", "doctors"), ("healthcare", "doctor")],
    "fryzjer": [("shop", "hairdresser")],
    "restauracja": [("amenity", "restaurant")],
    "apteka": [("amenity", "pharmacy")],
    "prawnik": [("office", "lawyer")],
    "adwokat": [("office", "lawyer")],
    "hotel": [("tourism", "hotel")],
    "hydraulik": [("craft", "plumber")],
    "elektryk": [("craft", "electrician")],
    "weterynarz": [("amenity", "veterinary")],
    "optyk": [("shop", "optician")],
    "kwiaciarnia": [("shop", "florist")],
    "piekarnia": [("shop", "bakery")],
    "fizjoterapeuta": [("healthcare", "physiotherapist")],
    "dentist": [("amenity", "dentist")],
    "mechanic": [("shop", "car_repair")],
    "lawyer": [("office", "lawyer")],
    "doctor": [("amenity", "doctors")],
    "restaurant": [("amenity", "restaurant")],
    "pharmacy": [("amenity", "pharmacy")],
    "plumber": [("craft", "plumber")],
    "electrician": [("craft", "electrician")],
    "hairdresser": [("shop", "hairdresser")],
    "gym": [("leisure", "fitness_centre")],
    "bakery": [("shop", "bakery")],
    "optician": [("shop", "optician")],
    "vet": [("amenity", "veterinary")],
    "florist": [("shop", "florist")],
}

OSM_BBOXES = {
    "pl": (49.0, 14.1, 54.9, 24.2),
    "us": (24.5, -125.0, 49.5, -66.9),
    "eu": (34.0, -10.0, 71.0, 40.0),
}

POLISH_CITIES = [
    "Warszawa", "Krakow", "Lodz", "Wroclaw", "Poznan", "Gdansk", "Szczecin",
    "Bydgoszcz", "Lublin", "Katowice", "Bialystok", "Gdynia", "Czestochowa",
    "Radom", "Sosnowiec", "Torun", "Kielce", "Gliwice", "Zabrze", "Bytom",
    "Olsztyn", "Bielsko-Biala", "Rzeszow", "Ruda Slaska", "Rybnik", "Tychy",
    "Dabrowa Gornicza", "Opole", "Elblag", "Plock", "Walbrzych", "Zielona Gora",
    "Wloclawek", "Tarnow", "Chorzow", "Koszalin", "Kalisz", "Legnica",
    "Grudziadz", "Jaworzno", "Slupsk", "Jastrzebie-Zdoj", "Nowy Sacz",
    "Siedlce", "Mysłowice", "Konin", "Pila", "Piotrkow Trybunalski",
    "Inowroclaw", "Lubin", "Ostrow Wielkopolski", "Suwalki", "Gniezno",
    "Jelenia Gora", "Ostrowiec Swietokrzyski", "Stargard", "Siemianowice Slaskie",
    "Pabianice", "Lomza", "Przemysl", "Zamosc", "Tarnowskie Gory", "Zory",
    "Zawiercie", "Leszno", "Kedzierzyn-Kozle", "Tomaszow Mazowiecki", "Mielec",
    "Nowy Targ", "Ostroleka", "Stalowa Wola", "Starachowice", "Sanok",
    "Skierniewice", "Skarzysko-Kamienna", "Biala Podlaska", "Swidnica",
    "Legionowo", "Pruszkow", "Radomsko", "Raciborz", "Mikolow",
    "Rumia", "Tczew", "Oswiecim", "Zywiec", "Myszkow", "Bedzin",
    "Czechowice-Dziedzice", "Wodzislaw Slaski", "Elk", "Nowa Sol", "Bialogard",
    "Chelm", "Krosno", "Brzeg", "Nysa", "Srem", "Wagrowiec",
    "Glogow", "Zgorzelec", "Sandomierz", "Augustow", "Bigoraj",
    "Zambrow", "Hajnowka", "Lowicz", "Sierpc", "Plonsk", "Ciechanow",
    "Ostrow Mazowiecka", "Wegrow", "Minsk Mazowiecki", "Wolomin", "Marki",
    "Piaseczno", "Grojec", "Zyrardow", "Sochaczew", "Nowy Dwor Mazowiecki",
    "Otwock", "Zbki", "Kobylka", "Sulejowek", "Debica", "Jaroslaw",
    "Lubaczow", "Lezajsk", "Lancut", "Nisko", "Jaslo", "Gorlice",
    "Limanowa", "Zakopane", "Wadowice", "Olkusz", "Miechow", "Wieliczka",
    "Skawina", "Myslenice", "Bochnia", "Pszczyna", "Laziska Gorne",
    "Nowa Ruda", "Klodzko", "Zabkowice Slaskie", "Dzierzoniow", "Bielawa",
    "Boleslawiec", "Luban", "Lwowek Slaski", "Zlotoryja", "Chojnow",
    "Kolo", "Turek", "Klodawa", "Slupca", "Sroda Wielkopolska", "Gostyj",
    "Rawicz", "Krotoszyn", "Pleszew", "Jarocin", "Szamotuly", "Nowy Tomysl",
    "Wolsztyn", "Polkowice", "Bartoszyce", "Lidzbark Warminski", "Szczytno",
    "Pisz", "Mragowo", "Gizycko", "Wegorzewo", "Goldap", "Sokoltka",
    "Monki", "Grajewo", "Kolno", "Lapy", "Choroszc", "Wasilkow",
    "Mikotajki", "Ostroda", "Morag", "Ilawa", "Nowe Miasto Lubawskie",
    "Dzialdowo", "Mlawa", "Przasnysz", "Makow Mazowiecki", "Pultusk",
    "Wyszkow", "Radzymin", "Brodnica", "Rypin", "Lipno", "Ciechocinek",
    "Aleksandrow Kujawski", "Radziejow", "Znin", "Szubin",
    "Naklo nad Notecia", "Chodzies", "Czarnkow", "Wronki", "Swarzedz",
    "Kostrzyn", "Kornik", "Mosina", "Lubon", "Puszczykowo",
]

US_CITIES = [
    "New York NY", "Los Angeles CA", "Chicago IL", "Houston TX", "Phoenix AZ",
    "Philadelphia PA", "San Antonio TX", "San Diego CA", "Dallas TX", "Jacksonville FL",
    "Austin TX", "San Jose CA", "Fort Worth TX", "Columbus OH", "Charlotte NC",
    "Indianapolis IN", "San Francisco CA", "Seattle WA", "Denver CO", "Nashville TN",
    "Oklahoma City OK", "El Paso TX", "Washington DC", "Boston MA", "Las Vegas NV",
    "Memphis TN", "Louisville KY", "Portland OR", "Baltimore MD", "Milwaukee WI",
    "Albuquerque NM", "Tucson AZ", "Fresno CA", "Sacramento CA", "Mesa AZ",
    "Kansas City MO", "Atlanta GA", "Omaha NE", "Colorado Springs CO", "Raleigh NC",
    "Long Beach CA", "Virginia Beach VA", "Minneapolis MN", "Tampa FL", "New Orleans LA",
    "Honolulu HI", "Arlington TX", "Anaheim CA", "Aurora CO", "Santa Ana CA",
    "Corpus Christi TX", "Riverside CA", "St Louis MO", "Lexington KY", "Pittsburgh PA",
    "Anchorage AK", "Stockton CA", "Cincinnati OH", "St Paul MN", "Greensboro NC",
    "Toledo OH", "Newark NJ", "Plano TX", "Henderson NV", "Orlando FL",
    "Lincoln NE", "Jersey City NJ", "Chandler AZ", "St Petersburg FL", "Laredo TX",
    "Norfolk VA", "Madison WI", "Durham NC", "Lubbock TX", "Winston-Salem NC",
    "Garland TX", "Glendale AZ", "Hialeah FL", "Reno NV", "Baton Rouge LA",
    "Irvine CA", "Chesapeake VA", "Irving TX", "Scottsdale AZ", "Fremont CA",
    "Gilbert AZ", "San Bernardino CA", "Boise ID", "Birmingham AL",
    "Rochester NY", "Richmond VA", "Spokane WA", "Des Moines IA", "Montgomery AL",
    "Modesto CA", "Fayetteville NC", "Tacoma WA", "Shreveport LA", "Akron OH",
]


def get_domain(url: str) -> str:
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def is_blocked(url: str) -> bool:
    domain = get_domain(url)
    if not domain:
        return True
    for blocked in BLOCKED_DOMAINS:
        if domain == blocked or domain.endswith("." + blocked):
            return True
    return False


def normalize_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.startswith("http"):
        url = "https://" + url
    return url


def _add_urls(seen: Dict[str, str], new_urls: list, max_results: int) -> int:
    added = 0
    for url in new_urls:
        if len(seen) >= max_results:
            break
        try:
            url = normalize_url(str(url))
            if is_blocked(url):
                continue
            domain = get_domain(url)
            if not domain or domain in seen:
                continue
            seen[domain] = url
            added += 1
        except Exception:
            pass
    return added


def _detect_country(keyword: str) -> str:
    polish_roots = {
        "mechanik", "dentysta", "lekarz", "prawnik", "adwokat", "kancelari",
        "sklep", "serwis", "naprawa", "instalat", "hydraulik", "elektryk",
        "fryzjer", "kosmetyczk", "restauracj", "apteka", "piekarni",
        "cukierni", "kwiaciarni", "weterynarz", "optyk", "ubezpiecze",
        "fizjoterap", "gabinet",
    }
    kw_lower = keyword.lower()
    for word in polish_roots:
        if word in kw_lower:
            return "pl"
    return "us"


def _get_osm_tags(keyword: str) -> list:
    kw_lower = keyword.lower().strip()
    for key, tags in OSM_TAG_MAP.items():
        if key in kw_lower:
            return tags
    return []


async def _query_osm_overpass(client, tag_key, tag_value, bbox):
    urls = []
    min_lat, min_lon, max_lat, max_lon = bbox
    bbox_str = f"{min_lat},{min_lon},{max_lat},{max_lon}"
    query = f'[out:json][timeout:90];(node["{tag_key}"="{tag_value}"]({bbox_str});way["{tag_key}"="{tag_value}"]({bbox_str});relation["{tag_key}"="{tag_value}"]({bbox_str}););out body;'
    try:
        resp = await client.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query},
            headers={"User-Agent": "NicheWebsiteScraper/1.0 (educational tool)"},
            timeout=httpx.Timeout(120.0),
        )
        if resp.status_code != 200:
            return urls
        data = resp.json()
        elements = data.get("elements", [])
        print(f"[OSM] {len(elements)} elements for {tag_key}={tag_value}")
        for el in elements:
            tags = el.get("tags", {})
            website = tags.get("website") or tags.get("url") or tags.get("contact:website")
            if website:
                if not website.startswith("http"):
                    website = "https://" + website
                urls.append(website)
    except asyncio.TimeoutError:
        print(f"[OSM] Timeout for {tag_key}={tag_value}")
    except Exception as e:
        print(f"[OSM] Error: {e}")
    return urls


async def _search_duckduckgo(client, query, page=0, country="us"):
    urls = []
    try:
        kl = "pl-pl" if country == "pl" else "wt-wt"
        data = {"q": query, "kl": kl}
        if page > 0:
            data["s"] = str(page * 30)
            data["dc"] = str(page * 30 + 1)
            data["nextParams"] = ""
            data["v"] = "l"
            data["o"] = "json"
        resp = await client.post(
            "https://html.duckduckgo.com/html/",
            data=data,
            headers={
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8" if country == "pl" else "en-US,en;q=0.9",
                "Origin": "https://html.duckduckgo.com",
                "Referer": "https://html.duckduckgo.com/",
            },
            timeout=12.0
        )
        if resp.status_code != 200:
            return urls
        soup = BeautifulSoup(resp.text, "lxml")
        for a in soup.find_all("a", class_="result__a"):
            href = a.get("href", "")
            if "uddg=" in href:
                try:
                    if href.startswith("//"):
                        href = "https:" + href
                    real_url = parse_qs(urlparse(href).query).get("uddg", [None])[0]
                    if real_url:
                        urls.append(unquote(real_url))
                except Exception:
                    pass
            elif href.startswith("http"):
                urls.append(href)
    except Exception:
        pass
    return urls


async def _search_bing(client, query, page=0, country="us"):
    urls = []
    try:
        mkt = "pl-PL" if country == "pl" else "en-US"
        params = {"q": query, "count": "50", "mkt": mkt}
        if page > 0:
            params["first"] = str(page * 10 + 1)
        resp = await client.get(
            "https://www.bing.com/search",
            params=params,
            headers={
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "pl-PL,pl;q=0.9" if country == "pl" else "en-US,en;q=0.9",
            },
            timeout=12.0
        )
        if resp.status_code != 200:
            return urls
        soup = BeautifulSoup(resp.text, "lxml")
        for li in soup.find_all("li", class_="b_algo"):
            h2 = li.find("h2")
            if h2:
                a = h2.find("a")
                if a and a.get("href", "").startswith("http"):
                    urls.append(a["href"])
    except Exception:
        pass
    return urls


async def _scrape_panoramafirm(client, keyword, city, page=1):
    urls = []
    try:
        def slugify(s):
            s = s.lower()
            for a, b in [('ą','a'),('ć','c'),('ę','e'),('ł','l'),('ń','n'),('ó','o'),('ś','s'),('ź','z'),('ż','z')]:
                s = s.replace(a, b)
            return re.sub(r'[^\w-]', '-', s).strip('-')

        kw_slug = slugify(keyword)
        city_slug = slugify(city)
        base = f"https://panoramafirm.pl/{kw_slug}/{city_slug}"
        url = f"{base},firmy,{page}.html" if page > 1 else f"{base},firmy.html"

        resp = await client.get(
            url,
            headers={
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "pl-PL,pl;q=0.9",
            },
            timeout=12.0
        )
        if resp.status_code != 200:
            return urls
        soup = BeautifulSoup(resp.text, "lxml")
        for el in soup.find_all(attrs={"data-href": re.compile(r"^https?://")}):
            href = el.get("data-href", "")
            if "panoramafirm.pl" not in href:
                urls.append(href)
        for a in soup.find_all("a", href=re.compile(r"^https?://")):
            href = a.get("href", "")
            if "panoramafirm.pl" not in href:
                text = (a.get_text(strip=True) + " " + a.get("title","") + " " + " ".join(a.get("class",[]))).lower()
                if any(w in text for w in ["www", "website", "strona", "witryna", ".pl", ".com"]):
                    urls.append(href)
    except Exception:
        pass
    return urls


async def _scrape_aleo(client, keyword, city="", page=1):
    urls = []
    try:
        url = f"https://aleo.com/pl/firmy?q={keyword.replace(' ', '+')}&city={city.replace(' ', '+')}&page={page}"
        resp = await client.get(
            url,
            headers={
                "User-Agent": random.choice(USER_AGENTS),
                "Accept-Language": "pl-PL,pl;q=0.9",
            },
            timeout=12.0
        )
        if resp.status_code != 200:
            return urls
        soup = BeautifulSoup(resp.text, "lxml")
        for a in soup.find_all("a", href=re.compile(r"^https?://")):
            href = a.get("href", "")
            if "aleo.com" not in href:
                urls.append(href)
    except Exception:
        pass
    return urls


async def _scrape_yellowpages(client, keyword, city, page=1):
    urls = []
    try:
        params = {"search_terms": keyword, "geo_location_terms": city}
        if page > 1:
            params["page"] = page
        resp = await client.get(
            "https://www.yellowpages.com/search",
            params=params,
            headers={
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=15.0
        )
        if resp.status_code != 200:
            return urls
        soup = BeautifulSoup(resp.text, "lxml")
        for a in soup.find_all("a", class_=re.compile(r"track-visit-website|business-website", re.I)):
            href = a.get("href", "")
            if href.startswith("http") and "yellowpages.com" not in href:
                urls.append(href)
    except Exception:
        pass
    return urls


async def discover_websites(
    keyword: str,
    max_results: int = 2000,
    country: str = "auto",
    log_fn=None,  # async callable(msg: str) for live progress
) -> List[str]:
    if country == "auto":
        country = _detect_country(keyword)

    async def log(msg: str):
        print(f"[Discovery] {msg}")
        if log_fn:
            try:
                await log_fn(msg)
            except Exception:
                pass

    await log(f"Start: keyword='{keyword}', kraj={country}, cel={max_results} stron")
    seen: Dict[str, str] = {}

    cities = POLISH_CITIES if country == "pl" else US_CITIES

    async with httpx.AsyncClient(
        follow_redirects=True,
        verify=False,
        timeout=httpx.Timeout(20.0, connect=8.0),
        limits=httpx.Limits(max_connections=30, max_keepalive_connections=20),
    ) as client:

        # SOURCE 1: OSM Overpass API
        osm_tags = _get_osm_tags(keyword)
        bbox = OSM_BBOXES.get(country, OSM_BBOXES["us"])
        if osm_tags and bbox:
            await log(f"[1/3] OSM Overpass API — zapytanie o {len(osm_tags)} tag(ów) na mapie ({country.upper()})... (może trwać 30-90s)")

            async def osm_heartbeat():
                for i in range(1, 12):
                    await asyncio.sleep(10)
                    await log(f"[1/3] OSM: pobieram dane... {i*10}s")

            hb_task = asyncio.create_task(osm_heartbeat())
            osm_results = await asyncio.gather(
                *[_query_osm_overpass(client, k, v, bbox) for k, v in osm_tags],
                return_exceptions=True
            )
            hb_task.cancel()
            before = len(seen)
            for r in osm_results:
                if isinstance(r, list):
                    _add_urls(seen, r, max_results)
            osm_found = len(seen) - before
            await log(f"[1/3] OSM: znaleziono {osm_found} nowych stron ({len(seen)} łącznie)")
        else:
            await log(f"[1/3] OSM: brak mapowania tagów dla '{keyword}', pomijam")

        if len(seen) >= max_results:
            await log(f"Osiągnięto limit {max_results} stron — kończę discovery")
            return list(seen.values())

        # SOURCE 2: Search engines with ALL cities
        all_queries = [keyword]
        for city in cities:
            all_queries.append(f"{keyword} {city}")
        if country == "pl":
            for city in cities[:50]:
                all_queries.append(f"gabinet {keyword} {city}")

        total_search_tasks_count = len(all_queries) * 5  # ~5 requests per query
        await log(f"[2/3] Wyszukiwarki — {len(all_queries)} zapytań × 3 strony (DDG + Bing)...")
        search_sem = asyncio.Semaphore(6)
        pages_per_query = 3

        async def do_search(engine, query, page):
            async with search_sem:
                await asyncio.sleep(random.uniform(0.2, 0.5))
                if engine == "ddg":
                    return await _search_duckduckgo(client, query, page, country)
                else:
                    return await _search_bing(client, query, page, country)

        search_tasks = []
        for query in all_queries:
            for page in range(pages_per_query):
                search_tasks.append(("ddg", query, page))
                if page < 2:
                    search_tasks.append(("bing", query, page))

        batch_size = 40
        total_batches = (len(search_tasks) + batch_size - 1) // batch_size
        for i in range(0, len(search_tasks), batch_size):
            if len(seen) >= max_results:
                break
            batch = search_tasks[i:i + batch_size]
            results = await asyncio.gather(
                *[do_search(e, q, p) for e, q, p in batch],
                return_exceptions=True
            )
            for r in results:
                if isinstance(r, list):
                    _add_urls(seen, r, max_results)
            batch_num = i // batch_size + 1
            if batch_num % 5 == 0 or batch_num == total_batches:
                pct = round(batch_num / total_batches * 100)
                await log(f"[2/3] Wyszukiwarki {pct}% — partia {batch_num}/{total_batches} — {len(seen)} stron znalezionych")

        await log(f"[2/3] Wyszukiwarki gotowe: {len(seen)} unikalnych domen")

        # SOURCE 3: Business Directories
        if len(seen) < max_results:
            dir_sem = asyncio.Semaphore(5)

            if country == "pl":
                await log(f"[3/3] Katalogi biznesowe PL — Panorama Firm + Aleo ({len(POLISH_CITIES)} miast × 3 strony)...")
                dir_tasks = []
                for city in POLISH_CITIES:
                    for page in range(1, 4):
                        dir_tasks.append(("panorama", city, page))
                        dir_tasks.append(("aleo", city, page))

                async def do_dir(source, city, page):
                    async with dir_sem:
                        await asyncio.sleep(random.uniform(0.3, 0.7))
                        if source == "panorama":
                            return await _scrape_panoramafirm(client, keyword, city, page)
                        else:
                            return await _scrape_aleo(client, keyword, city, page)

                total_dir_batches = (len(dir_tasks) + 49) // 50
                for i in range(0, len(dir_tasks), 50):
                    if len(seen) >= max_results:
                        break
                    batch = dir_tasks[i:i + 50]
                    results = await asyncio.gather(
                        *[do_dir(s, c, p) for s, c, p in batch],
                        return_exceptions=True
                    )
                    for r in results:
                        if isinstance(r, list):
                            _add_urls(seen, r, max_results)
                    batch_num = i // 50 + 1
                    if batch_num % 10 == 0 or batch_num == total_dir_batches:
                        pct = round(batch_num / total_dir_batches * 100)
                        await log(f"[3/3] Katalogi PL {pct}% — {len(seen)} stron znalezionych")

                await log(f"[3/3] Katalogi PL gotowe: {len(seen)} unikalnych domen")

            elif country == "us":
                await log(f"[3/3] Yellow Pages USA — {len(US_CITIES)} miast × 3 strony...")
                yp_sem = asyncio.Semaphore(4)
                yp_tasks = [(city, page) for city in US_CITIES for page in range(1, 4)]

                async def do_yp(city, page):
                    async with yp_sem:
                        await asyncio.sleep(random.uniform(0.5, 1.0))
                        return await _scrape_yellowpages(client, keyword, city, page)

                total_yp_batches = (len(yp_tasks) + 39) // 40
                for i in range(0, len(yp_tasks), 40):
                    if len(seen) >= max_results:
                        break
                    batch = yp_tasks[i:i + 40]
                    results = await asyncio.gather(
                        *[do_yp(c, p) for c, p in batch],
                        return_exceptions=True
                    )
                    for r in results:
                        if isinstance(r, list):
                            _add_urls(seen, r, max_results)
                    batch_num = i // 40 + 1
                    if batch_num % 5 == 0 or batch_num == total_yp_batches:
                        pct = round(batch_num / total_yp_batches * 100)
                        await log(f"[3/3] Yellow Pages {pct}% — {len(seen)} stron znalezionych")

                await log(f"[3/3] Yellow Pages gotowe: {len(seen)} unikalnych domen")

    await log(f"GOTOWE: {len(seen)} unikalnych domen odkryto")
    return list(seen.values())
