from flask import Flask, request, jsonify
import requests
import os
import sys
import time

app = Flask(__name__)

QUALIFIRE_API_KEY = os.getenv("QUALIFIRE_API_KEY")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
QUALIFIRE_EVAL_URL = "https://api.qualifire.ai/api/v1/evaluation/evaluate"
QUALIFIRE_GUARDRAILS_URL = "https://proxy.qualifire.ai/api/guardrails"

# Populated on startup from Qualifire UI config
ASSERTIONS = []
DEFAULT_RESPONSES = {}
PROMPT_INJECTION_RESPONSE = ""
GUARDRAILS_SOURCE = "none"  # "api" or "fallback"


def log(msg):
    print(msg, flush=True)
    sys.stdout.flush()


def load_guardrails():
    """Fetch guardrails from Qualifire API and extract assertions + default_responses."""
    global ASSERTIONS, DEFAULT_RESPONSES, PROMPT_INJECTION_RESPONSE, GUARDRAILS_SOURCE

    log("=== LOADING GUARDRAILS FROM QUALIFIRE API ===")

    try:
        resp = requests.get(
            QUALIFIRE_GUARDRAILS_URL,
            headers={
                "X-Qualifire-API-Key": QUALIFIRE_API_KEY,
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        log(f"Guardrails API: {resp.status_code}")

        if resp.status_code != 200:
            log(f"[FALLBACK] Failed to load guardrails: {resp.status_code} — using hardcoded fallbacks")
            _use_fallbacks()
            return

        guardrails = resp.json()
        log(f"[API] Raw response type: {type(guardrails)}")
        log(f"[API] Raw response: {str(guardrails)[:500]}")

        # Handle both list and dict responses
        if isinstance(guardrails, dict):
            guardrails = guardrails.get("guardrails", guardrails.get("data", []))
        if not isinstance(guardrails, list):
            guardrails = [guardrails]

        log(f"[API] Found {len(guardrails)} guardrails")

        for g in guardrails:
            name = g.get("name", "").lower()
            active = g.get("active", False)

            if not active:
                log(f"  [API] Skipping inactive: {name}")
                continue

            # Extract default_response from actions
            actions = g.get("actions", {})
            default_resp = actions.get("default_response", "")

            # Extract assertion text from evaluations
            evals = g.get("evaluations", {})
            req_eval = evals.get("request_evaluation", "")
            resp_eval = evals.get("response_evaluation", "")

            log(f"  [API] Guardrail: {name}")
            log(f"    [API] keys: {list(g.keys())}")
            log(f"    [API] actions keys: {list(actions.keys()) if actions else 'none'}")
            log(f"    [API] evals keys: {list(evals.keys()) if evals else 'none'}")
            log(f"    [API] default_response: {default_resp[:80] if default_resp else 'EMPTY'}")
            log(f"    [API] request_eval: {req_eval[:80] if req_eval else 'EMPTY'}")
            log(f"    [API] response_eval: {resp_eval[:80] if resp_eval else 'EMPTY'}")

            # Prompt injection — no assertion needed, handled by prompt_injections flag
            if "prompt" in name and "injection" in name:
                if default_resp:
                    PROMPT_INJECTION_RESPONSE = default_resp
                    log(f"    [API] ✓ Prompt injection response loaded from API")
                else:
                    log(f"    [API] ✗ Prompt injection has no default_response")
                continue

            # Custom policy guardrails — extract assertion text
            assertion_text = req_eval or resp_eval
            if assertion_text:
                ASSERTIONS.append(assertion_text)
                if default_resp:
                    DEFAULT_RESPONSES[assertion_text] = default_resp
                    log(f"    [API] ✓ Assertion + response loaded")
                else:
                    log(f"    [API] ✗ Assertion loaded but NO default_response")
            else:
                log(f"    [API] ✗ No assertion text found for: {name}")

        # Check if we got anything useful
        if ASSERTIONS:
            GUARDRAILS_SOURCE = "api"
            log(f"\n[API] ✓ SUCCESS — Loaded from Qualifire API")
            log(f"[API]   Assertions: {len(ASSERTIONS)}")
            log(f"[API]   Responses: {len(DEFAULT_RESPONSES)}")
            log(f"[API]   Prompt injection response: {'YES' if PROMPT_INJECTION_RESPONSE else 'NO'}")
        else:
            log(f"\n[FALLBACK] No assertions extracted from API — using hardcoded fallbacks")
            _use_fallbacks()

    except Exception as e:
        log(f"[FALLBACK] Error loading guardrails: {e} — using hardcoded fallbacks")
        import traceback
        traceback.print_exc()
        _use_fallbacks()


def _use_fallbacks():
    """Hardcoded fallbacks in case API fetch fails."""
    global ASSERTIONS, DEFAULT_RESPONSES, PROMPT_INJECTION_RESPONSE, GUARDRAILS_SOURCE

    GUARDRAILS_SOURCE = "fallback"

    ASSERTIONS = [
        "The assistant must never provide personalized financial, legal, or tax advice including investment recommendations, stock/crypto/fund picks, portfolio allocation, legal strategy, contract interpretation, tax deductions, filing strategies, or audit responses.",
        "The assistant must never provide medical diagnoses, treatment plans, medication advice, or therapy techniques including interpreting symptoms, recommending dosages, suggesting medications or supplements, providing CBT/DBT/EMDR exercises, or diagnosing mental health conditions.",
    ]

    PROMPT_INJECTION_RESPONSE = (
        "Nice try! I'm your productivity buddy and my focus is helping you get things done. "
        "I can't reveal my instructions or change how I work. "
        "What task can I help you tackle today?"
    )

    DEFAULT_RESPONSES = {
        ASSERTIONS[0]: (
            "I'm your productivity buddy, not a financial advisor, attorney, or tax professional! "
            "For investment decisions, legal questions, or tax strategies, "
            "please consult a licensed professional. "
            "What task can I help you organize today?"
        ),
        ASSERTIONS[1]: (
            "I'm your productivity buddy, not a healthcare provider! "
            "For medical symptoms, medication questions, or mental health support, "
            "please consult a doctor, therapist, or psychiatrist. "
            "For emergencies, call 911 or 988. "
            "What task can I help you organize today?"
        ),
    }

    log(f"[FALLBACK] ⚠ Using hardcoded assertions and responses")
    log(f"[FALLBACK]   Assertions: {len(ASSERTIONS)}")
    log(f"[FALLBACK]   Responses: {len(DEFAULT_RESPONSES)}")


FALLBACK_DEFAULT = (
    "I'm a productivity assistant and can only help with tasks related to "
    "productivity, time management, and organization. "
    "Let's get back on track! How can I help you be more productive today?"
)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "provider": "qualifire",
        "guardrails_source": GUARDRAILS_SOURCE,  # "api" or "fallback"
        "assertions_loaded": len(ASSERTIONS),
        "responses_loaded": len(DEFAULT_RESPONSES),
        "prompt_injection_response_set": bool(PROMPT_INJECTION_RESPONSE),
        "qualifire_key_set": bool(QUALIFIRE_API_KEY),
    })


@app.route("/reload", methods=["POST"])
def reload_guardrails():
    """Hot-reload guardrails from Qualifire UI without restarting."""
    load_guardrails()
    return jsonify({
        "status": "reloaded",
        "guardrails_source": GUARDRAILS_SOURCE,
        "assertions": len(ASSERTIONS),
        "responses": len(DEFAULT_RESPONSES),
    })


@app.route("/guardrail", methods=["POST"])
def guardrail_webhook():
    log(f"=== QUALIFIRE WEBHOOK CALLED [source={GUARDRAILS_SOURCE}] ===")

    auth = request.headers.get("Authorization", "")
    if WEBHOOK_SECRET and f"Bearer {WEBHOOK_SECRET}" != auth:
        log("Auth failed")
        return jsonify({"verdict": True})

    data = request.json or {}
    requestData = data.get("request", {}).get("json", {})
    messages = requestData.get("messages", [])

    if not messages:
        log("No messages — passing through")
        return jsonify({"verdict": True})

    lastMsg = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            lastMsg = msg.get("content", "")
            break
    if not lastMsg:
        lastMsg = messages[-1].get("content", "")

    log(f"Checking: {lastMsg[:100]}")

    try:
        payload = {
            "prompt_injections": True,
            "assertions": ASSERTIONS,
            "assertions_mode": "balanced",
            "policy_target": "both",
            "messages": [
                {"role": "user", "content": lastMsg},
                {"role": "assistant", "content": ""},
            ],
        }

        start = time.time()
        resp = requests.post(
            QUALIFIRE_EVAL_URL,
            headers={
                "X-Qualifire-API-Key": QUALIFIRE_API_KEY,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=10,
        )
        latencyMs = (time.time() - start) * 1000
        log(f"Qualifire: {resp.status_code} in {latencyMs:.0f}ms")

        if resp.status_code != 200:
            log(f"HTTP {resp.status_code} — failing open")
            return jsonify({"verdict": True})

        result = resp.json()
        status = result.get("status", "").lower()
        score = result.get("score")
        log(f"status={status}, score={score}")

        if status in ("fail", "failed") or (score is not None and score <= 50):
            overrideMsg, matchSource = _get_block_message(result)
            log(f"BLOCKED [{GUARDRAILS_SOURCE}→{matchSource}]: {overrideMsg[:80]}")

            return jsonify({
                "verdict": False,
                "data": {
                    "action": "block",
                    "revised_response": overrideMsg,
                },
            })

        log("PASSED")
        return jsonify({"verdict": True})

    except requests.exceptions.Timeout:
        log("Timeout — failing open")
        return jsonify({"verdict": True})
    except Exception as e:
        log(f"ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"verdict": True})


def _get_block_message(data):
    """Match failed evaluation back to the correct default_response.
    Returns (message, source) where source indicates how it was matched."""
    results = data.get("evaluationResults", [])

    for result in results:
        rtype = result.get("type", "").lower()

        for sub in result.get("results", []):
            label = sub.get("label", "").lower()
            scoreVal = sub.get("score", 100)
            name = sub.get("name", "")

            if label in ("fail", "unsafe", "detected", "true") or scoreVal < 50:
                log(f"  Failed check: type={rtype}, name={name}, label={label}, score={scoreVal}")

                # Prompt injection
                if "injection" in rtype or "injection" in name.lower():
                    if PROMPT_INJECTION_RESPONSE:
                        return PROMPT_INJECTION_RESPONSE, "prompt_injection"
                    log(f"  [WARN] Prompt injection detected but no response configured")

                # Policy assertion — match by assertion text or name
                if "assertion" in rtype or "policy" in rtype:
                    # Try matching by assertion name/text
                    for assertion, response in DEFAULT_RESPONSES.items():
                        if name and name.lower() in assertion.lower():
                            return response, "assertion_name_match"
                        if assertion[:30].lower() in name.lower():
                            return response, "assertion_prefix_match"

                    # Try matching by index if name contains a number
                    try:
                        idx = int("".join(filter(str.isdigit, name))) - 1
                        if 0 <= idx < len(ASSERTIONS) and ASSERTIONS[idx] in DEFAULT_RESPONSES:
                            return DEFAULT_RESPONSES[ASSERTIONS[idx]], f"assertion_index_{idx}"
                    except (ValueError, IndexError):
                        pass

                    # Try keyword matching on reason
                    reason = sub.get("reason", "").lower()
                    log(f"  Trying keyword match on reason: {reason[:100]}")
                    for assertion, response in DEFAULT_RESPONSES.items():
                        if any(kw in reason for kw in ["financial", "invest", "legal", "tax"]):
                            if "financial" in assertion.lower() or "legal" in assertion.lower():
                                return response, "keyword_financial"
                        if any(kw in reason for kw in ["medical", "diagnos", "medication", "therapy"]):
                            if "medical" in assertion.lower() or "diagnos" in assertion.lower():
                                return response, "keyword_medical"

                    log(f"  [WARN] Assertion failed but could not match to a response")

    # Generic fallback
    log(f"  [WARN] No specific match — using generic fallback")
    return FALLBACK_DEFAULT, "generic_fallback"


# Load guardrails on startup
load_guardrails()


if __name__ == "__main__":
    log(f"\nQualifire Guardrail Webhook Ready")
    log(f"Source: {GUARDRAILS_SOURCE}")
    log(f"Assertions: {len(ASSERTIONS)}")
    log(f"Responses: {len(DEFAULT_RESPONSES)}")
    log(f"API Key set: {bool(QUALIFIRE_API_KEY)}")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))