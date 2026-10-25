# Prescription Sahayak

**Understand your doctor's prescription — in your own language.**

**Live app:** https://sarvam-prescription-agent.vercel.app

Prescription Sahayak is a mobile-first web app. A patient photographs a handwritten prescription. The app reads it, lists each medicine with dose, frequency, timing and duration, then reads the instructions aloud in one of 11 Indian languages.

The app is built on the [Sarvam AI](https://www.sarvam.ai) platform and deploys to [Vercel](https://vercel.com).

> **Disclaimer:** This tool helps people understand a prescription. It is not medical advice. AI can misread handwriting. Always confirm medicines and doses with a doctor or pharmacist.

---

## How it works

```
 Photo / PDF ──► Sarvam Vision ──► Sarvam LLM ──► Sarvam Translate ──► Bulbul TTS ──► Audio + text
                 (doc_ai.digitise)  (sarvam-105b)   (sarvam-translate:v1) (bulbul:v4-flash)
                 handwriting OCR    medicines,      English summary →      spoken MP3
                                    dosage, diagnosis  patient's language
```

| Step | Endpoint | Sarvam API |
|------|----------|------------|
| 1. Read the handwriting | `POST /api/digitise` | `client.doc_ai.digitise` (Sarvam Vision, `content_type="mixed"`) |
| 2. Structure the prescription | `POST /api/analyse` | `client.chat.completions` with `sarvam-105b` (JSON mode) |
| 3. Translate | `POST /api/translate` | `client.text.translate` with `sarvam-translate:v1` |
| 4. Speak | `POST /api/speak` | `client.text_to_speech.convert` with `bulbul:v4-flash` |

The browser runs these four steps in order, so the user sees real progress. If they switch language afterwards, only steps 3 and 4 run again.

**Languages:** Hindi, English, Bengali, Marathi, Tamil, Telugu, Kannada, Gujarati, Punjabi, Malayalam and Odia. Bulbul v4 has no Malayalam or Odia voices yet, so those two use `bulbul:v3`.

## Features

- **Camera-first input.** "Take photo" opens the phone's rear camera. Upload and drag-and-drop work on desktop.
- **Photos are prepared in the browser.** Each photo is rotated upright and downscaled before upload, so uploads are fast and stay under the size limit.
- **Clear medicine cards.** Each card shows dose, how often, when (before or after food) and for how long. Diagnosis, tests, advice and follow-up appear below the cards.
- **Uncertain readings are flagged.** Anything the model couldn't read clearly appears under "Please double-check with your pharmacist".
- **Large audio player.** Download and native share (WhatsApp and similar apps) are built in, along with slow, normal and fast speaking speeds.
- **Light and dark themes**, accessible markup, and an installable web app manifest.
- **Privacy.** Nothing is stored server-side, and prescription content is never logged.

## Project structure

```
├── api/
│   └── index.py          # FastAPI app → one Vercel Python serverless function
├── sahayak/              # Core library (framework-independent)
│   ├── config.py         # Model IDs, limits, languages and voices
│   ├── pipeline.py       # Sarvam Vision → LLM → Translate → TTS
│   ├── models.py         # Prescription / Medicine dataclasses
│   ├── text.py           # JSON extraction, chunking, patient summary
│   ├── media.py          # Upload normalisation (EXIF rotation, resize)
│   └── errors.py
├── public/               # Static frontend served by Vercel's CDN
│   ├── index.html
│   ├── styles.css
│   ├── app.js
│   ├── favicon.svg
│   └── manifest.webmanifest
├── tests/                # Offline tests (Sarvam calls are faked)
├── vercel.json           # Function config, /api rewrite, security headers
├── requirements.txt      # Runtime dependencies (used by Vercel)
├── requirements-dev.txt  # + uvicorn, pytest
└── .env.example
```

## API keys

The app needs **one** key:

| Variable | Where to get it | Used for |
|----------|-----------------|----------|
| `SARVAM_API_KEY` | [dashboard.sarvam.ai](https://dashboard.sarvam.ai) → API Keys | Vision, LLM, Translate and TTS |

The key stays on the server. The browser never sees it.

## Run locally

Requires Python 3.11 or later.

```bash
git clone https://github.com/Advait-Pathak9222/Sarvam_Prescription_agent.git
cd Sarvam_Prescription_agent

python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env          # then put your SARVAM_API_KEY in .env
uvicorn api.index:app --reload
```

Open <http://127.0.0.1:8000>. In local development the API docs are at `/api/docs`.

To try it on your phone, run `uvicorn api.index:app --host 0.0.0.0` and open `http://<your-computer-ip>:8000` on the same Wi-Fi. Browsers only allow the camera over HTTPS or on localhost, so use the deployed site to test the camera.

### Tests

```bash
pytest
```

## Deploy to Vercel

### Option A — Vercel dashboard (recommended)

1. Push this repository to GitHub.
2. In [vercel.com/new](https://vercel.com/new), import the repository. Leave **Framework Preset** on *Other* and keep the default build settings.
3. Under **Environment Variables**, add `SARVAM_API_KEY` for Production, Preview and Development.
4. Click **Deploy**. Each push to `main` then redeploys automatically.

### Option B — Vercel CLI

```bash
npm i -g vercel
vercel login
vercel link                                   # create or link the project
vercel env add SARVAM_API_KEY production      # paste the key when prompted
vercel env add SARVAM_API_KEY preview
vercel --prod
```

### Deployment notes

- `vercel.json` sends `/api/*` to the FastAPI function, with up to 60 seconds per request. It also sets a strict Content-Security-Policy and other security headers.
- Vercel limits request and response bodies to 4.5 MB. The browser downsizes photos to 4 MB or less before upload. Audio is generated in chunks of up to 1,500 characters, so each MP3 response stays small. The browser joins the chunks into one file.
- The site is public, so anyone with the URL can use your Sarvam credits. Before sharing widely, consider [Vercel Deployment Protection](https://vercel.com/docs/deployment-protection) or a Vercel Firewall rate limit on `/api/*`.

## Tech stack

- **Backend:** Python 3.12, FastAPI, `sarvamai` SDK, Pillow
- **Frontend:** HTML, CSS and JavaScript with no build step
- **Hosting:** Vercel (static CDN plus a Python serverless function)
