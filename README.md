# SHARK MINDSET Autopilot (@20shark26)

Besplatan sistem koji sam pravi i zakazuje objave na Instagramu, i dok je laptop ugašen.

## Kako radi
- **GitHub Actions** se pokreće 3 puta dnevno (oko 05:00, 12:00 i 17:00 po Beogradu).
- **Gemini** (rezerva: **Groq**) piše 5 objava: 08:00 motivacija, 11:30 novac, 14:30 prodaja, 18:00 psihologija biznisa, 21:00 večernji izazov.
- **Pillow** pravi brendirane slike 1080x1350, koje se čuvaju u folderu `posts/`.
- **Buffer** (besplatni plan, max 10 objava u redu) dobija onoliko objava koliko ima slobodnih mesta; ostale čekaju sledeće pokretanje.
- `history.json` pamti teme da se ne ponavljaju, a `queue.json` čuva status svake objave.

## Potrebno (Settings → Secrets and variables → Actions)
`GEMINI_API_KEY`, `GROQ_API_KEY`, `BUFFER_API_KEY`, `BUFFER_CHANNEL_ID`.
Repo mora biti **javan**, da bi Buffer mogao da preuzme slike.

## Upravljanje
- **Ručno pokretanje:** Actions → Shark Mindset Autopilot → Run workflow
- **Pauza:** Actions → Shark Mindset Autopilot → ⋯ → Disable workflow
- **Probni režim:** Settings → Secrets and variables → Actions → Variables → `DRY_RUN` = `true`
- **Termini i teme:** lista `SLOTS` u `main.py`
