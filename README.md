# Shark Mindset Autopilot (@20shark26)

Ovaj sistem automatski generiše, dizajnira i zakazuje 5 objava dnevno na Instagramu koristeći AI i GitHub Actions.

## Kako funkcioniše
1. **GitHub Actions** se pokreće svakog jutra u 05:00.
2. **AI (Gemini/Groq)** smišlja 5 tema i tekstova na srpskom jeziku.
3. **Python (Pillow)** kreira slike sa naslovima i logotipom.
4. **Buffer API** zakazuje objave u određeno vreme (08:00, 11:30, 14:30, 18:00, 21:00).

## Konfiguracija
Sve postavke se nalaze u `main.py`:
- `POST_TYPES`: Vremena i tipovi objava.
- `accent_colors`: Boje koje se koriste za dizajn.
- `history.json`: Čuva istoriju da se teme ne bi ponavljale.

## Pauziranje
Da biste pauzirali sistem, idite na **GitHub Repo -> Actions -> Shark Mindset Autopilot -> Disable workflow**.

## Ručno pokretanje
Idite na **GitHub Repo -> Actions -> Shark Mindset Autopilot -> Run workflow**.

## Potrebni Secret-i
- `GEMINI_API_KEY`
- `GROQ_API_KEY`
- `BUFFER_API_KEY`
- `BUFFER_CHANNEL_ID`

---
🤖 Generisano pomoću [Claude Code](https://claude.com/claude-code)
