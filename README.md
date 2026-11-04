# Awaaz Bazaar

Awaaz Bazaar is a small multilingual listing and pricing assistant for Indian artisans. An artisan can describe a product by voice or text, add its cost and optional competitor prices, then review an editable listing and a price snapshot.

**Live demo:** [aawaz-bazaar-sarvam-ai.vercel.app](https://aawaz-bazaar-sarvam-ai.vercel.app)

## What it does

- Accepts typed product details, a short recording, or an audio upload. Sarvam Saaras v4 transcribes audio.
- Drafts a product title, description, and practical seller suggestions using Sarvam 105B Conversations. Listing and advice languages can be selected in the app.
- Calculates a cost floor from the artisan's unit cost and target gross margin. If the artisan enters comparable prices, it also shows their range and median and a suggested starting price.
- Offers a welcome message in supported languages using Sarvam Mayura and Bulbul.
- Lets the user edit and copy the listing or download a JSON snapshot.
- Lets the user choose a product image for a local preview in the snapshot. The image stays in the browser; it is not sent to Sarvam, saved by the app, or included in the downloaded JSON.

## What it does not do

- It does not search marketplaces or verify competitor prices. Comparisons come only from the artisan, and the result labels them as user-provided.
- Without comparison prices, the app can still create a snapshot and show a cost-based price floor, but it cannot provide a market-price benchmark.
- The price suggestion is a calculation from the entered cost, margin, and optional comparisons. It is not a live quote or a guarantee that the product will sell at that price.
- AI suggestions should be reviewed before they are used. The app does not have user accounts or save product records. Product text, audio, costs, and comparisons are sent to Sarvam for processing.

## Run locally

You need Python and a Sarvam API key.

1. Create and activate a virtual environment, then install the SDK:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Create a `.env` file in the project root and add your key:

   ```dotenv
   SARVAM_API_KEY=your_key_here
   ```

   `.env` is listed in `.gitignore`; keep your real key out of source control.

3. Start the app:

   ```bash
   python app.py
   ```

4. Open <http://127.0.0.1:8000>.

The welcome audio and AI features require a valid API key. For voice recording, allow microphone access in your browser. Audio sent for transcription must be under 3 MB; product images are limited to 5 MB and remain local to the browser.

## Use the form

1. Choose a language for the welcome and seller advice. Choose a listing language for the generated product copy.
2. Speak, upload audio, or type a product description. Add a product image if you want a local preview.
3. Enter an all-in cost per item and target margin. A positive cost is required.
4. Competitor comparisons are optional. Add one per line in this format:

   ```text
   Product or listing name | price in rupees | source or URL | date checked
   ```

   Only the name and numeric price are required; source and date are recommended. Up to eight entries are accepted.

5. Create the snapshot, review the generated suggestions, and edit the listing before sharing it.

## Deploy on Vercel

The repository includes a static `index.html`, Python API routes under `api/`, and `vercel.json` with a 60-second function limit.

1. Import the GitHub repository into Vercel.
2. Add `SARVAM_API_KEY` in the Vercel project settings for the environments you use (at least Production).
3. Deploy or redeploy after saving the environment variable.

Do not put the API key in `index.html` or commit it to GitHub.

## Project files

- `index.html` — standalone web interface served by Vercel.
- `app.py` — local Python server, Sarvam integrations, and the page used by local runs.
- `api/` — Vercel Python function routes for listing generation, transcription, and welcome audio.
- `sarvam_chat.py` — minimal example of a Sarvam chat completion call.
- `requirements.txt` — Python SDK dependency.

## Possible next improvements

- Add a guided, voice-led flow that asks only for product details that are missing.
- Explore sourced competitor discovery, with dates and clear notes when products are not directly comparable.
- Let artisans reuse a product or workshop profile across listings; accounts and cloud storage can wait until they are needed.
