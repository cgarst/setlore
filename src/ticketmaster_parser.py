import re
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

MONTH_MAP = {
    'jan': 1, 'january': 1,
    'feb': 2, 'february': 2,
    'mar': 3, 'march': 3,
    'apr': 4, 'april': 4,
    'may': 5,
    'jun': 6, 'june': 6,
    'jul': 7, 'july': 7,
    'aug': 8, 'august': 8,
    'sep': 9, 'sept': 9, 'september': 9,
    'oct': 10, 'october': 10,
    'nov': 11, 'november': 11,
    'dec': 12, 'december': 12,
}

IGNORE_LINE_PATTERNS = [
    r'^\d{4}$',  # Standalone 4-digit years e.g. 2026, 2025, 2024
    r'^skip\s+to\s+main\s+content',
    r'.*selected,\s*change\s*country.*',
    r'^(?:hotels|sell|gift\s*cards|help|vip|search)$',
    r'^paypal\s+preferred\s+payments\s+partner',
    r'^ticketmaster\s+(?:home\s+page|logo)',
    r'^(?:my\s*account|my\s*profile|my\s*settings|my\s*listings|my\s*tickets)$',
    r'^(?:home|upcoming\s*events?|past\s*events?|order\s*history|sign\s*out|need\s*help\??)$',
    r'^welcome\s+back.*',
    r'^loaded\s+\d+\s+past\s+orders',
    r'^(?:view\s*(?:order\s*)?details|see\s*tickets?|receipt|view\s*receipt|add\s*to\s*calendar)$',
    r'^(?:past\s*event|event\s*details|completed|cancelled|canceled)$',
    r'^(?:standard\s*ticket|verified\s*resale|general\s*admission|vip\s*package|lawn)$',
    r'^(?:sec(?:tion)?\s*\w+|row\s*\w+|seat\s*\w+)$',
    r'^(?:total|subtotal|fees|taxes|\$[\d\.,]+)$',
    r'^(?:let\'?s\s*connect|download\s*our\s*apps|helpful\s*links|our\s*network|about\s*us|friends\s*&\s*partners|our\s*policies)$',
    r'.*\(opens\s+in\s+new\s+tab\).*',
    r'^by\s+continuing\s+past\s+this\s+page.*',
    r'^(?:help/faq|contact\s*us|do\s*not\s*sell.*|get\s*started\s*on\s*ticketmaster)$',
    r'^(?:live\s*nation|house\s*of\s*blues|front\s*gate\s*tickets|ticketweb|universe|nfl|nba|nhl)$',
    r'^(?:ticketmaster\s*blog|ticketing\s*truths|ad\s*choices|careers|ticket\s*your\s*event|innovation)$',
    r'^(?:allianz|aws|affiliates|privacy\s*policy|cookie\s*policy|manage\s*my\s*cookies.*)$',
    r'^©\s*\d{4}.*ticketmaster.*',
    r'^(?:https?://\S+)$',
]

def is_ignorable_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    for pat in IGNORE_LINE_PATTERNS:
        if re.match(pat, s, re.IGNORECASE):
            return True
    return False

def parse_tm_date(text: str) -> Optional[datetime]:
    if not text or not text.strip():
        return None
    s = text.strip()
    # Strip day of week names (e.g. Thu, Thursday, Sat •)
    s = re.sub(r'^(?:mon|tue(?:s)?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?\.?,?\s*[•\-|,]?\s*', '', s, flags=re.IGNORECASE)
    # Strip time part (e.g. • 7:30 PM, 8:00pm, at 7:00 PM, 19:30)
    s = re.sub(r'(?:[•\-|,]|\bat\b)\s*\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm)?.*$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s+\d{1,2}:\d{2}\s*(?:am|pm)?.*$', '', s, flags=re.IGNORECASE)
    s = s.strip()

    # Standard formats
    for fmt in (
        "%b %d, %Y", "%B %d, %Y",
        "%b %d %Y", "%B %d %Y",
        "%b. %d, %Y", "%b. %d %Y",
        "%m/%d/%Y", "%m/%d/%y",
        "%Y-%m-%d", "%m-%d-%Y", "%d-%m-%Y"
    ):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.year < 1970:
                dt = dt.replace(year=dt.year + 100)
            return dt
        except ValueError:
            pass

    # Regex search for Month DD, YYYY anywhere in string
    m = re.search(r'([a-zA-Z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})', s)
    if m:
        month_str = m.group(1).lower()
        if month_str in MONTH_MAP:
            try:
                month = MONTH_MAP[month_str]
                day = int(m.group(2))
                year = int(m.group(3))
                return datetime(year, month, day)
            except Exception:
                pass

    # Regex search for MM/DD/YYYY
    m2 = re.search(r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})', s)
    if m2:
        try:
            month = int(m2.group(1))
            day = int(m2.group(2))
            yr = int(m2.group(3))
            if yr < 100:
                yr += 2000 if yr < 70 else 1900
            return datetime(yr, month, day)
        except Exception:
            pass

    return None

def extract_venue_and_location(raw_venue_line: str) -> Tuple[str, str, str]:
    """
    Returns (venue_name, city, state)
    Examples:
    - "Capital One Arena - Washington, DC" -> ("Capital One Arena", "Washington", "DC")
    - "The Anthem, Washington, DC" -> ("The Anthem", "Washington", "DC")
    - "The Fillmore Silver Spring" -> ("The Fillmore Silver Spring", "", "")
    """
    s = raw_venue_line.strip()
    s = re.sub(r'Order\s*#.*$', '', s, flags=re.IGNORECASE).strip()

    match = re.search(r'^(.*?)\s*(?:[-–—]|\bat\b)\s*(.*?),\s*([A-Z]{2}(?:\s+US|\s+USA)?|[A-Za-z\s]+)$', s)
    if match:
        v_name = match.group(1).strip()
        city = match.group(2).strip()
        state = match.group(3).strip()
        return v_name, city, state

    match_comma = re.search(r'^(.*?),\s*([^,]+),\s*([A-Z]{2}|[A-Za-z\s]+)$', s)
    if match_comma:
        v_name = match_comma.group(1).strip()
        city = match_comma.group(2).strip()
        state = match_comma.group(3).strip()
        return v_name, city, state

    match_single_comma = re.search(r'^(.*?),\s*([A-Z]{2})$', s)
    if match_single_comma:
        v_name = match_single_comma.group(1).strip()
        return v_name, "", match_single_comma.group(2).strip()

    return s, "", ""

def clean_event_title(event_title: str) -> Tuple[str, str]:
    """
    Extracts (artist_name, tour_name_or_notes)
    Examples:
    - "Iron Maiden: Run For Your Lives World Tour 2026" -> ("Iron Maiden", "Run For Your Lives World Tour 2026")
    - "BEAR McCREARY \"THEMES & VARIATIONS\" TOUR 2025" -> ("BEAR McCREARY", "\"THEMES & VARIATIONS\" TOUR 2025")
    - "Devin Townsend's PowerNerd Tour! w/ Tesseract" -> ("Devin Townsend", "PowerNerd Tour! w/ Tesseract")
    - "Heritage Hunter Tour featuring: Opeth and Mastodon with special guest" -> ("Opeth and Mastodon", "Heritage Hunter Tour with special guest")
    - "Saints & Sinners Tour feat. Between The Buried And Me" -> ("Between The Buried And Me", "Saints & Sinners Tour")
    """
    s = event_title.strip()
    s = re.sub(r'^(?:event|show|artist|concert):\s*', '', s, flags=re.IGNORECASE).strip()

    # Pattern: Tour Name feat./featuring: Artist
    feat_match = re.search(r'^(.*?)\s+(?:feat\.?|featuring:?)\s+(.*?)(?:\s+with\s+(.*))?$', s, re.IGNORECASE)
    if feat_match:
        tour_part = feat_match.group(1).strip()
        artist_part = feat_match.group(2).strip()
        guest_part = feat_match.group(3).strip() if feat_match.group(3) else ""
        notes = f"{tour_part} with {guest_part}" if guest_part else tour_part
        return artist_part, notes

    # Pattern: Artist's Tour Name (e.g. Devin Townsend's PowerNerd Tour! w/ Tesseract)
    possessive_match = re.search(r"^([A-Z][a-zA-Z\s]+)'s\s+(.*)$", s)
    if possessive_match:
        artist = possessive_match.group(1).strip()
        notes = possessive_match.group(2).strip()
        return artist, notes

    # Pattern: Artist "TOUR NAME"
    quotes_match = re.search(r'^(.*?)\s+("[^"]+.*"|\'[^\']+.*\')(.*)$', s)
    if quotes_match:
        artist = quotes_match.group(1).strip()
        notes = f"{quotes_match.group(2).strip()} {quotes_match.group(3).strip()}".strip()
        return artist, notes

    # Split on colon if present (e.g. "Dream Theater: 40th Anniversary Tour")
    if ':' in s:
        parts = s.split(':', 1)
        artist = parts[0].strip()
        notes = parts[1].strip()
        return artist, notes

    # Split on "with special guest" or "w/"
    w_match = re.search(r'^(.*?)\s+(?:with\s+(?:special\s+)?guests?|w/)\s+(.*)$', s, re.IGNORECASE)
    if w_match:
        artist = w_match.group(1).strip()
        notes = f"with {w_match.group(2).strip()}"
        return artist, notes

    # Split on " - " if tour name follows
    if ' - ' in s:
        parts = s.split(' - ', 1)
        artist = parts[0].strip()
        notes = parts[1].strip()
        return artist, notes

    return s, ""

def parse_ticketmaster_text(raw_text: str) -> List[Dict[str, Any]]:
    """
    Parses pasted text from Ticketmaster past events page into a structured list of events.
    Robustly handles full-page copy-pastes containing page headers, footers, navigation links,
    Order # delimiters, dates, venues, and artist titles.
    """
    if not raw_text:
        return []

    raw_lines = [line.strip() for line in raw_text.splitlines()]
    lines = []
    for l in raw_lines:
        if not l or is_ignorable_line(l):
            continue
        lines.append(l)

    # Strategy 1: Group by Order # delimiters (standard Ticketmaster past events format)
    has_orders = any(re.search(r'Order\s*#\s*[0-9a-zA-Z\-/]+', l, re.IGNORECASE) for l in lines)
    cards = []

    if has_orders:
        cur_card = []
        for l in lines:
            cur_card.append(l)
            if re.search(r'Order\s*#\s*[0-9a-zA-Z\-/]+', l, re.IGNORECASE):
                cards.append(cur_card)
                cur_card = []
        if cur_card:
            cards.append(cur_card)
    else:
        # Fallback: Group by date occurrences
        cur_card = []
        for l in lines:
            is_date = parse_tm_date(l) is not None
            if is_date and cur_card:
                dates_in_cur = [idx for idx, x in enumerate(cur_card) if parse_tm_date(x) is not None]
                if dates_in_cur and (len(cur_card) - 1 > dates_in_cur[-1]):
                    cards.append(cur_card)
                    cur_card = [l]
                else:
                    cur_card.append(l)
            else:
                cur_card.append(l)
        if cur_card:
            cards.append(cur_card)

    events = []
    for card in cards:
        order_num = ""
        filtered = []
        for l in card:
            m_ord = re.search(r'Order\s*#\s*([0-9a-zA-Z\-/]+)', l, re.IGNORECASE)
            if m_ord:
                order_num = m_ord.group(1)
            else:
                filtered.append(l)

        if not filtered:
            continue

        # Find date line(s)
        date_indices = []
        first_dt = None
        for idx, l in enumerate(filtered):
            # Check for date range like 'Friday, September 8 – Saturday, September 9, 2017'
            first_date_part = re.split(r'[–—\-]\s*(?:mon|tue|wed|thu|fri|sat|sun|[a-zA-Z]+day)', l, flags=re.IGNORECASE)[0]
            p_dt = parse_tm_date(first_date_part) or parse_tm_date(l)
            if p_dt:
                date_indices.append(idx)
                if not first_dt:
                    first_dt = p_dt

        if not first_dt or not date_indices:
            continue

        first_date_idx = date_indices[0]
        last_date_idx = date_indices[-1]

        if first_date_idx > 0:
            # Title precedes the date (take the closest non-date line right before the date)
            title_lines = filtered[:first_date_idx]
            # Strip any residual noise lines from beginning
            clean_title_lines = [tl for tl in title_lines if not is_ignorable_line(tl)]
            # In case multiple lines precede, take the line immediately above date
            title_str = clean_title_lines[-1] if clean_title_lines else "Unknown Event"
            venue_lines = filtered[last_date_idx + 1:]
        else:
            # Date is at the start of the card
            non_date_lines = [nl for nl in filtered[last_date_idx + 1:] if not is_ignorable_line(nl)]
            if len(non_date_lines) == 1:
                title_str = non_date_lines[0]
                venue_lines = []
            elif len(non_date_lines) >= 2:
                title_str = non_date_lines[0]
                venue_lines = non_date_lines[1:]
            else:
                title_str = "Unknown Event"
                venue_lines = []

        # Filter venue lines for ignorable noise
        clean_venue_lines = [vl for vl in venue_lines if not is_ignorable_line(vl)]
        venue_str = clean_venue_lines[0] if clean_venue_lines else "Unknown Venue"

        art_name, tour_notes = clean_event_title(title_str)
        v_name, city, state = extract_venue_and_location(venue_str)

        events.append({
            "date": first_dt.strftime("%Y-%m-%d"),
            "display_date": first_dt.strftime("%m-%d-%Y"),
            "raw_date": first_dt.strftime("%m/%d/%Y"),
            "year": first_dt.year,
            "raw_event": title_str,
            "artist": art_name,
            "primary_artist": art_name,
            "tour_notes": tour_notes,
            "order_number": order_num,
            "venue": v_name or "Unknown Venue",
            "city": city,
            "state": state,
            "country": "United States"
        })

    return events
