"""
bedrock_service.py – Amazon Bedrock integration for the AI Playground.

Uses a base foundation model from the Amazon Bedrock playground to provide
telecom-domain AI assistance: coverage analysis interpretation, link budget
recommendations, RF planning guidance, and general telecom Q&A.

Includes a RAG (Retrieval-Augmented Generation) context builder that injects
domain-specific RF engineering knowledge into prompts so the LLM can reason
semantically about numerical results instead of merely echoing them.

Environment variables:
    BEDROCK_REGION       – AWS region for Bedrock (default: us-east-1)
    BEDROCK_MODEL_ID     – Foundation model ID (default: amazon.nova-micro-v1:0)
    BEDROCK_MAX_TOKENS   – Max tokens in response (default: 1024)
    BEDROCK_TEMPERATURE  – Sampling temperature 0-1 (default: 0.7)
"""

import json
import logging
import math
import os
import re
from typing import Optional, List, Dict, Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

BEDROCK_REGION = os.getenv("BEDROCK_REGION", "us-east-1")
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "amazon.nova-micro-v1:0")
BEDROCK_MAX_TOKENS = int(os.getenv("BEDROCK_MAX_TOKENS", "1024"))
BEDROCK_TEMPERATURE = float(os.getenv("BEDROCK_TEMPERATURE", "0.7"))

_client = None

SYSTEM_PROMPT = (
    "You are an expert telecom RF engineer assistant for the TELECOM TOWER POWER "
    "platform. You help users understand link budget analysis, RF propagation, "
    "Fresnel zone clearance, repeater chain planning, antenna specifications, "
    "and general cellular network topics.\n\n"
    "IMPORTANT RULES:\n"
    "- Always interpret numerical results in engineering context. A signal of "
    "-95 dBm may be acceptable for narrowband SCADA telemetry but terrible for "
    "1 Gbps 5G backhaul.\n"
    "- When a terrain profile and Fresnel zone clearance are provided, reason about "
    "specific obstructions, Earth curvature effects, and practical remediation "
    "(raise antenna, use repeater, change frequency).\n"
    "- Use standard units: dBm for power, dB for gains/losses, km for distance, "
    "m for heights, MHz/GHz for frequency.\n"
    "- Reference ITU-R recommendations (P.526, P.525, P.676) and propagation models "
    "(Free-Space, Okumura-Hata, Longley-Rice) when appropriate.\n"
    "- Provide actionable engineering advice, not just theoretical explanations.\n"
    "- When comparing frequency bands, discuss trade-offs: range vs. capacity, "
    "penetration vs. bandwidth, Fresnel zone size vs. spectrum availability.\n"
    "- If the user provides batch analysis data, summarize trends, identify "
    "worst-performing links, and prioritize remediation.\n"
    "- Format responses with clear sections and bullet points for readability.\n"
    "- If asked about something outside telecom engineering, politely redirect "
    "the conversation."
)

# ── RAG Knowledge Base ──────────────────────────────────────────────
# Domain-specific knowledge fragments injected into prompts based on
# query content.  This is a lightweight RAG approach: keyword-matched
# retrieval of curated engineering knowledge, prepended to the user
# prompt so the LLM can ground its reasoning in accurate RF theory.
# ────────────────────────────────────────────────────────────────────

_RAG_KNOWLEDGE: List[Dict[str, Any]] = [
    {
        "keywords": ["fresnel", "clearance", "obstruction", "line of sight", "los"],
        "title": "Fresnel Zone Theory & Clearance Requirements",
        "content": (
            "The first Fresnel zone radius at a point along the path is: "
            "r1 = sqrt(λ·d1·d2 / (d1+d2)), where λ = c/f, d1 and d2 are distances "
            "from each end to the point.\n"
            "- 60% clearance of the first Fresnel zone is the MINIMUM for a reliable link "
            "(≈0 dB additional diffraction loss).\n"
            "- 100% clearance is ideal and provides true free-space propagation.\n"
            "- Below 40% clearance, expect 6-20 dB additional loss from knife-edge diffraction.\n"
            "- At 0% clearance (grazing), add ~6 dB loss.\n"
            "- Negative clearance (obstruction penetrating the zone) causes severe loss "
            "(20+ dB) and the link is likely unfeasible without a repeater.\n"
            "- The Fresnel zone is widest at midpath and narrows toward both ends.\n"
            "- Higher frequencies have SMALLER Fresnel zones (easier to clear) but more "
            "atmospheric attenuation.\n"
            "- Earth curvature adds an effective 'bulge' of d1·d2/(2·k·Re) metres, where "
            "k=4/3 (standard atmosphere) and Re=6371 km.\n"
            "REMEDIATION OPTIONS (ordered by cost):\n"
            "1. Raise antenna height (cheapest if structure allows)\n"
            "2. Move to higher frequency (smaller Fresnel zone, but check link budget)\n"
            "3. Add a mid-path repeater to split the link\n"
            "4. Use adaptive modulation to tolerate lower SNR"
        ),
    },
    {
        "keywords": ["signal", "strength", "dbm", "adequate", "fwa", "sensitivity", "snr"],
        "title": "Signal Strength Interpretation by Application",
        "content": (
            "Signal strength interpretation depends entirely on the APPLICATION:\n"
            "| Application | Min Signal | Typical Target | Notes |\n"
            "|---|---|---|---|\n"
            "| Voice (GSM) | -104 dBm | -85 dBm | Low data rate, robust coding |\n"
            "| 4G LTE Data | -100 dBm | -80 dBm | For 10+ Mbps throughput |\n"
            "| 5G NR Sub-6 | -95 dBm | -75 dBm | Higher modulation orders |\n"
            "| 5G mmWave | -85 dBm | -70 dBm | Very sensitive to obstructions |\n"
            "| FWA (Fixed Wireless Access) | -90 dBm | -75 dBm | Directional antenna helps |\n"
            "| SCADA/IoT Telemetry | -110 dBm | -95 dBm | Low data rate, high latency OK |\n"
            "| Microwave Backhaul | -75 dBm | -55 dBm | Requires high availability 99.999% |\n"
            "| Wi-Fi (2.4 GHz) | -80 dBm | -65 dBm | Indoor, short range |\n\n"
            "FADE MARGIN: Always design with 10-15 dB fade margin above receiver sensitivity "
            "to account for rain fade, multipath, and seasonal vegetation changes.\n"
            "LINK AVAILABILITY: A link designed for 99.99% availability needs ~10 dB more "
            "margin than one designed for 99.9%."
        ),
    },
    {
        "keywords": ["700", "3500", "frequency", "band", "compare", "mhz", "ghz", "spectrum"],
        "title": "Frequency Band Comparison for RF Planning",
        "content": (
            "BAND CHARACTERISTICS COMPARISON:\n\n"
            "700 MHz (Band 28 / n28):\n"
            "- Excellent propagation range (up to 30+ km rural LOS)\n"
            "- Good building/foliage penetration\n"
            "- First Fresnel zone radius at 10 km midpoint: ~32.8 m (LARGE)\n"
            "- Limited bandwidth (typically 10-20 MHz per operator)\n"
            "- Best for: rural coverage, IoT, baseline LTE coverage\n"
            "- FSPL at 10 km: 111.4 dB\n\n"
            "1800 MHz (Band 3 / n3):\n"
            "- Good balance of range and capacity\n"
            "- Fresnel zone radius at 10 km midpoint: ~19.8 m\n"
            "- Moderate building penetration\n"
            "- FSPL at 10 km: 119.5 dB\n\n"
            "2600 MHz (Band 7 / n7):\n"
            "- Higher capacity, moderate range\n"
            "- Fresnel zone radius at 10 km midpoint: ~16.4 m\n"
            "- FSPL at 10 km: 122.7 dB\n\n"
            "3500 MHz (Band n78 - 5G NR):\n"
            "- High capacity (up to 100 MHz bandwidth)\n"
            "- Limited range (typically 5-8 km practical)\n"
            "- First Fresnel zone radius at 5 km midpoint: ~10.3 m (easier to clear)\n"
            "- Poor foliage/building penetration\n"
            "- Best for: urban 5G, FWA in suburban areas\n"
            "- FSPL at 5 km: 119.3 dB\n\n"
            "KEY TRADE-OFF: Lower frequency = longer range + bigger Fresnel zone. "
            "Higher frequency = more capacity + smaller Fresnel zone but shorter range. "
            "A link with terrain obstructions may actually perform BETTER at 3500 MHz "
            "if the smaller Fresnel zone clears obstacles that 700 MHz cannot."
        ),
    },
    {
        "keywords": ["antenna", "height", "improve", "raise", "tower", "mast"],
        "title": "Antenna Height Engineering Guidelines",
        "content": (
            "Antenna height is often the most cost-effective way to improve a link:\n\n"
            "RULES OF THUMB:\n"
            "- Doubling antenna height gains approximately 6 dB in path loss reduction "
            "(Okumura-Hata model, urban/suburban).\n"
            "- For Fresnel zone clearance, the required height increase Δh can be "
            "estimated: Δh ≈ (required_clearance - current_clearance) × fresnel_radius "
            "at the obstruction point.\n"
            "- Earth curvature effect: at 20 km, the Earth 'bulge' at midpath is ~4.7 m "
            "(k=4/3). At 40 km it's ~18.8 m.\n"
            "- Typical tower heights: Rural macro = 30-60 m, Suburban = 20-35 m, "
            "Urban small cell = 6-15 m.\n"
            "- Receiver mast heights for FWA: 6-15 m is typical, must clear nearby "
            "buildings and trees.\n\n"
            "COST CONSIDERATIONS:\n"
            "- Self-supporting tower: ~$800-1500/m (steel, foundations, installation)\n"
            "- Guyed mast: ~$300-600/m (needs ground space for guy wires)\n"
            "- Rooftop mount: ~$5,000-15,000 fixed (structural survey required)\n"
            "- Each additional 10 m above 40 m increases wind loading significantly "
            "and may require structural reinforcement."
        ),
    },
    {
        "keywords": ["repeater", "relay", "hop", "chain", "multi-hop"],
        "title": "Repeater Chain Planning",
        "content": (
            "When a direct link is not feasible (signal below threshold or Fresnel "
            "blockage), a repeater chain can bridge the gap:\n\n"
            "DESIGN RULES:\n"
            "- Each hop adds latency (~0.1-0.5 ms for RF + processing).\n"
            "- Amplify-and-forward repeaters also amplify noise (avoid for >2 hops).\n"
            "- Decode-and-forward (regenerative) repeaters reset the noise floor but "
            "add 1-5 ms processing delay per hop.\n"
            "- Maximum practical hops: 3-4 for real-time voice/data, 6-8 for SCADA.\n"
            "- Each repeater site needs: power, backhaul, physical security.\n"
            "- Optimal repeater placement: at terrain high points with LOS to both "
            "adjacent nodes, NOT necessarily at the midpoint.\n"
            "- For batch analysis with many receivers, identify 'cluster centroids' "
            "where a single repeater serves multiple endpoints."
        ),
    },
    {
        "keywords": ["link budget", "fspl", "path loss", "propagation", "model"],
        "title": "Link Budget & Propagation Models",
        "content": (
            "LINK BUDGET EQUATION:\n"
            "Rx_Power (dBm) = Tx_Power + Tx_Gain + Rx_Gain - FSPL - Additional_Losses\n\n"
            "FREE-SPACE PATH LOSS (ITU-R P.525):\n"
            "FSPL (dB) = 20·log10(d) + 20·log10(f) - 147.55\n"
            "where d in metres, f in Hz.\n\n"
            "ADDITIONAL LOSSES TO CONSIDER:\n"
            "- Rain attenuation: significant above 10 GHz (ITU-R P.838)\n"
            "- Atmospheric absorption: O2 peak at 60 GHz, water vapour at 22 GHz\n"
            "- Foliage loss: 0.2-1.0 dB/m at UHF, up to 2-4 dB/m at 5 GHz\n"
            "- Diffraction loss: depends on Fresnel zone clearance (ITU-R P.526)\n"
            "- Body/building penetration: 10-25 dB depending on material and frequency\n"
            "- Cable/connector losses: 0.5-3 dB typical (depends on cable type and length)\n\n"
            "PROPAGATION MODELS:\n"
            "- Free-Space (ITU-R P.525): LOS only, baseline reference\n"
            "- Okumura-Hata: Urban/suburban/rural, 150-1500 MHz, 1-20 km\n"
            "- COST-231 Hata: Extension to 2 GHz\n"
            "- Longley-Rice (ITM): Terrain-based, 20 MHz - 20 GHz, irregular terrain\n"
            "- ITU-R P.526: Diffraction over terrain obstacles"
        ),
    },
    {
        "keywords": ["batch", "multiple", "points", "receivers", "csv", "summary"],
        "title": "Batch Analysis Interpretation Guide",
        "content": (
            "When analyzing multiple receiver points simultaneously:\n\n"
            "ANALYSIS APPROACH:\n"
            "1. COVERAGE CLASSIFICATION: Group points by signal quality:\n"
            "   - Excellent: > -70 dBm (high throughput, reliable)\n"
            "   - Good: -70 to -85 dBm (adequate for most services)\n"
            "   - Marginal: -85 to -95 dBm (voice/basic data only)\n"
            "   - Poor: -95 to -105 dBm (intermittent, IoT only)\n"
            "   - No coverage: < -105 dBm\n\n"
            "2. FRESNEL ANALYSIS: Identify links with <60% clearance as priority "
            "remediation targets.\n\n"
            "3. DISTANCE TRENDS: Plot signal vs. distance to identify whether "
            "terrain or distance is the limiting factor.\n\n"
            "4. REPEATER OPPORTUNITY: Links where LOS fails but distance is short "
            "(<10 km) are ideal candidates for a single mid-path repeater.\n\n"
            "5. PRIORITIZATION: Rank by population served, economic impact, or "
            "strategic importance — not just signal level."
        ),
    },
]


def _retrieve_rag_context(query: str) -> str:
    """
    Lightweight keyword-based RAG retrieval.
    Scans the query for domain keywords and returns matching knowledge
    fragments formatted for injection into the LLM prompt.
    """
    query_lower = query.lower()
    matched = []
    for fragment in _RAG_KNOWLEDGE:
        for kw in fragment["keywords"]:
            if kw in query_lower:
                matched.append(fragment)
                break

    if not matched:
        return ""

    parts = ["=== DOMAIN KNOWLEDGE (use this to ground your reasoning) ===\n"]
    for frag in matched:
        parts.append(f"### {frag['title']}\n{frag['content']}\n")
    parts.append("=== END DOMAIN KNOWLEDGE ===\n")
    return "\n".join(parts)


def _interpret_signal_quality(signal_dbm: float) -> str:
    """Return a human-readable quality label for a signal level."""
    if signal_dbm > -70:
        return "Excellent"
    if signal_dbm > -85:
        return "Good"
    if signal_dbm > -95:
        return "Marginal"
    if signal_dbm > -105:
        return "Poor"
    return "No coverage"


def _build_analysis_context(context_json: str) -> str:
    """
    Parse analysis context JSON and enrich it with semantic interpretation.
    Transforms raw numbers into engineering-meaningful annotations.
    """
    try:
        ctx = json.loads(context_json)
    except (json.JSONDecodeError, TypeError):
        return context_json  # Return as-is if not valid JSON

    enriched_parts = []

    # Enrich analysis result
    analysis = ctx.get("analysis")
    if analysis:
        signal = analysis.get("signal_dbm")
        fresnel = analysis.get("fresnel_clearance")
        distance = analysis.get("distance_km")
        los = analysis.get("los_ok")
        feasible = analysis.get("feasible")

        enriched_parts.append("=== LINK ANALYSIS RESULT ===")
        enriched_parts.append(json.dumps(analysis, indent=2))
        enriched_parts.append("\n--- AUTOMATED INTERPRETATION ---")

        if signal is not None:
            quality = _interpret_signal_quality(signal)
            enriched_parts.append(f"Signal Quality: {quality} ({signal:.1f} dBm)")
            if signal > -75:
                enriched_parts.append("→ Suitable for: 5G NR, FWA, microwave backhaul, all services")
            elif signal > -85:
                enriched_parts.append("→ Suitable for: 4G LTE, FWA, voice, basic 5G")
            elif signal > -95:
                enriched_parts.append("→ Suitable for: voice, IoT/SCADA, basic LTE")
                enriched_parts.append("→ NOT suitable for: high-throughput FWA, 5G NR")
            elif signal > -105:
                enriched_parts.append("→ Suitable for: narrowband IoT only (LoRa, NB-IoT)")
                enriched_parts.append("→ RECOMMENDATION: Add repeater or raise antenna")
            else:
                enriched_parts.append("→ Link is NOT feasible for any standard service")
                enriched_parts.append("→ RECOMMENDATION: Repeater chain required")

        if fresnel is not None:
            pct = fresnel * 100
            enriched_parts.append(f"Fresnel Clearance: {pct:.1f}% of first Fresnel zone")
            if fresnel >= 1.0:
                enriched_parts.append("→ Full clearance — true free-space propagation")
            elif fresnel >= 0.6:
                enriched_parts.append("→ Adequate clearance (≥60%) — no significant diffraction loss")
            elif fresnel >= 0.4:
                enriched_parts.append("→ Marginal clearance — expect 3-6 dB diffraction loss")
            elif fresnel >= 0.0:
                enriched_parts.append(f"→ Poor clearance — expect 6-20 dB diffraction loss")
                enriched_parts.append("→ RECOMMENDATION: Raise antenna or use higher frequency")
            else:
                enriched_parts.append("→ Obstruction penetrates Fresnel zone — severe loss (20+ dB)")
                enriched_parts.append("→ RECOMMENDATION: Mid-path repeater or significantly raise antenna")

        if distance is not None:
            enriched_parts.append(f"Distance: {distance:.2f} km")
            if distance > 20:
                enriched_parts.append("→ Long-range link — Earth curvature is significant")
                bulge = (distance * 500) ** 2 / (2 * 6371 * 1.33 * 1e6) * 1000
                enriched_parts.append(f"→ Midpath Earth bulge: ~{bulge:.1f} m (k=4/3)")

        if los is not None:
            enriched_parts.append(f"Line of Sight: {'Clear' if los else 'OBSTRUCTED'}")

        # Terrain profile stats
        terrain = analysis.get("terrain_profile")
        if terrain and isinstance(terrain, list) and len(terrain) > 2:
            min_elev = min(terrain)
            max_elev = max(terrain)
            avg_elev = sum(terrain) / len(terrain)
            enriched_parts.append(f"\nTerrain: min={min_elev:.0f}m, max={max_elev:.0f}m, "
                                  f"avg={avg_elev:.0f}m, range={max_elev - min_elev:.0f}m, "
                                  f"points={len(terrain)}")

    # Enrich tower info
    tower = ctx.get("tower")
    if tower:
        enriched_parts.append("\n=== TOWER INFORMATION ===")
        enriched_parts.append(json.dumps(tower, indent=2))
        freq = tower.get("frequency_mhz")
        if freq:
            enriched_parts.append(f"Operating frequency: {freq} MHz")
            if freq < 1000:
                enriched_parts.append("→ Sub-1GHz: excellent range, large Fresnel zone")
            elif freq < 3000:
                enriched_parts.append("→ Mid-band: balanced range/capacity")
            else:
                enriched_parts.append("→ High-band: high capacity, limited range, small Fresnel zone")

    # Batch results summary
    batch = ctx.get("batch_results")
    if batch and isinstance(batch, list):
        enriched_parts.append(f"\n=== BATCH ANALYSIS ({len(batch)} points) ===")
        signals = [r.get("signal_dbm") for r in batch if r.get("signal_dbm") is not None]
        fresnels = [r.get("fresnel_clearance") for r in batch if r.get("fresnel_clearance") is not None]
        distances = [r.get("distance_km") for r in batch if r.get("distance_km") is not None]
        feasible_count = sum(1 for r in batch if r.get("feasible"))
        los_ok_count = sum(1 for r in batch if r.get("los_ok"))

        enriched_parts.append(f"Feasible: {feasible_count}/{len(batch)} ({100*feasible_count/len(batch):.0f}%)")
        enriched_parts.append(f"LOS clear: {los_ok_count}/{len(batch)} ({100*los_ok_count/len(batch):.0f}%)")

        if signals:
            enriched_parts.append(f"Signal: min={min(signals):.1f}, max={max(signals):.1f}, "
                                  f"mean={sum(signals)/len(signals):.1f} dBm")
            # Coverage classification
            excellent = sum(1 for s in signals if s > -70)
            good = sum(1 for s in signals if -85 < s <= -70)
            marginal = sum(1 for s in signals if -95 < s <= -85)
            poor = sum(1 for s in signals if -105 < s <= -95)
            nocov = sum(1 for s in signals if s <= -105)
            enriched_parts.append(f"Coverage: Excellent={excellent}, Good={good}, "
                                  f"Marginal={marginal}, Poor={poor}, None={nocov}")

        if fresnels:
            below_60 = sum(1 for f in fresnels if f < 0.6)
            enriched_parts.append(f"Fresnel <60% clearance: {below_60}/{len(fresnels)} links "
                                  f"(these need attention)")

        if distances:
            enriched_parts.append(f"Distance: min={min(distances):.1f}, max={max(distances):.1f}, "
                                  f"mean={sum(distances)/len(distances):.1f} km")

        # Include individual results (truncated for token budget)
        enriched_parts.append("\nPer-link details:")
        for i, r in enumerate(batch[:50]):  # Cap at 50 to control token count
            sig = r.get("signal_dbm", "?")
            fc = r.get("fresnel_clearance", "?")
            dist = r.get("distance_km", "?")
            feas = "✓" if r.get("feasible") else "✗"
            enriched_parts.append(f"  [{i+1}] {feas} signal={sig} dBm, fresnel={fc}, dist={dist} km")
        if len(batch) > 50:
            enriched_parts.append(f"  ... and {len(batch) - 50} more points (truncated)")

    # Scenario comparison context
    scenarios = ctx.get("scenarios")
    if scenarios and isinstance(scenarios, list):
        enriched_parts.append("\n=== SCENARIO COMPARISON ===")
        for sc in scenarios:
            label = sc.get("label", "Scenario")
            enriched_parts.append(f"\n--- {label} ---")
            enriched_parts.append(json.dumps(sc, indent=2))

    return "\n".join(enriched_parts) if enriched_parts else context_json


def _get_client():
    """Lazy-init Bedrock Runtime client."""
    global _client
    if _client is None:
        _client = boto3.client(
            "bedrock-runtime",
            region_name=BEDROCK_REGION,
        )
    return _client


def _build_titan_body(prompt: str, max_tokens: int, temperature: float) -> dict:
    """Build request body for Amazon Titan Text models."""
    return {
        "inputText": prompt,
        "textGenerationConfig": {
            "maxTokenCount": max_tokens,
            "temperature": temperature,
            "topP": 0.9,
            "stopSequences": [],
        },
    }


def _build_claude_body(prompt: str, max_tokens: int, temperature: float) -> dict:
    """Build request body for Anthropic Claude models on Bedrock."""
    return {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
    }


def _build_llama_body(prompt: str, max_tokens: int, temperature: float) -> dict:
    """Build request body for Meta Llama models on Bedrock."""
    return {
        "prompt": f"<s>[INST] <<SYS>>\n{SYSTEM_PROMPT}\n<</SYS>>\n\n{prompt} [/INST]",
        "max_gen_len": max_tokens,
        "temperature": temperature,
        "top_p": 0.9,
    }


def _build_nova_body(prompt: str, max_tokens: int, temperature: float) -> dict:
    """Build request body for Amazon Nova models on Bedrock."""
    return {
        "schemaVersion": "messages-v1",
        "system": [{"text": SYSTEM_PROMPT}],
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
        "inferenceConfig": {
            "maxNewTokens": max_tokens,
            "temperature": temperature,
            "topP": 0.9,
        },
    }


def _build_request_body(
    model_id: str, prompt: str, max_tokens: int, temperature: float
) -> dict:
    """Route to the correct body builder based on model family."""
    mid = model_id.lower()
    if "claude" in mid or "anthropic" in mid:
        return _build_claude_body(prompt, max_tokens, temperature)
    if "llama" in mid or "meta" in mid:
        return _build_llama_body(prompt, max_tokens, temperature)
    if "nova" in mid:
        return _build_nova_body(prompt, max_tokens, temperature)
    # Default: Titan
    full_prompt = f"{SYSTEM_PROMPT}\n\nUser: {prompt}\n\nAssistant:"
    return _build_titan_body(full_prompt, max_tokens, temperature)


def _extract_response_text(model_id: str, response_body: dict) -> str:
    """Extract generated text from model-specific response format."""
    mid = model_id.lower()
    if "claude" in mid or "anthropic" in mid:
        # Messages API format
        content = response_body.get("content", [])
        return "".join(
            block.get("text", "") for block in content if block.get("type") == "text"
        )
    if "llama" in mid or "meta" in mid:
        return response_body.get("generation", "")
    if "nova" in mid:
        # Nova uses messages-v1 response format
        output = response_body.get("output", {})
        message = output.get("message", {})
        content = message.get("content", [])
        return "".join(block.get("text", "") for block in content)
    # Titan
    results = response_body.get("results", [])
    if results:
        return results[0].get("outputText", "")
    return response_body.get("outputText", "")


def invoke_model(
    prompt: str,
    model_id: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    context: Optional[str] = None,
) -> dict:
    """
    Invoke a Bedrock foundation model with the given prompt.

    Uses RAG context injection: domain-specific RF engineering knowledge is
    retrieved based on query keywords and prepended to the prompt so the LLM
    can reason accurately about Fresnel zones, signal levels, band trade-offs,
    and other telecom concepts.

    Args:
        prompt: User prompt / question.
        model_id: Override the default model ID.
        max_tokens: Override the default max tokens.
        temperature: Override the default temperature.
        context: Optional analysis context (e.g. JSON of a link analysis result)
                 that gets prepended to the prompt.

    Returns:
        dict with keys: response, model_id, input_tokens (estimate), output_tokens (estimate)
    """
    model = model_id or BEDROCK_MODEL_ID
    tokens = max_tokens or BEDROCK_MAX_TOKENS
    temp = temperature if temperature is not None else BEDROCK_TEMPERATURE

    # RAG: retrieve domain knowledge relevant to the user's query
    rag_context = _retrieve_rag_context(prompt)

    full_prompt = prompt
    if context:
        # Enrich raw context with semantic interpretation
        enriched = _build_analysis_context(context)
        full_prompt = (
            f"{enriched}\n\n"
            f"{rag_context}\n"
            f"User question: {prompt}"
        )
    elif rag_context:
        full_prompt = f"{rag_context}\nUser question: {prompt}"

    body = _build_request_body(model, full_prompt, tokens, temp)

    client = _get_client()
    try:
        response = client.invoke_model(
            modelId=model,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(body),
        )
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_msg = e.response["Error"]["Message"]
        logger.error("Bedrock invoke_model error: %s – %s", error_code, error_msg)
        raise

    response_body = json.loads(response["body"].read())
    text = _extract_response_text(model, response_body)

    # Token estimates from response metadata when available
    usage = response_body.get("usage", {})
    input_tokens = usage.get("input_tokens") or usage.get("inputTextTokenCount") or 0
    output_tokens = usage.get("output_tokens") or usage.get("totalTokenCount") or 0

    return {
        "response": text.strip(),
        "model_id": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


def list_available_models() -> list[dict]:
    """
    Return a curated list of Bedrock base models suitable for the playground.
    Queries the Bedrock control-plane API for available text models.
    Falls back to a static list if the API call fails.
    """
    static_models = [
        {
            "model_id": "amazon.nova-micro-v1:0",
            "provider": "Amazon",
            "name": "Nova Micro",
        },
        {
            "model_id": "amazon.nova-lite-v1:0",
            "provider": "Amazon",
            "name": "Nova Lite",
        },
        {
            "model_id": "amazon.nova-pro-v1:0",
            "provider": "Amazon",
            "name": "Nova Pro",
        },
        {
            "model_id": "anthropic.claude-haiku-4-5-20251001-v1:0",
            "provider": "Anthropic",
            "name": "Claude Haiku 4.5",
        },
        {
            "model_id": "anthropic.claude-sonnet-4-20250514-v1:0",
            "provider": "Anthropic",
            "name": "Claude Sonnet 4",
        },
    ]

    try:
        bedrock_ctrl = boto3.client("bedrock", region_name=BEDROCK_REGION)
        resp = bedrock_ctrl.list_foundation_models(
            byOutputModality="TEXT",
            byInferenceType="ON_DEMAND",
        )
        models = []
        for m in resp.get("modelSummaries", []):
            models.append(
                {
                    "model_id": m["modelId"],
                    "provider": m.get("providerName", "Unknown"),
                    "name": m.get("modelName", m["modelId"]),
                }
            )
        return models if models else static_models
    except Exception:
        logger.info("Could not list Bedrock models; returning static list")
        return static_models


# ── Scenario Comparison ─────────────────────────────────────────────

def compare_scenarios(
    scenarios: List[Dict[str, Any]],
    question: Optional[str] = None,
    model_id: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
) -> dict:
    """
    Compare multiple RF scenarios (e.g. 700 MHz vs 3500 MHz, different
    antenna heights) and return AI-generated engineering analysis.

    Each scenario dict should contain at minimum:
        - label: str (e.g. "700 MHz at 30m")
        - signal_dbm, fresnel_clearance, distance_km, los_ok, feasible

    Returns same format as invoke_model.
    """
    context = json.dumps({"scenarios": scenarios})
    default_question = (
        "Compare these RF scenarios in detail. For each scenario, assess signal "
        "quality, Fresnel zone clearance, and overall feasibility. Recommend "
        "which scenario is best and explain the engineering trade-offs. "
        "Consider both performance and practical deployment factors."
    )
    return invoke_model(
        prompt=question or default_question,
        model_id=model_id,
        max_tokens=max_tokens or 2048,
        temperature=temperature,
        context=context,
    )


def analyze_batch(
    batch_results: List[Dict[str, Any]],
    question: Optional[str] = None,
    model_id: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
) -> dict:
    """
    Analyze a batch of link analysis results and provide a consolidated
    AI-generated engineering summary with prioritized recommendations.

    Each result dict should contain:
        - signal_dbm, fresnel_clearance, distance_km, los_ok, feasible

    Returns same format as invoke_model.
    """
    context = json.dumps({"batch_results": batch_results})
    default_question = (
        "Analyze this batch of RF link results comprehensively. Provide:\n"
        "1. Overall coverage assessment (% feasible, signal distribution)\n"
        "2. Identification of the worst-performing links and why they fail\n"
        "3. Prioritized remediation recommendations (raise antenna, add repeater, "
        "change frequency)\n"
        "4. Estimated improvement if the top recommendation is implemented\n"
        "5. Summary table of coverage quality distribution"
    )
    return invoke_model(
        prompt=question or default_question,
        model_id=model_id,
        max_tokens=max_tokens or 2048,
        temperature=temperature,
        context=context,
    )


def suggest_antenna_height(
    analysis: Dict[str, Any],
    tower: Dict[str, Any],
    target_clearance: float = 0.6,
    model_id: Optional[str] = None,
) -> dict:
    """
    Given a link analysis result and tower info, ask the AI to calculate
    and recommend the optimal antenna height for the desired Fresnel
    zone clearance.
    """
    context = json.dumps({
        "analysis": analysis,
        "tower": tower,
        "target_fresnel_clearance": target_clearance,
    })
    prompt = (
        f"Based on the link analysis data and terrain profile provided, calculate "
        f"what antenna height is required to achieve at least {target_clearance*100:.0f}% "
        f"first Fresnel zone clearance. Consider:\n"
        f"1. Current antenna height and Fresnel clearance\n"
        f"2. The terrain profile and where the worst obstruction is\n"
        f"3. Earth curvature at this distance\n"
        f"4. Whether raising the TX antenna, RX antenna, or both is more effective\n"
        f"5. Practical height limits for the tower structure\n"
        f"Provide a specific height recommendation with engineering justification."
    )
    return invoke_model(
        prompt=prompt,
        model_id=model_id,
        max_tokens=2048,
        context=context,
    )
