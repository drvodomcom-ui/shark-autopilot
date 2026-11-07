import os
import json
import random
import textwrap
from datetime import datetime, timedelta
import pytz
import requests
from PIL import Image, ImageDraw, ImageFont
import google.generativeai as genai
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

# Konfiguracija
HISTORY_FILE = "history.json"
FONTS_DIR = "fonts"
POSTS_DIR = "posts"
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

# API Ključevi
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
GROQ_KEY = os.getenv("GROQ_API_KEY")
BUFFER_KEY = os.getenv("BUFFER_API_KEY")
BUFFER_CHANNEL_ID = os.getenv("BUFFER_CHANNEL_ID")

# Tipovi objava
POST_TYPES = [
    {"time": "08:00", "type": "Jutarnja motivacija", "desc": "jedna snažna rečenica + kratko objašnjenje"},
    {"time": "11:30", "type": "Lekcija o novcu", "desc": "konkretan savet, broj, primer"},
    {"time": "14:30", "type": "Prodaja i pregovaranje", "desc": "tehnika + kako je primeniti danas"},
    {"time": "18:00", "type": "Psihologija biznisa", "desc": "zanimljivost, 'da li si znao'"},
    {"time": "21:00", "type": "Večernji izazov", "desc": "pitanje ili izazov za publiku"}
]

def get_gemini_content(prompt):
    try:
        genai.configure(api_key=GEMINI_KEY)
        model = genai.GenerativeModel('gemini-2.0-flash')
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"Gemini error: {e}")
        return None

def get_groq_content(prompt):
    try:
        client = Groq(api_key=GROQ_KEY)
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        return completion.choices[0].message.content
    except Exception as e:
        print(f"Groq error: {e}")
        return None

def generate_content():
    # Učitaj istoriju
    history = []
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            history = json.load(f)

    last_topics = [h.get("topic", "") for h in history[-50:]]

    prompt = f"""
    Ti si stručnjak za Instagram sadržaj za profil @20shark26 „SHARK MINDSET".
    Teme: finansije, prodaja, psihologija biznisa, mindset, disciplina, novac.
    Jezik: srpski (latinica). Ton: samouveren, kratak, udarni, „ajkula" mentalitet.

    Generiši 5 objava za danas. Izbegavaj ove teme koje smo već imali: {last_topics}.

    Formatiraj odgovor isključivo kao JSON niz od 5 objekata.
    Svaki objekat mora imati:
    - "type": tip obave (iz liste ispod)
    - "title": naslov na slici (max 12 reči)
    - "caption": opis (80-150 reči, jak prvi red, poziv na akciju, 8-12 hashtagova)
    - "topic": kratka tema za istoriju

    Tipovi i termini:
    1. Jutarnja motivacija (08:00)
    2. Lekcija o novcu/finansijama (11:30)
    3. Prodaja i pregovaranje (14:30)
    4. Psihologija biznisa / greška (18:00)
    5. Večernje pitanje/izazov (21:00)

    Stroga pravila:
    - Bez izmišljenih statistika.
    - Bez klišea.
    - JSON mora biti validan.
    """

    content = get_gemini_content(prompt) or get_groq_content(prompt)
    if not content:
        raise Exception("Failed to get content from both APIs")

    # Očisti JSON ako Gemini doda markdown
    content = content.strip()
    if content.startswith("```json"):
        content = content[7:-3].strip()

    return json.loads(content)

def create_image(post_data, index):
    width, height = 1080, 1350
    # Boje
    bg_color = (10, 10, 15)  # Skoro crna
    accent_colors = [(0, 120, 255), (255, 215, 0), (0, 255, 127), (255, 69, 0), (138, 43, 226)]
    accent = accent_colors[index % len(accent_colors)]

    img = Image.new('RGB', (width, height), color=bg_color)
    draw = ImageDraw.Draw(img)

    # Fonts
    try:
        font_title = ImageFont.truetype(os.path.join(FONTS_DIR, "Montserrat-Bold.ttf"), 70)
        font_logo = ImageFont.truetype(os.path.join(FONTS_DIR, "BebasNeue-Regular.ttf"), 40)
        font_tag = ImageFont.truetype(os.path.join(FONTS_DIR, "Montserrat-Regular.ttf"), 30)
    except:
        font_title = ImageFont.load_default()
        font_logo = ImageFont.load_default()
        font_tag = ImageFont.load_default()

    # Draw shark fin shape (simplified)
    draw.polygon([(width//2, 100), (width//2 - 100, 300), (width//2 + 100, 300)], fill=accent)

    # Title wrap
    lines = textwrap.wrap(post_data['title'], width=20)
    y_text = height // 2 - (len(lines) * 40)
    for line in lines:
        # Center text
        bbox = draw.textbbox((0, 0), line, font=font_title)
        w_text = bbox[2] - bbox[0]
        draw.text(((width - w_text) // 2, y_text), line, font=font_title, fill=(255, 255, 255))
        y_text += 90

    # Logo & Tag
    draw.text((50, height - 100), "SHARK MINDSET", font=font_logo, fill=accent)
    draw.text((width - 250, height - 100), "@20shark26", font=font_tag, fill=(150, 150, 150))

    # Border
    draw.rectangle([20, 20, width-20, height-20], outline=accent, width=5)

    date_str = datetime.now().strftime("%Y-%m-%d")
    folder = os.path.join(POSTS_DIR, date_str)
    os.makedirs(folder, exist_ok=True)

    filename = f"post_{index}.jpg"
    filepath = os.path.join(folder, filename)
    img.save(filepath, quality=95)
    return filepath

def schedule_buffer(posts):
    if DRY_RUN:
        print("DRY_RUN: Skipping Buffer scheduling")
        return

    tz = pytz.timezone("Europe/Belgrade")
    now = datetime.now(tz)

    # Buffer API headers
    headers = {
        "Authorization": f"Bearer {BUFFER_KEY}",
        "Content-Type": "application/json"
    }

    # Get current repo info for raw links
    # Expecting GITHUB_REPOSITORY env var in Actions
    repo = os.getenv("GITHUB_REPOSITORY", "user/repo")
    base_url = f"https://raw.githubusercontent.com/{repo}/main/"

    for i, post in enumerate(posts):
        scheduled_time_str = POST_TYPES[i]["time"]
        scheduled_time = datetime.strptime(f"{now.strftime('%Y-%m-%d')} {scheduled_time_str}", "%Y-%m-%d %H:%M")
        scheduled_time = tz.localize(scheduled_time)

        if scheduled_time < now:
            scheduled_time = now + timedelta(minutes=10)

        due_at = scheduled_time.astimezone(pytz.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        image_url = f"{base_url}{post['image_path'].replace('\\', '/')}"

        # Verify image exists via HTTP before scheduling
        # (In real run, we might need a small delay for GitHub to refresh raw cache)

        payload = {
            "profile_ids": [BUFFER_CHANNEL_ID],
            "text": post['caption'],
            "media": {"photo": image_url},
            "scheduled_at": due_at
        }

        # NOTE: Buffer API uses different endpoints depending on version.
        # Using a generic POST /updates/create for basic API.
        response = requests.post("https://api.bufferapp.com/1/updates/create.json", headers=headers, data=payload)
        print(f"Buffer response for post {i}: {response.status_code} {response.text}")

def main():
    print("Generating content...")
    posts = generate_content()

    for i, post in enumerate(posts):
        print(f"Creating image for post {i}...")
        path = create_image(post, i)
        post['image_path'] = path

    print("Scheduling posts...")
    schedule_buffer(posts)

    # Update history
    history = []
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            history = json.load(f)

    for post in posts:
        history.append({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "topic": post['topic'],
            "type": post['type']
        })

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=4, ensure_ascii=False)

    print("Done.")

if __name__ == "__main__":
    main()
