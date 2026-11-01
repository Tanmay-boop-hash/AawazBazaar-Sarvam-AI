# Awaaz Bazaar

A voice-first market coach for artisans, powered by Sarvam AI.
Turn a spoken product description into a multilingual listing, then review transparent price guidance and ways to stand out.
Price comparisons use artisan-provided listings; this prototype does not search marketplaces.

## Run locally

```bash
python3 -m venv .venv
.venv/bin/python -m pip install sarvamai
```

Add your key to the local `.env` file as `SARVAM_API_KEY=...`, then start the app:

```bash
.venv/bin/python app.py
```

Open <http://127.0.0.1:8000>. The app binds to localhost and uses the existing `.env`; the key is never displayed in the UI.

## Sarvam APIs

- **Saaras v4** transcribes an artisan's short voice description.
- **Sarvam 105B Conversations** drafts the listing and seller guidance in the selected languages.
- **Mayura** translates the welcome speech when needed.
- **Bulbul v3** speaks the welcome when the visitor clicks the audio button.

The introduction audio is generated on demand and cached in the browser session. Product descriptions, costs, and comparison entries are sent to Sarvam for processing. This prototype does not save them.

## Price and competitor data

The prototype does not search marketplaces. Comparable listings must be entered by the user in this format, one per line:

```text
Title | price in rupees | source or URL | date checked
```

The market range and median reflect only those entered records. The suggested starting price is the higher of their median and the cost floor, rounded up to the next ₹10. The cost floor uses the artisan's all-in unit cost and target gross margin. It is guidance, not a live market quote. Sample entries are fictional and are labelled in the app.
