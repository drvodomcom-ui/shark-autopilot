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

GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
GROQ_KEY = os.getenv("GROQ_API_KEY", "")
BUFFER_KEY = os.getenv("BUFFER_API_KEY", "")
BUFFER_CHANNEL_ID = os.getenv("BUFFER_CHANNEL_ID", "")
REPO = os.getenv("GITHUB_REPOSITORY", "")

# Termini (beogradsko vreme) i tip objave za svaki termin
SLOTS = [
    {"time": "08:00", "key": "motivacija", "label": "JUTARNJI MINDSET",
     "brief": "Jutarnja motivacija: jedna snažna rečenica kao naslov, u opisu kratko objašnjenje i kako je primeniti danas."},
    {"time": "11:30", "key": "novac", "label": "LEKCIJA O NOVCU",
     "brief": "Lekcija o novcu/finansijama: konkretan, primenljiv savet (štednja, ulaganje, dug, budžet, prihod) sa jednostavnim primerom u dinarima ili evrima."},
    {"time": "14:30", "key": "prodaja", "label": "PRODAJA",
     "brief": "Prodaja i pregovaranje: jedna konkretna tehnika i kako je primeniti danas u razgovoru sa kupcem ili šefom."},
    {"time": "18:00", "key": "psihologija", "label": "PSIHOLOGIJA BIZNISA",
     "brief": "Psihologija biznisa: zanimljiv psihološki efekat ili česta greška ljudi sa novcem/poslom, u stilu 'da li si znao'."},
    {"time": "21:00", "key": "izazov", "label": "VEČERNJI IZAZOV",
     "brief": "Večernje pitanje ili izazov za publiku koje podstiče komentare; naslov je pitanje."},
]
ACCENTS = {
    "motivacija": (255, 196, 0),
    "novac": (0, 200, 120),
    "prodaja": (0, 150, 255),
    "psihologija": (190, 90, 255),
    "izazov": (255, 80, 60),
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
    slots_txt = "\n".join(f'{i+1}. key="{s["key"]}" — {s["brief"]}' for i, s in enumerate(SLOTS))
    avoid = "; ".join(avoid_topics[-80:]) or "nema"
    return f"""Ti si vrhunski kreator sadržaja za Instagram profil @20shark26 „SHARK MINDSET".
Teme: finansije, prodaja, psihologija biznisa, mindset, disciplina, novac. Publika: šira publika u Srbiji, ne samo preduzetnici.
Jezik: srpski, LATINICA, sa pravilnim slovima č ć ž š đ. Ton: samouveren, kratak, udarni, „ajkula" mentalitet, bez kliševa, bez lažnih obećanja o brzom bogaćenju.

Napravi tačno 5 objava, po jednu za svaki tip:
{slots_txt}

Teme koje su VEĆ korišćene i koje NE smeš ponoviti ni sličnim rečima: {avoid}

Pravila:
- "title": naslov koji ide NA SLIKU, najviše 10 reči, bez hashtagova i bez emotikona.
- "caption": opis 80–150 reči. Prvi red je jaka udica. Zatim vrednost (konkretno, praktično). Pa poziv na akciju (sačuvaj / podeli / komentariši). Na kraju prazan red i 8–12 hashtagova (mešavina srpskih i engleskih, npr. #novac #biznis #mindset #prodaja #finansije #motivacija #uspeh).
- "topic": kratak opis teme (do 8 reči) za proveru ponavljanja.
- Bez izmišljenih statistika, procenata i lažnih citata. Ako citiraš, samo opšte poznate i proverljive citate sa tačnim autorom; inače bez citata.

Vrati ISKLJUČIVO validan JSON ovog oblika, bez ikakvog drugog teksta:
{{"posts": [{{"key": "motivacija", "title": "...", "caption": "...", "topic": "..."}}, ...]}}"""


def call_gemini(prompt):
    if not GEMINI_KEY:
        return None
    for model in ["gemini-flash-latest", "gemini-2.5-flash", "gemini-2.0-flash"]:
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key": GEMINI_KEY},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"responseMimeType": "application/json", "temperature": 0.9},
                },
                timeout=120,
            )
            if r.status_code != 200:
                print(f"Gemini {model}: HTTP {r.status_code} {r.text[:200]}")
                continue
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:  # noqa: BLE001
            print(f"Gemini {model} greška: {e}")
    return None


def call_groq(prompt):
    if not GROQ_KEY:
        return None
    for model in ["llama-3.3-70b-versatile", "openai/gpt-oss-120b"]:
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_KEY}"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.9,
                },
                timeout=120,
            )
            if r.status_code != 200:
                print(f"Groq {model}: HTTP {r.status_code} {r.text[:200]}")
                continue
            return r.json()["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001
            print(f"Groq {model} greška: {e}")
    return None


def parse_posts(raw):
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    data = json.loads(raw)
    posts = data["posts"] if isinstance(data, dict) else data
    by_key = {}
    for p in posts:
        k = str(p.get("key", "")).strip().lower()
        title = str(p.get("title", "")).strip()
        caption = str(p.get("caption", "")).strip()
        topic = str(p.get("topic", "")).strip()
        if k in ACCENTS and title and len(caption) >= 200 and topic:
            by_key[k] = {"key": k, "title": title, "caption": caption, "topic": topic}
    if len(by_key) != len(SLOTS):
        raise ValueError(f"AI je vratio {len(by_key)}/5 ispravnih objava")
    return [by_key[s["key"]] for s in SLOTS]


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


# ---------------- Komanda: generate ----------------
def cmd_generate():
    queue = load_json(QUEUE_FILE, [])
    history = load_json(HISTORY_FILE, [])
    pending = [q for q in queue if q["status"] == "pending"]
    if len(pending) >= BACKLOG_TARGET:
        print(f"U rezervi već ima {len(pending)} objava — ne pravim nove.")
        return

    topics = [h["topic"] for h in history]
    posts = generate_texts(topics)
    stamp = now_local().strftime("%Y%m%d-%H%M%S")
    for i, (post, slot) in enumerate(zip(posts, SLOTS)):
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


# ---------------- Komanda: schedule ----------------
def cmd_schedule():
    queue = load_json(QUEUE_FILE, [])
    pending = [q for q in queue if q["status"] == "pending"]
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
    }
    for i, slot in enumerate(SLOTS):
        post = {"key": slot["key"], "title": demo[slot["key"]]}
        create_image(post, slot, f"preview/{i+1}-{slot['key']}.jpg")
    print("Probne slike su u folderu preview/")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "generate"
    {"generate": cmd_generate, "schedule": cmd_schedule, "preview": cmd_preview}[cmd]()
