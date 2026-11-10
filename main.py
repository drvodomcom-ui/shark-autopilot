"""SHARK MINDSET autopilot za Instagram @20shark26.

Komande:
  python main.py generate   -> napravi nove objave (tekst + slike) ako ih ima malo u rezervi
  python main.py schedule   -> zakaži objave u Buffer-u koliko ima slobodnih mesta u redu
  python main.py preview    -> napravi 5 probnih slika bez AI-ja (test dizajna)
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont

# ---------------- Podešavanja ----------------
TZ = ZoneInfo("Europe/Belgrade")
QUEUE_FILE = "queue.json"        # sve napravljene objave i njihov status
HISTORY_FILE = "history.json"    # teme koje su već korišćene
POSTS_DIR = "posts"
FONTS_DIR = "fonts"

BUFFER_QUEUE_LIMIT = int(os.getenv("BUFFER_QUEUE_LIMIT", "10"))  # Buffer free: 10 u redu
BACKLOG_TARGET = int(os.getenv("BACKLOG_TARGET", "10"))          # koliko gotovih objava držimo u rezervi
MIN_LEAD_MINUTES = 20                                            # najranije zakazivanje od sada
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

def env_clean(name):
    """Ključ bez razmaka, novih redova i navodnika (česta greška pri lepljenju)."""
    return "".join(os.getenv(name, "").split()).strip("\"'")


GEMINI_KEY = env_clean("GEMINI_API_KEY")
GROQ_KEY = env_clean("GROQ_API_KEY")
BUFFER_KEY = env_clean("BUFFER_API_KEY")
BUFFER_CHANNEL_ID = env_clean("BUFFER_CHANNEL_ID")
REPO = env_clean("GITHUB_REPOSITORY")
IG_TOKEN = env_clean("IG_ACCESS_TOKEN")
IG_USER_ID = env_clean("IG_USER_ID")
PUBLISH_WINDOW_HOURS = 3   # objava za termin ide ako je pokretanje unutar 3h posle termina

# Termini (beogradsko vreme) i tip objave za svaki termin
SLOTS = [
    {"time": "08:00", "key": "motivacija", "label": "JUTARNJI MINDSET",
     "brief": "Jutarnja motivacija: jedna snažna rečenica kao naslov, u opisu kratko objašnjenje i kako je primeniti danas."},
    {"time": "11:30", "key": "novac", "label": "LEKCIJA O NOVCU",
     "brief": "Lekcija o novcu/finansijama: konkretan, primenljiv savet (štednja, ulaganje, dug, budžet, prihod) sa jednostavnim primerom u dinarima ili evrima."},
    {"time": "13:00", "key": "investiranje", "label": "INVESTIRANJE",
     "brief": "Investiranje za početnike: jedan jasan princip (štednja, kamata na kamatu, diversifikacija, rizik, nekretnine, zlato, akcije/ETF) objašnjen jednostavno, bez saveta za kupovinu konkretne akcije."},
    {"time": "14:30", "key": "prodaja", "label": "PRODAJA",
     "brief": "Prodaja i pregovaranje: jedna konkretna tehnika i kako je primeniti danas u razgovoru sa kupcem ili šefom."},
    {"time": "18:00", "key": "psihologija", "label": "PSIHOLOGIJA BIZNISA",
     "brief": "Psihologija biznisa: zanimljiv psihološki efekat ili česta greška ljudi sa novcem/poslom, u stilu 'da li si znao'."},
    {"time": "21:00", "key": "izazov", "label": "VEČERNJI IZAZOV",
     "brief": "Večernje pitanje ili izazov za publiku koje podstiče komentare; naslov je pitanje."},
]
NEWS_TIMES = ["07:00", "09:00", "10:00", "12:00", "15:30", "16:30", "17:00", "19:00", "20:00", "22:00"]
REGULAR_SLOTS = list(SLOTS)
SLOTS = sorted(SLOTS + [{"time": t, "key": "vesti", "label": "EKONOMSKE VESTI", "news": True,
                         "brief": "Jedna važna ekonomska vest."} for t in NEWS_TIMES], key=lambda x: x["time"])
ACCENTS = {
    "motivacija": (255, 196, 0),
    "novac": (0, 200, 120),
    "prodaja": (0, 150, 255),
    "psihologija": (190, 90, 255),
    "izazov": (255, 80, 60),
    "vesti": (230, 230, 235),
    "investiranje": (255, 140, 0),
}


# ---------------- Pomoćne funkcije ----------------
def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def now_local():
    return datetime.now(TZ)


# ---------------- AI tekst ----------------
def build_prompt(avoid_topics):
    slots_txt = "\n".join(f'{i+1}. key="{s["key"]}" — {s["brief"]}' for i, s in enumerate(REGULAR_SLOTS))
    avoid = "; ".join(avoid_topics[-80:]) or "nema"
    return f"""Ti si vrhunski kreator sadržaja za Instagram profil @20shark26 „SHARK MINDSET".
Teme: finansije, prodaja, psihologija biznisa, mindset, disciplina, novac. Publika: šira publika u Srbiji, ne samo preduzetnici.
Jezik: srpski, LATINICA, sa pravilnim slovima č ć ž š đ. Ton: samouveren, kratak, udarni, „ajkula" mentalitet, bez kliševa, bez lažnih obećanja o brzom bogaćenju.

Napravi tačno {len(REGULAR_SLOTS)} objava, po jednu za svaki tip:
{slots_txt}

Teme koje su VEĆ korišćene i koje NE smeš ponoviti ni sličnim rečima: {avoid}

Pravila:
- "title": naslov koji ide NA SLIKU, najviše 10 reči, bez hashtagova i bez emotikona.
- "caption": opis 80–150 reči. Prvi red je jaka udica. Zatim vrednost (konkretno, praktično). Pa poziv na akciju (sačuvaj / podeli / komentariši). Na kraju prazan red i 8–12 hashtagova (mešavina srpskih i engleskih, npr. #novac #biznis #mindset #prodaja #finansije #motivacija #uspeh).
- "topic": kratak opis teme (do 8 reči) za proveru ponavljanja.
- Bez izmišljenih statistika, procenata i lažnih citata. Ako citiraš, samo opšte poznate i proverljive citate sa tačnim autorom; inače bez citata.

Vrati ISKLJUČIVO validan JSON ovog oblika, bez ikakvog drugog teksta:
{{"posts": [{{"key": "motivacija", "title": "...", "caption": "...", "topic": "..."}}, ...]}}"""


GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
_gemini_cache = {}


def _gemini_auth(mode):
    """Dva načina slanja ključa: header (novi AQ. ključevi) i ?key= (stari AIza ključevi)."""
    return ({"x-goog-api-key": GEMINI_KEY}, {}) if mode == "header" else ({}, {"key": GEMINI_KEY})


def _version_score(name):
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", name)]
    return nums[0] if nums else 0.0


def gemini_models():
    """Sam pronalazi Gemini modele koji trenutno postoje za ovaj ključ (Google ih često gasi)."""
    if "list" in _gemini_cache:
        return _gemini_cache["list"]
    found, auth_ok = [], "header"
    for mode in ("header", "param"):
        try:
            headers, params = _gemini_auth(mode)
            names, token = [], None
            for _ in range(10):
                p = dict(params, pageSize=200, **({"pageToken": token} if token else {}))
                r = requests.get(f"{GEMINI_BASE}/models", headers=headers, params=p, timeout=60)
                if r.status_code != 200:
                    print(f"Gemini lista modela ({mode}): HTTP {r.status_code} {r.text[:150]}")
                    names = []
                    break
                data = r.json()
                for m in data.get("models", []):
                    if "generateContent" in m.get("supportedGenerationMethods", []):
                        names.append(m["name"].split("/", 1)[-1])
                token = data.get("nextPageToken")
                if not token:
                    break
            if names:
                found, auth_ok = names, mode
                break
        except Exception as e:  # noqa: BLE001
            print(f"Gemini lista modela ({mode}) greška: {e}")
    bad = ("image", "tts", "audio", "embedding", "live", "vision", "aqa", "gemma", "robotics", "computer")
    text_models = [n for n in found if not any(b in n for b in bad)]
    flash = [n for n in text_models if "flash" in n and "lite" not in n]
    lite = [n for n in text_models if "flash" in n and "lite" in n]
    flash.sort(key=lambda n: (_version_score(n), "preview" not in n and "exp" not in n), reverse=True)
    lite.sort(key=_version_score, reverse=True)
    ordered = flash + lite + [n for n in text_models if n not in flash and n not in lite and "pro" not in n]
    if not ordered:  # ako lista ne radi, probaj poznate aliase
        ordered = ["gemini-flash-latest", "gemini-3-flash-preview", "gemini-flash-lite-latest"]
    print(f"Gemini modeli za pokušaj: {ordered[:5]} (auth: {auth_ok})")
    _gemini_cache["list"] = (ordered[:5], auth_ok)
    return _gemini_cache["list"]


def call_gemini(prompt):
    if not GEMINI_KEY:
        return None
    models, auth = gemini_models()
    for model in models:
        for mode in dict.fromkeys([auth, "param" if auth == "header" else "header"]):
            try:
                headers, params = _gemini_auth(mode)
                r = requests.post(
                    f"{GEMINI_BASE}/models/{model}:generateContent",
                    headers=headers, params=params,
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.9},
                    },
                    timeout=180,
                )
                if r.status_code in (401, 403):
                    print(f"Gemini {model} ({mode}): HTTP {r.status_code}")
                    continue
                if r.status_code != 200:
                    print(f"Gemini {model}: HTTP {r.status_code} {r.text[:200]}")
                    break
                parts = r.json()["candidates"][0]["content"]["parts"]
                text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
                if text.strip():
                    print(f"Tekst napisao Gemini: {model}")
                    return text
            except Exception as e:  # noqa: BLE001
                print(f"Gemini {model} greška: {e}")
                break
    return None


def groq_models():
    preferred = ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "moonshotai/kimi-k2-instruct",
                 "qwen/qwen3-32b", "openai/gpt-oss-20b", "llama-3.1-8b-instant"]
    try:
        r = requests.get("https://api.groq.com/openai/v1/models",
                         headers={"Authorization": f"Bearer {GROQ_KEY}"}, timeout=60)
        if r.status_code == 200:
            avail = [m["id"] for m in r.json().get("data", []) if m.get("active", True)]
            skip = ("whisper", "tts", "guard", "playai", "orpheus", "distil", "prompt")
            avail = [a for a in avail if not any(s in a.lower() for s in skip)]
            ordered = [p for p in preferred if p in avail] + [a for a in avail if a not in preferred]
            if ordered:
                print(f"Groq modeli za pokušaj: {ordered[:5]}")
                return ordered[:5]
        print(f"Groq lista modela: HTTP {r.status_code} {r.text[:150]}")
    except Exception as e:  # noqa: BLE001
        print(f"Groq lista modela greška: {e}")
    return preferred


def call_groq(prompt):
    if not GROQ_KEY:
        return None
    for model in groq_models():
        for use_json_mode in (True, False):
            try:
                body = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.8}
                if use_json_mode:
                    body["response_format"] = {"type": "json_object"}
                r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                                  headers={"Authorization": f"Bearer {GROQ_KEY}"}, json=body, timeout=180)
                if r.status_code == 400 and use_json_mode:
                    continue  # probaj isti model bez JSON režima
                if r.status_code != 200:
                    print(f"Groq {model}: HTTP {r.status_code} {r.text[:200]}")
                    break
                text = r.json()["choices"][0]["message"]["content"] or ""
                if text.strip():
                    print(f"Tekst napisao Groq: {model}")
                    return text
            except Exception as e:  # noqa: BLE001
                print(f"Groq {model} greška: {e}")
                break
    return None


def parse_posts(raw):
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S).strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start and not raw.lstrip().startswith("["):
        raw = raw[start:end + 1]
    data = json.loads(raw)
    posts = data["posts"] if isinstance(data, dict) else data
    by_key = {}
    for p in posts:
        k = str(p.get("key", "")).strip().lower()
        title = str(p.get("title", "")).strip()
        caption = str(p.get("caption", "")).strip()
        topic = str(p.get("topic", "")).strip()
        if k in ACCENTS and k != "vesti" and title and len(caption) >= 200 and topic:
            by_key[k] = {"key": k, "title": title, "caption": caption, "topic": topic}
    if len(by_key) != len(REGULAR_SLOTS):
        raise ValueError(f"AI je vratio {len(by_key)}/5 ispravnih objava")
    return [by_key[s["key"]] for s in REGULAR_SLOTS]


def generate_texts(avoid_topics):
    prompt = build_prompt(avoid_topics)
    last_err = None
    for attempt in range(3):
        for provider in (call_gemini, call_groq):
            raw = provider(prompt)
            if not raw:
                continue
            try:
                return parse_posts(raw)
            except Exception as e:  # noqa: BLE001
                last_err = e
                print(f"{provider.__name__}: neispravan odgovor ({e}), pokušavam ponovo")
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Nijedan AI nije vratio ispravne objave: {last_err}")


# ---------------- Slike ----------------
def font(name, size):
    return ImageFont.truetype(os.path.join(FONTS_DIR, name), size)


def wrap_by_width(draw, text, fnt, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = f"{cur} {w}".strip()
        if draw.textlength(test, font=fnt) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def draw_fin(draw, cx, base_y, size, color):
    # stilizovano peraje ajkule
    pts = [(cx - size, base_y), (cx + int(size * 0.15), base_y - int(size * 1.6)),
           (cx + int(size * 0.55), base_y - int(size * 0.4)), (cx + size, base_y)]
    draw.polygon(pts, fill=color)
    draw.line([(cx - size * 2, base_y), (cx + size * 2, base_y)], fill=color, width=6)


def create_image(post, slot, out_path):
    W, H = 1080, 1350
    accent = ACCENTS[post["key"]]
    img = Image.new("RGB", (W, H), (8, 10, 18))
    d = ImageDraw.Draw(img)

    # suptilan gradijent odozgo
    for y in range(H):
        t = y / H
        c = (int(8 + 14 * (1 - t)), int(10 + 16 * (1 - t)), int(18 + 30 * (1 - t)))
        d.line([(0, y), (W, y)], fill=c)

    # okvir i gornja traka
    d.rectangle([40, 40, W - 40, H - 40], outline=accent, width=4)
    lbl_font = font("Montserrat-Bold.ttf", 34)
    label = slot["label"]
    lw = d.textlength(label, font=lbl_font)
    d.rectangle([80, 90, 80 + lw + 40, 150], fill=accent)
    d.text((100, 98), label, font=lbl_font, fill=(8, 10, 18))

    # peraje
    draw_fin(d, W // 2, 330, 70, accent)

    # naslov — automatski smanjuje font dok ne stane
    title = post["title"].upper()
    max_w, box_top, box_bottom = W - 200, 400, 1080
    for size in range(110, 47, -4):
        tf = font("BebasNeue-Regular.ttf", size)
        lines = wrap_by_width(d, title, tf, max_w)
        line_h = int(size * 1.08)
        if len(lines) * line_h <= box_bottom - box_top:
            break
    total_h = len(lines) * line_h
    y = box_top + (box_bottom - box_top - total_h) // 2
    for line in lines:
        w = d.textlength(line, font=tf)
        d.text(((W - w) / 2, y), line, font=tf, fill=(245, 245, 245))
        y += line_h

    # podnožje
    d.line([(100, 1150), (W - 100, 1150)], fill=accent, width=3)
    d.text((100, 1180), "SHARK MINDSET", font=font("BebasNeue-Regular.ttf", 60), fill=accent)
    handle_font = font("Montserrat-Regular.ttf", 36)
    hw = d.textlength("@20shark26", font=handle_font)
    d.text((W - 100 - hw, 1195), "@20shark26", font=handle_font, fill=(200, 200, 210))

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    img.save(out_path, "JPEG", quality=92, optimize=True)


# ---------------- Ekonomske vesti (Google News RSS) ----------------
NEWS_FEEDS = [
    ("Srbija", "https://news.google.com/rss/search?q=ekonomija+OR+privreda+OR+inflacija+OR+dinar+OR+NBS+Srbija+when:1d&hl=sr&gl=RS&ceid=RS:sr"),
    ("Balkan", "https://news.google.com/rss/search?q=economy+Western+Balkans+OR+Croatia+OR+Bosnia+OR+Montenegro+OR+%22North+Macedonia%22+when:1d&hl=en-US&gl=US&ceid=US:en"),
    ("Svet", "https://news.google.com/rss/search?q=global+economy+OR+markets+OR+%22central+bank%22+OR+inflation+when:1d&hl=en-US&gl=US&ceid=US:en"),
]


def fetch_news(max_per_feed=8, max_age_hours=36):
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    items, cutoff = [], datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    for region, url in NEWS_FEEDS:
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 shark-autopilot"})
            root = ET.fromstring(r.content)
            n = 0
            for it in root.iter("item"):
                title = (it.findtext("title") or "").strip()
                src = (it.findtext("source") or "").strip()
                try:
                    pub = parsedate_to_datetime(it.findtext("pubDate"))
                except Exception:  # noqa: BLE001
                    continue
                if not title or pub < cutoff:
                    continue
                items.append({"region": region, "title": title, "source": src, "date": pub.strftime("%d.%m.%Y")})
                n += 1
                if n >= max_per_feed:
                    break
        except Exception as e:  # noqa: BLE001
            print(f"Vesti {region} greška: {e}")
    print(f"Preuzeto naslova vesti: {len(items)}")
    return items


def build_news_prompt(items, already):
    lines = "\n".join(f"{i+1}. [{x['region']}] {x['title']} (izvor: {x['source'] or 'nepoznat'}, {x['date']})"
                      for i, x in enumerate(items))
    done = "\n".join(f"- {t}" for t in already[-40:]) or "- (još ništa)"
    return f"""Ti si urednik ekonomskih vesti za Instagram profil @20shark26 „SHARK MINDSET".
Ispod su naslovi vesti iz poslednjih 24–36 sati. Koristi ISKLJUČIVO informacije iz izabranog naslova — ništa ne izmišljaj, ne dodaj brojeve, imena ni detalje kojih nema u naslovu. Ne kopiraj naslov doslovno, prepričaj ga svojim rečima.

NASLOVI:
{lines}

VESTI KOJE SU VEĆ OBJAVLJENE (ne ponavljaj ni istu temu drugim rečima):
{done}

Izaberi JEDNU najvažniju vest koja utiče na ekonomiju ili novčanik ljudi i koja NIJE već objavljena. Prednost: Srbija, pa Balkan, pa svet — ali biraj po važnosti.
- "title": naslov na slici, najviše 9 reči, bez emotikona.
- "caption": srpski, LATINICA. Prvi red: oznaka (🇷🇸 Srbija / 🌍 Balkan / 🌐 Svet) i kratak udarni rezime. Zatim 2–3 rečenice šta se desilo, pa red „Šta to znači za tebe:" i 1–2 rečenice. Pa poziv da zaprate profil za vesti tokom dana, red „Izvor: <naziv izvora>", prazan red i 6–10 hashtagova (#ekonomija #vesti #srbija #finansije ...).
- Ton: neutralan i činjeničan, bez političkih stavova i bez saveta za kupovinu konkretnih akcija.
- "topic": kratak opis vesti (do 10 reči) za proveru ponavljanja.
- "pick": redni broj izabranog naslova.

Vrati ISKLJUČIVO validan JSON: {{"pick": 1, "title": "...", "caption": "...", "topic": "..."}}"""


def generate_news_post(already):
    items = fetch_news()
    if not items:
        raise RuntimeError("Nema svežih vesti")
    prompt = build_news_prompt(items, already)
    for attempt in range(3):
        for provider in (call_gemini, call_groq):
            raw = provider(prompt)
            if not raw:
                continue
            try:
                raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S)
                raw = raw[raw.find("{"): raw.rfind("}") + 1]
                d = json.loads(raw)
                if d.get("title") and len(d.get("caption", "")) >= 150 and d.get("topic"):
                    return {"key": "vesti", "title": d["title"].strip(), "caption": d["caption"].strip(),
                            "topic": d["topic"].strip()}
            except Exception as e:  # noqa: BLE001
                print(f"Vesti: neispravan odgovor ({e})")
        time.sleep(5 * (attempt + 1))
    raise RuntimeError("AI nije napravio objavu sa vestima")


def maybe_generate_news(queue, history):
    """Vest se pravi tek u svom terminu, da bude sveža."""
    found = current_slot()
    if not found or not found[0].get("news"):
        return False
    slot, slot_time = found
    tag = f"{slot_time.date().isoformat()} {slot['time']}"
    if any(q.get("for_slot") == tag for q in queue):
        print("Vest za ovaj termin je već napravljena.")
        return False
    two_days_ago = (slot_time.date() - timedelta(days=2)).isoformat()
    already = [h["topic"] for h in history if h.get("key") == "vesti" and h.get("date", "") >= two_days_ago]
    post = generate_news_post(already)
    stamp = now_local().strftime("%Y%m%d-%H%M%S")
    rel = f"{POSTS_DIR}/{stamp}/vesti.jpg"
    create_image(post, slot, rel)
    queue.append({"id": f"{stamp}-vesti", "key": "vesti", "slot": slot["time"], "for_slot": tag,
                  "title": post["title"], "caption": post["caption"], "topic": post["topic"], "image": rel,
                  "status": "pending", "created": now_local().isoformat()})
    history.append({"date": slot_time.date().isoformat(), "topic": post["topic"], "key": "vesti"})
    print(f"Napravljena vest: {post['title']}")
    return True


# ---------------- Komanda: generate ----------------
def cmd_generate():
    if os.path.exists(RUN_SLOT_FILE):
        os.remove(RUN_SLOT_FILE)
    current_slot()  # zapamti termin ovog pokretanja
    queue = load_json(QUEUE_FILE, [])
    history = load_json(HISTORY_FILE, [])
    try:
        if maybe_generate_news(queue, history):
            save_json(QUEUE_FILE, queue)
            save_json(HISTORY_FILE, history)
    except Exception as e:  # noqa: BLE001
        print(f"Vesti nisu napravljene ({e}) — u ovom terminu ide obična objava.")
    pending = [q for q in queue if q["status"] == "pending" and q["key"] != "vesti"]
    if len(pending) >= BACKLOG_TARGET:
        print(f"U rezervi već ima {len(pending)} objava — ne pravim nove.")
        return

    topics = [h["topic"] for h in history]
    posts = generate_texts(topics)
    stamp = now_local().strftime("%Y%m%d-%H%M%S")
    for i, (post, slot) in enumerate(zip(posts, REGULAR_SLOTS)):
        rel = f"{POSTS_DIR}/{stamp}/{i+1}-{post['key']}.jpg"
        create_image(post, slot, rel)
        queue.append({
            "id": f"{stamp}-{i+1}",
            "key": post["key"],
            "slot": slot["time"],
            "title": post["title"],
            "caption": post["caption"],
            "topic": post["topic"],
            "image": rel,
            "status": "pending",
            "created": now_local().isoformat(),
        })
        history.append({"date": now_local().strftime("%Y-%m-%d"), "topic": post["topic"], "key": post["key"]})
        print(f"Napravljeno: [{slot['time']}] {post['title']}")
    save_json(QUEUE_FILE, queue)
    save_json(HISTORY_FILE, history)


# ---------------- Buffer ----------------
def buffer_gql(query, variables=None):
    for attempt in range(3):
        try:
            r = requests.post(
                "https://api.buffer.com",
                headers={"Authorization": f"Bearer {BUFFER_KEY}", "Content-Type": "application/json"},
                json={"query": query, "variables": variables or {}},
                timeout=60,
            )
            data = r.json()
            if r.status_code == 200 and not data.get("errors"):
                return data["data"]
            print(f"Buffer HTTP {r.status_code}: {json.dumps(data)[:400]}")
        except Exception as e:  # noqa: BLE001
            print(f"Buffer greška: {e}")
        time.sleep(5 * (attempt + 1))
    raise RuntimeError("Buffer API ne odgovara ispravno")


def gql_str(s):
    """Bezbedan GraphQL string literal (JSON escape je validan i u GraphQL-u)."""
    return json.dumps(s, ensure_ascii=True)


def buffer_scheduled_count():
    orgs = buffer_gql("query { account { organizations { id } } }")["account"]["organizations"]
    total = 0
    for org in orgs:
        after = ""
        while True:
            q = ("query { posts(first: 50%s, input: {organizationId: %s, filter: {status: [scheduled], "
                 "channelIds: [%s]}}) { edges { node { id } } pageInfo { hasNextPage endCursor } } }"
                 % (after, gql_str(org["id"]), gql_str(BUFFER_CHANNEL_ID)))
            res = buffer_gql(q)["posts"]
            total += len(res["edges"])
            if not res["pageInfo"]["hasNextPage"]:
                break
            after = ", after: " + gql_str(res["pageInfo"]["endCursor"])
    return total


def buffer_create_post(text, image_url, alt, due_at_utc):
    q = """mutation {
      createPost(input: {
        text: %s
        channelId: %s
        schedulingType: automatic
        mode: customScheduled
        dueAt: %s
        assets: [{ image: { url: %s, metadata: { altText: %s } } }]
        metadata: { instagram: { type: post, shouldShareToFeed: true } }
      }) {
        ... on PostActionSuccess { post { id dueAt } }
        ... on MutationError { message }
      }
    }""" % (gql_str(text), gql_str(BUFFER_CHANNEL_ID), gql_str(due_at_utc), gql_str(image_url), gql_str(alt[:250]))
    res = buffer_gql(q)["createPost"]
    if "post" not in res:
        raise RuntimeError(f"Buffer odbio objavu: {res.get('message')}")
    return res["post"]


def git_head_sha():
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def wait_public(url):
    for _ in range(12):
        try:
            r = requests.head(url, timeout=20, allow_redirects=True)
            if r.status_code == 200:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(10)
    return False


def next_due(slot_time, taken):
    """Prvi budući termin za dati sat koji još nije zauzet."""
    h, m = map(int, slot_time.split(":"))
    earliest = now_local() + timedelta(minutes=MIN_LEAD_MINUTES)
    day = earliest.date()
    for _ in range(30):
        cand = datetime(day.year, day.month, day.day, h, m, tzinfo=TZ)
        if cand >= earliest and cand.isoformat() not in taken:
            return cand
        day += timedelta(days=1)
    raise RuntimeError("Nema slobodnog termina u narednih 30 dana")


# ---------------- Instagram (direktno, zvanični API) ----------------
IG_HOSTS = ["https://graph.facebook.com/v23.0", "https://graph.instagram.com/v23.0"]


def ig_call(method, path, **params):
    last = None
    for host in IG_HOSTS:
        try:
            r = requests.request(method, f"{host}/{path}", params=dict(params, access_token=IG_TOKEN), timeout=90)
            data = r.json()
            if r.status_code == 200 and "error" not in data:
                return data
            last = data
            err = data.get("error", {})
            # ako je pogrešan host za ovaj tip tokena, probaj drugi
            if err.get("code") in (190, 100, 803) or r.status_code in (400, 401, 404):
                continue
            break
        except Exception as e:  # noqa: BLE001
            last = {"error": str(e)}
    raise RuntimeError(f"Instagram API greška: {json.dumps(last)[:400]}")


_ig_ok_cache = {}


def ig_ok():
    """Da li Instagram API trenutno prihvata naš token (npr. Meta zna da blokira nalog)."""
    if not (IG_TOKEN and IG_USER_ID):
        return False
    if "ok" not in _ig_ok_cache:
        try:
            ig_call("GET", f"{IG_USER_ID}/content_publishing_limit")
            _ig_ok_cache["ok"] = True
        except Exception as e:  # noqa: BLE001
            print(f"Instagram API trenutno ne radi ({str(e)[:200]}) — koristim Buffer.")
            _ig_ok_cache["ok"] = False
    return _ig_ok_cache["ok"]


def ig_publish(image_url, caption):
    container = ig_call("POST", f"{IG_USER_ID}/media", image_url=image_url, caption=caption)["id"]
    for _ in range(30):  # čekaj da Instagram obradi sliku
        st = ig_call("GET", container, fields="status_code").get("status_code")
        if st == "FINISHED":
            break
        if st in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram nije prihvatio sliku (status {st})")
        time.sleep(5)
    return ig_call("POST", f"{IG_USER_ID}/media_publish", creation_id=container)["id"]


RUN_SLOT_FILE = ".run_slot.json"


def current_slot():
    """Termin ovog pokretanja. Određuje se jednom (na početku) i pamti u .run_slot.json,
    da pravljenje objava (koje traje par minuta) ne pomeri pokretanje u sledeći termin."""
    saved = load_json(RUN_SLOT_FILE, None)
    if saved:
        slot = next((s for s in SLOTS if s["time"] == saved["time"]), None)
        if slot:
            return slot, datetime.fromisoformat(saved["at"])
    found = _detect_slot()
    if found:
        save_json(RUN_SLOT_FILE, {"time": found[0]["time"], "at": found[1].isoformat()})
    return found


def _detect_slot():
    """Termin čije je vreme prošlo pre manje od PUBLISH_WINDOW_HOURS sati."""
    now = now_local()
    best = None
    for s in SLOTS:
        h, m = map(int, s["time"].split(":"))
        t = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if t <= now < t + timedelta(hours=PUBLISH_WINDOW_HOURS):
            best = (s, t)
    return best


def cmd_publish():
    use_buffer = not ig_ok()
    if use_buffer and not (BUFFER_KEY and BUFFER_CHANNEL_ID):
        print("Ni Instagram API ni Buffer nisu dostupni.")
        return
    found = current_slot()
    if not found:
        print("Sada nije vreme ni za jedan termin.")
        return
    slot, slot_time = found
    queue = load_json(QUEUE_FILE, [])
    today = slot_time.date().isoformat()
    if not use_buffer:
        # Instagram API radi: objave koje su ranije predate Buffer-u vrati botu
        changed = False
        for q in queue:
            if q["status"] == "scheduled":
                q["old_buffer_id"] = q.pop("buffer_id", None)
                q.pop("due_local", None)
                q.pop("published_slot", None)
                q["status"] = "expired" if q["key"] == "vesti" else "pending"
                changed = True
        if changed:
            save_json(QUEUE_FILE, queue)
            print("Objave iz Buffer-a su vraćene botu.")
    if any(q.get("published_slot") == f"{today} {slot['time']}" for q in queue):
        print(f"Termin {slot['time']} je već objavljen danas.")
        return
    if any(q["status"] == "scheduled" and q.get("due_local", "").startswith(slot_time.strftime("%Y-%m-%dT%H:%M"))
           for q in queue):
        print(f"Termin {slot['time']} danas već pokriva Buffer — preskačem.")
        return
    candidates = [q for q in queue if q["status"] == "pending" and q["key"] != "vesti"]
    if slot.get("news"):
        same_type = [q for q in queue if q["status"] == "pending" and q.get("for_slot") == f"{today} {slot['time']}"]
        if not same_type:
            print("Vest za ovaj termin nije napravljena — preskačem (bez zamene običnom objavom).")
            return
    else:
        same_type = [q for q in candidates if q["slot"] == slot["time"]]
    pick = (same_type or candidates or [None])[0]
    if not pick:
        print("Nema gotovih objava u rezervi.")
        return
    url = f"https://raw.githubusercontent.com/{REPO}/{git_head_sha()}/{pick['image']}"
    if not wait_public(url):
        raise RuntimeError(f"Slika nije javno dostupna: {url}")
    if DRY_RUN:
        print(f"[DRY_RUN] Objavio bih sada ({slot['time']}): {pick['title']}")
        return
    if use_buffer:
        if not slot.get("news"):
            return  # obične objave Buffer dobija unapred u koraku "schedule"
        if buffer_scheduled_count() >= BUFFER_QUEUE_LIMIT:
            print("Buffer red je pun — vest preskačem.")
            return
        due = now_local() + timedelta(minutes=5)
        post = buffer_create_post(pick["caption"], url, pick["title"],
                                  due.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"))
        pick.update(status="scheduled", buffer_id=post["id"], due_local=due.isoformat(),
                    published_slot=f"{today} {slot['time']}")
        save_json(QUEUE_FILE, queue)
        print(f"Vest poslata preko Buffer-a: {pick['title']}")
        return
    try:
        media_id = ig_publish(url, pick["caption"])
    except Exception as e:  # noqa: BLE001
        pick["fail_count"] = pick.get("fail_count", 0) + 1
        pick["last_error"] = str(e)[:300]
        save_json(QUEUE_FILE, queue)
        print(f"Objava nije uspela (pokušaj {pick['fail_count']}/3): {e}")
        if pick["fail_count"] >= 3:
            pick["status"] = "failed"
            save_json(QUEUE_FILE, queue)
            raise
        return  # sledeće pokretanje (za 15 min) pokušava ponovo
    pick["status"] = "published"
    pick["ig_media_id"] = media_id
    pick["published_slot"] = f"{today} {slot['time']}"
    pick["published_at"] = now_local().isoformat()
    save_json(QUEUE_FILE, queue)
    print(f"Objavljeno na Instagram ({slot['time']}): {pick['title']}")


# ---------------- Komanda: schedule ----------------
def cmd_schedule():
    if ig_ok():
        print("Instagram API radi — Buffer se ne koristi.")
        return
    queue = load_json(QUEUE_FILE, [])
    pending = [q for q in queue if q["status"] == "pending" and q["key"] != "vesti"]
    if not pending:
        print("Nema objava za zakazivanje.")
        return
    for name, val in [("BUFFER_API_KEY", BUFFER_KEY), ("BUFFER_CHANNEL_ID", BUFFER_CHANNEL_ID), ("GITHUB_REPOSITORY", REPO)]:
        if not val:
            raise SystemExit(f"Nedostaje {name}")

    in_queue = buffer_scheduled_count()
    free = max(0, BUFFER_QUEUE_LIMIT - in_queue)
    print(f"U Buffer redu: {in_queue}, slobodno mesta: {free}, u rezervi: {len(pending)}")
    if free == 0:
        print("Buffer red je pun — pokušaću pri sledećem pokretanju.")
        return

    sha = git_head_sha()
    taken = {q["due_local"] for q in queue if q.get("due_local")}
    # najstarije prvo, ali po redosledu termina u danu
    order = {s["time"]: i for i, s in enumerate(SLOTS)}
    pending.sort(key=lambda q: (q["created"][:10], order[q["slot"]]))

    done = 0
    for item in pending:
        if done >= free:
            break
        url = f"https://raw.githubusercontent.com/{REPO}/{sha}/{item['image']}"
        if not wait_public(url):
            raise RuntimeError(f"Slika nije javno dostupna: {url} (repo mora biti javan)")
        due = next_due(item["slot"], taken)
        due_utc = due.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        if DRY_RUN:
            print(f"[DRY_RUN] {due:%d.%m. %H:%M} — {item['title']}")
        else:
            post = buffer_create_post(item["caption"], url, item["title"], due_utc)
            item["buffer_id"] = post["id"]
            item["status"] = "scheduled"
            print(f"Zakazano {due:%d.%m. %H:%M} — {item['title']}")
        item["due_local"] = due.isoformat()
        taken.add(due.isoformat())
        done += 1
        save_json(QUEUE_FILE, queue)
    print(f"Ukupno zakazano u ovom pokretanju: {done}")


# ---------------- Komanda: preview ----------------
def cmd_preview():
    demo = {
        "motivacija": "Disciplina pobeđuje motivaciju svakog jutra",
        "novac": "Prvo plati sebe, pa tek onda sve ostale račune",
        "prodaja": "Ko postavlja pitanja, taj vodi razgovor",
        "psihologija": "Zašto ljudi više mrze gubitak nego što vole dobitak?",
        "izazov": "Šta bi uradio danas da znaš da ne možeš da pogrešiš?",
        "investiranje": "Kamata na kamatu je osmo svetsko čudo",
        "vesti": "NBS zadržala referentnu kamatnu stopu",
    }
    for i, slot in enumerate(REGULAR_SLOTS + [SLOTS[0]]):
        post = {"key": slot["key"], "title": demo[slot["key"]]}
        create_image(post, slot, f"preview/{i+1}-{slot['key']}.jpg")
    print("Probne slike su u folderu preview/")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "generate"
    {"generate": cmd_generate, "schedule": cmd_schedule, "publish": cmd_publish, "preview": cmd_preview}[cmd]()
