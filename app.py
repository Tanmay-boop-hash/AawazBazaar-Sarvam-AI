import base64
import json
import math
import os
import re
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from statistics import median

from sarvamai import SarvamAI

ROOT = Path(__file__).resolve().parent
MAX_REQUEST_BYTES = 4 * 1024 * 1024
LANGUAGE_CODES = {
    "English": "en-IN", "Hindi": "hi-IN", "Tamil": "ta-IN", "Telugu": "te-IN",
    "Bengali": "bn-IN", "Marathi": "mr-IN", "Kannada": "kn-IN", "Gujarati": "gu-IN",
    "Punjabi": "pa-IN", "Malayalam": "ml-IN", "Odia": "od-IN",
}
WELCOME_TEXT = (
    "Welcome to Awaaz Bazaar. Tell us what you make by speaking or typing. "
    "Add your all-in cost and a few similar listings if you have them. "
    "We will draft a product listing, compare only the prices you provide, "
    "and suggest ways to tell your craft's story. You review every detail before sharing. "
    "Choose your advice language, then start with your product description."
)
WELCOME_AUDIO_CACHE = {}


def load_key():
    if os.environ.get("SARVAM_API_KEY"):
        return os.environ["SARVAM_API_KEY"]
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip() == "SARVAM_API_KEY":
                value = value.strip().strip("\"'")
                if value:
                    os.environ["SARVAM_API_KEY"] = value
                    return value
    raise RuntimeError("SARVAM_API_KEY is missing. Add it to .env and restart the app.")


def sarvam():
    return SarvamAI(api_subscription_key=load_key())


def parse_comparables(raw):
    results = []
    for line_no, line in enumerate(raw.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        if len(results) >= 8:
            raise ValueError("Enter at most 8 comparable listings.")
        parts = [part.strip() for part in line.split("|", 3)]
        if len(parts) < 2 or not parts[0]:
            raise ValueError(f"Comparable line {line_no}: use title | price | source | date.")
        price_text = re.sub(r"[^0-9.]", "", parts[1].replace(",", ""))
        try:
            price = float(price_text)
        except ValueError:
            raise ValueError(f"Comparable line {line_no}: enter a numeric price in rupees.")
        if not math.isfinite(price) or price <= 0:
            raise ValueError(f"Comparable line {line_no}: price must be greater than zero.")
        results.append({
            "title": parts[0][:120],
            "price": round(price, 2),
            "source": parts[2][:250] if len(parts) > 2 and parts[2] else "Not provided",
            "checked": parts[3][:40] if len(parts) > 3 and parts[3] else "Not provided",
        })
    return results


def price_analysis(cost, margin_percent, comparables):
    if not math.isfinite(cost) or cost <= 0:
        raise ValueError("Enter a positive all-in cost per item.")
    if not math.isfinite(margin_percent) or not 0 <= margin_percent <= 75:
        raise ValueError("Target gross margin must be between 0% and 75%.")
    cost_floor = cost / (1 - margin_percent / 100)
    result = {
        "cost_floor": round(cost_floor, 2),
        "target_margin_percent": margin_percent,
        "market_low": None,
        "market_median": None,
        "market_high": None,
        "suggested_anchor": None,
        "basis": "Cost floor only; add comparable listings for a market benchmark.",
    }
    if comparables:
        prices = [item["price"] for item in comparables]
        market_median = median(prices)
        anchor = max(cost_floor, market_median)
        result.update({
            "market_low": min(prices),
            "market_median": round(market_median, 2),
            "market_high": max(prices),
            "suggested_anchor": math.ceil(anchor / 10) * 10,
            "basis": (
                "Suggested anchor is the greater of the entered cost floor and the median of the "
                "comparables you supplied, rounded up to the next ₹10."
            ),
            "cost_above_comparable_range": cost_floor > max(prices),
        })
    return result


def transcribe_audio(payload):
    data_url = payload.get("audio_data", "")
    if "," in data_url:
        data_url = data_url.split(",", 1)[1]
    try:
        audio_bytes = base64.b64decode(data_url, validate=True)
    except Exception:
        raise ValueError("The audio upload could not be read. Try recording again.")
    if not audio_bytes or len(audio_bytes) > 3 * 1024 * 1024:
        raise ValueError("Audio must be non-empty and under 3 MB.")

    filename = Path(payload.get("filename") or "recording.webm").name
    suffix = Path(filename).suffix.lower() or ".webm"
    if suffix not in {".wav", ".mp3", ".m4a", ".aac", ".webm", ".ogg", ".flac", ".mp4"}:
        raise ValueError("Use a WAV, MP3, M4A, AAC, WebM, OGG, FLAC, or MP4 audio file.")
    client = sarvam()
    with tempfile.NamedTemporaryFile(suffix=suffix) as audio_file:
        audio_file.write(audio_bytes)
        audio_file.flush()
        with open(audio_file.name, "rb") as stream:
            response = client.speech_to_text.transcribe(
                file=stream,
                model="saaras:v4",
                mode="transcribe",
            )
    return {
        "transcript": response.transcript,
        "language": getattr(response, "language_code", None),
    }


def welcome_audio(payload):
    language = str(payload.get("language") or "English")
    language_code = LANGUAGE_CODES.get(language)
    if not language_code:
        raise ValueError("Choose a supported welcome language.")
    if language in WELCOME_AUDIO_CACHE:
        return WELCOME_AUDIO_CACHE[language]

    client = sarvam()
    spoken_text = WELCOME_TEXT
    if language_code != "en-IN":
        translation = client.text.translate(
            input=WELCOME_TEXT,
            source_language_code="en-IN",
            target_language_code=language_code,
            model="mayura:v1",
            mode="modern-colloquial",
        )
        spoken_text = translation.translated_text
    audio = client.text_to_speech.convert(
        text=spoken_text,
        language_code=language_code,
        model="bulbul:v3",
        output_audio_codec="wav",
    )
    result = {"language": language, "script": spoken_text, "audio_base64": audio.audios[0]}
    WELCOME_AUDIO_CACHE[language] = result
    return result


def generate_listing(payload):
    description = str(payload.get("description", "")).strip()
    if not description:
        raise ValueError("Describe the product or record a voice note first.")
    if len(description) > 5000:
        raise ValueError("Keep the product description under 5,000 characters.")

    cost = float(payload.get("unit_cost") or 0)
    margin_value = payload.get("target_margin")
    margin = float(margin_value if margin_value not in (None, "") else 35)
    comparables = parse_comparables(str(payload.get("comparables", "")))
    pricing = price_analysis(cost, margin, comparables)
    output_language = str(payload.get("output_language") or "English")[:40]
    advice_language = str(payload.get("advice_language") or "English")[:40]
    market_context = str(payload.get("market_context") or "Not provided")[:180]
    item = {
        "artisan_description": description,
        "output_language": output_language,
        "advice_language": advice_language,
        "target_market_or_channel": market_context,
        "comparables_entered_by_user": comparables,
        "price_math": pricing,
    }

    system = (
        "You are Awaaz Bazaar, a careful product-listing and market-positioning assistant for artisans. "
        "Return one valid JSON object only. Do not invent facts, materials, origin, certifications, "
        "sales performance, competitor listings, or market trends. Use only the supplied artisan "
        "description and comparable records. Treat comparable records as user-supplied and unverified. "
        "Do not generalize them into claims about the whole market. "
        "If no comparable records exist, say that no competitor comparison is available. "
        "Never change the supplied price math. A differentiation suggestion must be grounded in a stated "
        "product fact; otherwise ask a useful question instead of claiming it as a differentiator. "
        "Write the marketplace listing in the requested output language and the coaching notes in the "
        "requested advice language. Keep the tone warm, practical, and concise."
    )
    user = (
        "Prepare a reviewable marketplace listing and seller coaching. Use exactly these JSON keys: "
        "listing_title (string), listing_description (string), product_facts_used (array of strings), "
        "differentiation_points (array of strings), improvement_suggestions (array of strings), "
        "questions_for_artisan (array of strings), competitor_readout (string), "
        "seller_summary (string). Keep listing claims faithful to the artisan's words. "
        "The price calculation is separate and already computed from the provided costs and comps.\n\n"
        + json.dumps(item, ensure_ascii=False)
    )
    response = sarvam().chat.completions(
        model="sarvam-105b-conversations",
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=1200,
    )
    content = response.choices[0].message.content
    try:
        result = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        raise RuntimeError("Sarvam returned an unreadable draft. Please try again.")
    if not isinstance(result, dict):
        raise RuntimeError("Sarvam returned an unreadable draft. Please try again.")
    for field in (
        "product_facts_used", "differentiation_points", "improvement_suggestions", "questions_for_artisan"
    ):
        if not isinstance(result.get(field), list):
            result[field] = []
    for field in ("listing_title", "listing_description", "competitor_readout", "seller_summary"):
        if not isinstance(result.get(field), str):
            result[field] = ""
    result["pricing"] = pricing
    result["comparables"] = comparables
    result["evidence_note"] = (
        "Competitor prices are only the records entered here; this prototype does not search marketplaces."
        if comparables else
        "No competitor listings were provided, so this result has no market-price benchmark."
    )
    return result


PAGE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Awaaz Bazaar — Artisan market coach</title>
<style>
:root{--ink:#20231e;--muted:#72776c;--paper:#f5f2eb;--white:#fffefa;--line:#e5e0d5;--green:#244d3e;--green2:#dce8df;--rust:#b75b3d;--gold:#d49c45;--shadow:0 18px 52px rgba(37,43,35,.08)}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}button,input,textarea,select{font:inherit}button{cursor:pointer}.shell{max-width:1280px;margin:auto;padding:24px 30px 44px}.topbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:34px}.brand{display:flex;gap:11px;align-items:center;letter-spacing:.09em;font-size:12px;font-weight:800}.brand-mark{width:38px;height:38px;border-radius:12px;background:var(--green);color:#f4dfb2;display:grid;place-items:center;font-size:21px}.brand small{display:block;color:var(--muted);font-size:10px;letter-spacing:.14em;font-weight:600;margin-top:1px}.top-pill{font-size:12px;color:var(--green);background:var(--green2);padding:7px 12px;border-radius:99px}.hero{display:flex;align-items:end;justify-content:space-between;gap:20px;margin:0 0 24px}.eyebrow{color:var(--rust);font-size:11px;letter-spacing:.16em;text-transform:uppercase;font-weight:800}.hero h1{font-family:Georgia,serif;font-weight:500;font-size:clamp(32px,4vw,50px);line-height:1.05;letter-spacing:-.04em;margin:7px 0}.hero p{color:var(--muted);max-width:670px;margin:0}.hero-note{max-width:210px;color:var(--muted);font-size:12px;text-align:right}.layout{display:grid;grid-template-columns:minmax(360px,.88fr) minmax(440px,1.12fr);gap:18px;align-items:start}.card{background:var(--white);border:1px solid var(--line);border-radius:18px;box-shadow:var(--shadow)}.form-card{padding:24px}.card-head{display:flex;justify-content:space-between;align-items:start;margin-bottom:19px}.card-head h2{font:500 22px Georgia,serif;margin:0}.card-head p{font-size:12px;color:var(--muted);margin:5px 0 0}.step{font-size:10px;font-weight:800;letter-spacing:.1em;color:var(--muted);background:#f0eee7;padding:6px 9px;border-radius:99px}.field{margin:16px 0}.field label{display:flex;justify-content:space-between;gap:12px;font-weight:700;font-size:12px;margin:0 0 7px}.hint{font-weight:400;color:var(--muted);font-size:11px}.entry,textarea,select{width:100%;border:1px solid #dcd8cf;background:#fff;border-radius:10px;padding:11px 12px;color:var(--ink);outline:none;transition:.15s}textarea{resize:vertical;min-height:104px;line-height:1.5}.entry:focus,textarea:focus,select:focus{border-color:#648572;box-shadow:0 0 0 3px rgba(36,77,62,.1)}.voice-row{display:flex;align-items:center;gap:8px;margin:8px 0 12px}.voice-btn,.upload-btn,.soft-btn{border:1px solid var(--line);background:#fff;border-radius:9px;padding:8px 11px;font-size:12px;font-weight:700;color:var(--green)}.voice-btn.recording{background:#fff0ed;color:#a33928;border-color:#e5a697}.voice-hint{font-size:11px;color:var(--muted)}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}.comp-wrap{position:relative}.comp-label{display:flex;align-items:center;justify-content:space-between}.demo-btn{border:0;background:none;color:var(--rust);font-size:11px;font-weight:800;padding:0}.demo-warning{display:none;padding:8px 10px;margin:8px 0 0;border-radius:8px;background:#fff4df;color:#785217;font-size:11px}.demo-warning.show{display:block}.primary{width:100%;border:0;background:var(--green);color:white;border-radius:10px;padding:13px 16px;font-weight:750;display:flex;align-items:center;justify-content:center;gap:8px;margin-top:18px;transition:.15s}.primary:hover{background:#193b2e;transform:translateY(-1px)}.primary:disabled{opacity:.65;cursor:wait;transform:none}.status{min-height:20px;margin-top:9px;color:var(--muted);font-size:12px;text-align:center}.result-card{min-height:600px;overflow:hidden}.result-top{padding:22px 24px 17px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between}.result-top h2{font:500 22px Georgia,serif;margin:0}.result-top span{font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:var(--green);font-weight:800}.empty{min-height:510px;display:grid;place-items:center;text-align:center;padding:42px}.empty-art{width:76px;height:76px;border-radius:50%;background:#f4ecda;color:var(--rust);display:grid;place-items:center;font-size:32px;margin:0 auto 17px}.empty h3{font:500 23px Georgia,serif;margin:0 0 6px}.empty p{max-width:340px;color:var(--muted);font-size:13px;margin:0}.results{padding:20px 24px 24px}.result-section{padding:15px 0;border-bottom:1px solid var(--line)}.result-section:first-child{padding-top:0}.result-section:last-child{border:0;padding-bottom:0}.section-label{font-size:10px;text-transform:uppercase;letter-spacing:.14em;color:var(--muted);font-weight:800;margin-bottom:9px}.listing-title{font:500 23px Georgia,serif;margin:0 0 8px}.listing-copy{white-space:pre-wrap;margin:0;color:#3b4037;font-size:14px}.chips{display:flex;flex-wrap:wrap;gap:7px}.chip{background:#edf3ee;color:#315b47;padding:6px 9px;border-radius:7px;font-size:12px}.note-list{margin:0;padding-left:18px;color:#3b4037}.note-list li{margin:4px 0}.price-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}.price-box{padding:12px;border-radius:11px;background:#f5f2eb}.price-box small{display:block;color:var(--muted);font-size:10px;line-height:1.3}.price-box strong{display:block;font-size:18px;margin-top:3px}.price-box.featured{background:var(--green);color:white}.price-box.featured small{color:#c8d8cf}.formula{margin-top:9px;color:var(--muted);font-size:11px}.warning{background:#fff4df;border-radius:9px;padding:10px 12px;color:#76541d;font-size:12px;margin-top:10px}.evidence{font-size:11px;color:var(--muted);margin:10px 0 0}.comp-table{width:100%;border-collapse:collapse;font-size:12px}.comp-table td{padding:7px 5px;border-top:1px solid var(--line);vertical-align:top}.comp-table td:last-child{text-align:right;white-space:nowrap;font-weight:750}.actions{display:flex;gap:8px;margin:15px 24px 20px}.actions button{border:1px solid var(--line);background:#fff;padding:8px 11px;border-radius:8px;font-size:11px;font-weight:700;color:var(--green)}.footer{display:flex;justify-content:space-between;gap:15px;padding:18px 2px 0;color:var(--muted);font-size:11px}.footer strong{color:var(--green)}.hidden{display:none!important}@media(max-width:900px){.layout{grid-template-columns:1fr}.result-card{min-height:0}.empty{min-height:290px}.hero-note{display:none}}@media(max-width:560px){.shell{padding:17px 14px 30px}.topbar{margin-bottom:24px}.top-pill{font-size:10px}.hero{margin-bottom:17px}.form-card{padding:17px}.result-top,.results{padding-left:17px;padding-right:17px}.actions{margin-left:17px}.grid2{grid-template-columns:1fr}.price-grid{grid-template-columns:1fr 1fr}.price-box:last-child{grid-column:1/-1}.footer{flex-direction:column}}
.hero{align-items:flex-start;margin-top:0}.topbar{margin-bottom:22px}.hero-note{max-width:none;min-width:290px;text-align:right}.welcome-controls{display:flex;align-items:center;justify-content:flex-end;gap:9px}.intro-btn{padding:11px 14px}.language-menu{position:relative}.language-menu summary{height:44px;min-width:52px;display:flex;align-items:center;justify-content:center;gap:7px;padding:0 11px;border:1px solid #b9c9bf;border-radius:10px;background:#fffefa;color:var(--green);font-size:12px;font-weight:750;box-shadow:0 5px 16px rgba(37,43,35,.06);cursor:pointer;list-style:none}.language-menu summary::-webkit-details-marker{display:none}.language-menu summary:hover,.language-menu[open] summary{background:var(--green2)}.language-chevron{font-size:15px;line-height:1}.language-options{position:absolute;z-index:5;right:0;top:calc(100% + 7px);width:180px;max-height:250px;overflow:auto;padding:5px;border:1px solid var(--line);border-radius:12px;background:var(--white);box-shadow:0 12px 32px rgba(37,43,35,.16);text-align:left}.language-options button{display:block;width:100%;padding:8px 10px;border:0;border-radius:7px;background:transparent;color:var(--ink);text-align:left;font-size:13px}.language-options button:hover,.language-options button[aria-selected=true]{background:var(--green2);color:var(--green)}#outputLanguage{min-height:50px;padding:12px 14px;border-radius:11px;font-size:15px}.intro-caption{margin-top:7px}.welcome-audio{width:min(100%,290px);margin:8px 0 0 auto}@media(max-width:900px){.hero{align-items:flex-start}.hero-note{min-width:0}.welcome-controls{justify-content:flex-start}.intro-caption{text-align:left}.welcome-audio{margin-left:0}}@media(max-width:560px){.hero{gap:14px}.hero-note{width:100%}.welcome-controls{flex-wrap:wrap}.language-options{right:0}}.intro-btn{border:1px solid #b9c9bf;background:#fffefa;border-radius:10px;color:var(--green);font-size:12px;font-weight:750;padding:10px 13px;box-shadow:0 5px 16px rgba(37,43,35,.06);white-space:nowrap}.intro-btn:hover{background:var(--green);color:white}.intro-btn:disabled{opacity:.65;cursor:wait}.intro-caption{font-size:10px;color:var(--muted);margin-top:7px;text-align:right}.welcome-audio{display:block;width:220px;height:34px;margin:8px 0 0 auto}.welcome-audio.hidden{display:none!important}@media(max-width:900px){.hero-note{display:block;max-width:none;text-align:left}.intro-caption{text-align:left}.welcome-audio{margin-left:0}}
</style>
</head>
<body>
<div class="shell">
  <header class="topbar"><div class="brand"><div class="brand-mark">आ</div><div>AAWAAZ BAZAAR<small>ARTISAN MARKET COACH</small></div></div><div class="top-pill">✦ Voice-first · multilingual help</div></header>
  <section class="hero"><div><div class="eyebrow">Your craft, ready for its next buyer</div><h1>Make your work stand out.</h1><p>Describe your product in your own words. Get a thoughtful listing and price guidance grounded in your costs and the comparisons you provide.</p></div><div class="hero-note"><div class="welcome-controls"><button id="introBtn" class="intro-btn" type="button">▶ &nbsp;Hear the welcome</button><details class="language-menu"><summary aria-label="Choose welcome language" title="Choose welcome language"><span aria-hidden="true">文</span><span class="language-current">English</span><span class="language-chevron" aria-hidden="true">⌄</span></summary><div class="language-options" role="listbox" aria-label="Welcome language"><button type="button" role="option" data-language="English">English</button><button type="button" role="option" data-language="Hindi">Hindi</button><button type="button" role="option" data-language="Tamil">Tamil</button><button type="button" role="option" data-language="Telugu">Telugu</button><button type="button" role="option" data-language="Bengali">Bengali</button><button type="button" role="option" data-language="Marathi">Marathi</button><button type="button" role="option" data-language="Kannada">Kannada</button><button type="button" role="option" data-language="Gujarati">Gujarati</button><button type="button" role="option" data-language="Punjabi">Punjabi</button><button type="button" role="option" data-language="Malayalam">Malayalam</button><button type="button" role="option" data-language="Odia">Odia</button></div></details><select id="adviceLanguage" class="hidden" aria-hidden="true" tabindex="-1"><option>English</option><option>Hindi</option><option>Tamil</option><option>Telugu</option><option>Bengali</option><option>Marathi</option><option>Kannada</option><option>Gujarati</option><option>Punjabi</option><option>Malayalam</option><option>Odia</option></select></div><div class="intro-caption">Choose a language, then listen to your welcome</div><audio id="welcomeAudio" class="welcome-audio hidden" controls></audio></div></section>
  <main class="layout">
    <section class="card form-card">
      <div class="card-head"><div><h2>Tell us about your craft</h2><p>Speak or type. You can edit the transcript before creating a draft.</p></div><span class="step">01 / INPUT</span></div>
      <div class="field"><label for="description">Product description <span class="hint">What makes this piece yours?</span></label><div class="voice-row"><button class="voice-btn" id="recordBtn" type="button">● &nbsp;Record voice</button><label class="upload-btn" for="audioFile">↑ &nbsp;Upload audio</label><input id="audioFile" class="hidden" type="file" accept="audio/*"><span id="voiceStatus" class="voice-hint">Up to 30 seconds</span></div><textarea id="description" placeholder="Example: I weave these cotton stoles by hand using a pattern my family has used for years. Each one takes me two days."></textarea></div>
      <div class="field"><label for="outputLanguage">Listing language</label><select id="outputLanguage"><option>English</option><option>Hindi</option><option>Tamil</option><option>Telugu</option><option>Bengali</option><option>Marathi</option><option>Kannada</option><option>Gujarati</option><option>Punjabi</option><option>Malayalam</option><option>Odia</option></select></div>
      <div class="field"><label for="marketContext">Target market or channel <span class="hint">optional</span></label><input class="entry" id="marketContext" type="text" maxlength="180" placeholder="e.g. Indian craft marketplace · urban buyers"></div>
      <div class="grid2"><div class="field"><label for="unitCost">All-in cost per item <span class="hint">₹</span></label><input class="entry" id="unitCost" type="number" min="1" step="1" placeholder="e.g. 650"></div><div class="field"><label for="margin">Target gross margin <span id="marginValue" class="hint">35%</span></label><input id="margin" type="range" min="0" max="75" value="35" style="width:100%;accent-color:#244d3e;margin-top:8px"></div></div>
      <div class="field comp-wrap"><div class="comp-label"><label for="comparables">Comparable listings <span class="hint">optional · one per line</span></label><button class="demo-btn" type="button" id="demoBtn">Try sample</button></div><textarea id="comparables" style="min-height:90px" placeholder="Title | price in ₹ | source/link | date checked&#10;Handwoven cotton stole | 1450 | marketplace listing URL | 2026-10-28"></textarea><div id="demoWarning" class="demo-warning">Sample prices are fictional demo data. Replace them with real, dated comparisons before using the recommendation.</div><div class="hint" style="margin-top:6px">Use similar products and enter a source and date where you have them. This prototype does not search marketplaces.</div></div>
      <div class="hint">Audio, product details, costs, and comparison entries are sent to Sarvam for processing. This prototype does not save them.</div>
      <button class="primary" id="generateBtn" type="button">Create my listing &amp; market snapshot <span>→</span></button><div id="status" class="status"></div>
    </section>
    <section class="card result-card"><div class="result-top"><h2>Your market snapshot</h2><span id="resultTag">Ready when you are</span></div><div id="empty" class="empty"><div><div class="empty-art">✳</div><h3>A clearer story for your craft</h3><p>Your draft will appear here, with editable copy, a transparent price calculation, and market context from the comparisons you enter.</p></div></div><div id="results" class="results hidden"></div><div id="actions" class="actions hidden"><button id="copyBtn" type="button">Copy listing</button><button id="downloadBtn" type="button">Download snapshot</button></div></section>
  </main>
  <footer class="footer"><span><strong>Built for the maker, not just the marketplace.</strong> Review and approve every detail.</span><span>Competitor data is user-supplied · No live marketplace feed</span></footer>
</div>
<script>
const $ = id => document.getElementById(id);
let currentResult = null;
let recorder = null;
let recordedChunks = [];
const welcomeAudioCache = new Map();
const adviceLanguage = $("adviceLanguage");
const languageMenu = document.querySelector(".language-menu");
languageMenu.querySelectorAll("[data-language]").forEach(option => {
  option.addEventListener("click", () => {
    adviceLanguage.value = option.dataset.language;
    languageMenu.querySelector(".language-current").textContent = option.dataset.language;
    languageMenu.querySelectorAll("[role=option]").forEach(item => item.setAttribute("aria-selected", String(item === option)));
    languageMenu.open = false;
  });
});
document.addEventListener("click", event => {
  if (!languageMenu.contains(event.target)) languageMenu.open = false;
});
$("introBtn").addEventListener("click", async () => {
  const button = $("introBtn");
  const language = $("adviceLanguage").value;
  button.disabled = true;
  button.textContent = "Preparing…";
  try {
    if (!welcomeAudioCache.has(language)) {
      const response = await fetch("/api/welcome-audio", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({language})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Could not prepare the welcome audio.");
      welcomeAudioCache.set(language, result.audio_base64);
    }
    const player = $("welcomeAudio");
    player.src = "data:audio/wav;base64," + welcomeAudioCache.get(language);
    player.classList.remove("hidden");
    try { await player.play(); } catch (error) { player.controls = true; }
    button.textContent = "▶  Hear welcome again";
    $("introBtn").setAttribute("aria-label", "Play the welcome in " + language);
  } catch (error) {
    button.textContent = "▶  Try the welcome again";
    $("status").textContent = error.message;
  } finally { button.disabled = false; }
});
$("margin").addEventListener("input", () => { $("marginValue").textContent = $("margin").value + "%"; });
$("demoBtn").addEventListener("click", () => {
  $("description").value = "I weave cotton stoles by hand on a traditional handloom. This one uses indigo and takes two days to finish. Each piece is slightly different, and I make them in my family workshop in Kutch.";
  $("unitCost").value = "620";
  $("comparables").value = "Sample listing A — cotton stole | 1150 | FICTIONAL DEMO DATA | demo\nSample listing B — handloom stole | 1480 | FICTIONAL DEMO DATA | demo\nSample listing C — indigo cotton wrap | 1690 | FICTIONAL DEMO DATA | demo";
  $("demoWarning").classList.add("show");
});
$("comparables").addEventListener("input", () => $("demoWarning").classList.remove("show"));
async function sendAudio(file) {
  $("voiceStatus").textContent = "Transcribing…";
  $("recordBtn").disabled = true;
  try {
    const data = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsDataURL(file); });
    const response = await fetch("/api/transcribe", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({audio_data:data, filename:file.name || "recording.webm"})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Transcription failed.");
    $("description").value = result.transcript;
    $("voiceStatus").textContent = result.language ? "Detected: " + result.language : "Transcript ready — review it";
  } catch (error) { $("voiceStatus").textContent = error.message; }
  finally { $("recordBtn").disabled = false; }
}
$("audioFile").addEventListener("change", event => { const file = event.target.files[0]; if (file) sendAudio(file); event.target.value = ""; });
$("recordBtn").addEventListener("click", async () => {
  if (recorder && recorder.state === "recording") { recorder.stop(); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({audio:true});
    recordedChunks = [];
    recorder = new MediaRecorder(stream);
    recorder.ondataavailable = event => { if (event.data.size) recordedChunks.push(event.data); };
    recorder.onstop = () => { const blob = new Blob(recordedChunks, {type:recorder.mimeType || "audio/webm"}); const extension = blob.type.includes("mp4") ? "mp4" : "webm"; stream.getTracks().forEach(track => track.stop()); sendAudio(new File([blob], "recording." + extension, {type:blob.type})); $("recordBtn").textContent = "●  Record voice"; $("recordBtn").classList.remove("recording"); };
    recorder.start();
    $("recordBtn").textContent = "■  Stop recording";
    $("recordBtn").classList.add("recording");
    $("voiceStatus").textContent = "Recording… stop within 30 seconds";
    setTimeout(() => { if (recorder && recorder.state === "recording") recorder.stop(); }, 28000);
  } catch (error) { $("voiceStatus").textContent = "Microphone unavailable. You can upload audio or type instead."; }
});
function escapeHtml(value) { return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c])); }
function rupees(value) { return value == null ? "—" : "₹" + Number(value).toLocaleString("en-IN", {maximumFractionDigits:0}); }
function list(items) { return items && items.length ? "<ul class=\"note-list\">" + items.map(item => "<li>" + escapeHtml(item) + "</li>").join("") + "</ul>" : "<span class=\"hint\">No additional notes.</span>"; }
function render(data) {
  const p = data.pricing;
  let range = p.market_low == null ? "Add comps" : rupees(p.market_low) + "–" + rupees(p.market_high);
  let medianLabel = p.market_median == null ? "No median available" : "Median " + rupees(p.market_median);
  let priceHtml = "<div class=\"price-grid\"><div class=\"price-box\"><small>Cost floor at " + escapeHtml(p.target_margin_percent) + "% target margin</small><strong>" + rupees(p.cost_floor) + "</strong></div><div class=\"price-box\"><small>Comparable range · entered data</small><strong>" + range + "</strong><small>" + medianLabel + "</small></div><div class=\"price-box featured\"><small>Suggested starting price</small><strong>" + (p.suggested_anchor == null ? "Need comps" : rupees(p.suggested_anchor)) + "</strong></div></div><div class=\"formula\">" + escapeHtml(p.basis) + " Cost floor = all-in cost ÷ (1 − target margin). Include platform fees, packaging, and shipping in your cost where relevant.</div>";
  if (p.cost_above_comparable_range) priceHtml += "<div class=\"warning\">Your cost floor is above every entered comparable. Consider a premium position with clear evidence of added value, or review your costs before setting a price.</div>";
  let comps = data.comparables.length ? "<table class=\"comp-table\">" + data.comparables.map(c => "<tr><td>" + escapeHtml(c.title) + "<br><span class=\"hint\">" + escapeHtml(c.source) + " · " + escapeHtml(c.checked) + "</span></td><td>" + rupees(c.price) + "</td></tr>").join("") + "</table>" : "<p class=\"hint\">No comparable listings entered. Price view uses your costs only.</p>";
  $("results").innerHTML = "<section class=\"result-section\"><div class=\"section-label\">Suggested listing · " + escapeHtml($("outputLanguage").value) + "</div><textarea id=\"editTitle\" class=\"entry listing-title\" rows=\"1\">" + escapeHtml(data.listing_title) + "</textarea><textarea id=\"editDescription\" style=\"margin-top:8px;min-height:110px\">" + escapeHtml(data.listing_description) + "</textarea></section>" +
  "<section class=\"result-section\"><div class=\"section-label\">Facts used from your description</div>" + list(data.product_facts_used) + "</section>" +
  "<section class=\"result-section\"><div class=\"section-label\">Your price picture</div>" + priceHtml + "</section>" +
  "<section class=\"result-section\"><div class=\"section-label\">How to stand out</div>" + list(data.differentiation_points) + "</section>" +
  "<section class=\"result-section\"><div class=\"section-label\">Polish the listing</div>" + list(data.improvement_suggestions) + "</section>" +
  "<section class=\"result-section\"><div class=\"section-label\">Questions that could improve your listing</div>" + list(data.questions_for_artisan) + "</section>" +
  "<section class=\"result-section\"><div class=\"section-label\">Comparable listings</div>" + comps + "<p class=\"evidence\">" + escapeHtml(data.evidence_note) + "</p><p class=\"listing-copy\" style=\"margin-top:9px\">" + escapeHtml(data.competitor_readout) + "</p></section>" +
  "<section class=\"result-section\"><div class=\"section-label\">A note for you</div><p class=\"listing-copy\">" + escapeHtml(data.seller_summary) + "</p></section>";
  $("empty").classList.add("hidden"); $("results").classList.remove("hidden"); $("actions").classList.remove("hidden"); $("resultTag").textContent = "Draft · review before sharing";
}
$("generateBtn").addEventListener("click", async () => {
  const button = $("generateBtn");
  button.disabled = true; button.textContent = "Building your market snapshot…"; $("status").textContent = "Using Sarvam to shape your listing. Your entered comparison data stays visible.";
  try {
    const response = await fetch("/api/analyze", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({description:$("description").value, unit_cost:$("unitCost").value, target_margin:$("margin").value, comparables:$("comparables").value, market_context:$("marketContext").value, output_language:$("outputLanguage").value, advice_language:$("adviceLanguage").value})});
    const data = await response.json(); if (!response.ok) throw new Error(data.error || "Could not create the draft.");
    currentResult = data; render(data); $("status").textContent = "Draft ready. Check the facts and edit the listing before sharing.";
  } catch (error) { $("status").textContent = error.message; }
  finally { button.disabled = false; button.innerHTML = "Create my listing &amp; market snapshot <span>→</span>"; }
});
$("copyBtn").addEventListener("click", async () => { const text = $("editTitle").value + "\n\n" + $("editDescription").value; await navigator.clipboard.writeText(text); $("status").textContent = "Listing copied."; });
$("downloadBtn").addEventListener("click", () => { if (!currentResult) return; const data = {...currentResult, listing_title:$("editTitle").value, listing_description:$("editDescription").value}; const blob = new Blob([JSON.stringify(data,null,2)], {type:"application/json"}); const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "awaaz-bazaar-snapshot.json"; a.click(); URL.revokeObjectURL(a.href); });
</script>
</body>
</html>'''


class AppHandler(BaseHTTPRequestHandler):
    def send_json(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("Request is empty or too large.")
        return json.loads(self.rfile.read(length))

    def do_GET(self):
        if self.path not in {"/", "/index.html"}:
            self.send_error(404)
            return
        body = PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            payload = self.read_json()
            if self.path == "/api/transcribe":
                result = transcribe_audio(payload)
            elif self.path == "/api/welcome-audio":
                result = welcome_audio(payload)
            elif self.path == "/api/analyze":
                result = generate_listing(payload)
            else:
                self.send_json(404, {"error": "Route not found."})
                return
            self.send_json(200, result)
        except ValueError as error:
            self.send_json(400, {"error": str(error)})
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_json(400, {"error": "Could not read the request. Please try again."})
        except Exception as error:
            # Do not return request headers or credentials to the browser.
            message = str(error)
            key = os.environ.get("SARVAM_API_KEY", "")
            if key:
                message = message.replace(key, "[redacted]")
            self.send_json(502, {"error": message[:400] or "Sarvam request failed. Please try again."})

    def log_message(self, format_string, *args):
        # Keep local logs free of product descriptions and other request data.
        super().log_message(format_string, *args)


def main():
    load_key()
    server = HTTPServer(("127.0.0.1", 8000), AppHandler)
    print("Awaaz Bazaar is running at http://127.0.0.1:8000")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nAwaaz Bazaar stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
