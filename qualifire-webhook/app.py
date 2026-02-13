from flask import Flask, request, jsonify
import requests
import os
import sys
import time

app = Flask(__name__)

QUALIFIRE_API_KEY = os.getenv("QUALIFIRE_API_KEY")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
QUALIFIRE_EVAL_URL = "https://proxy.qualifire.ai/api/evaluation/evaluate"


def log(msg):
    print(msg, flush=True)
    sys.stdout.flush()


FALLBACK_RESPONSES = {
    "prompt_injection": (
        "Nice try! I'm your productivity buddy and my focus is helping you get things done. "
        "I can't reveal my instructions or change how I work. "
        "What task can I help you tackle today?"
    ),
    "financial": (
        "I'm your productivity buddy, not a financial advisor, attorney, or tax professional! "
        "For investment decisions, legal questions, or tax strategies, "
        "please consult a licensed professional. "
        "What task can I help you organize today?"
    ),
    "medical": (
        "I'm your productivity buddy, not a healthcare provider! "
        "For medical symptoms, medication questions, or mental health support, "
        "please consult a doctor, therapist, or psychiatrist. "
        "For emergencies, call 911 or 988. "
        "What task can I help you organize today?"
    ),
    "default": (
        "I'm a productivity assistant and can only help with tasks related to "
        "productivity, time management, and organization. "
        "Let's get back on track! How can I help you be more productive today?"
    ),
}

TYPE_MAP = {
    "prompt_injection": "prompt_injection",
    "prompt_injections": "prompt_injection",
    "injection": "prompt_injection",
    "financial": "financial",
    "legal": "financial",
    "tax": "financial",
    "medical": "medical",
    "therapeutic": "medical",
    "mental": "medical",
    "health": "medical",
}


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "provider": "qualifire",
        "guardrails": ["prompt_injection", "financial_tax_legal", "medical"],
        "qualifire_key_set": bool(QUALIFIRE_API_KEY),
    })


@app.route("/guardrail", methods=["POST"])
def guardrail_webhook():
    log("=== QUALIFIRE WEBHOOK CALLED ===")

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
        log(f"status={status}, score={score}, keys={list(result.keys())}")

        if status in ("fail", "failed") or (score is not None and score <= 50):
            overrideMsg = _extractQualifireResponse(result)

            if not overrideMsg:
                violation = _identifyViolation(result)
                overrideMsg = FALLBACK_RESPONSES.get(violation, FALLBACK_RESPONSES["default"])
                log(f"BLOCKED ({violation})")
            else:
                log(f"BLOCKED (qualifire response)")

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


def _extractQualifireResponse(data):
    for key in ["default_response", "defaultResponse", "response", "message",
                "revised_response", "revisedResponse", "blocked_response"]:
        val = data.get(key)
        if val and isinstance(val, str) and val.strip():
            return val.strip()

    for actionKey in ["action", "actions", "guardrail_action"]:
        action = data.get(actionKey, {})
        if isinstance(action, dict):
            for key in ["default_response", "defaultResponse", "response", "message"]:
                val = action.get(key)
                if val and isinstance(val, str) and val.strip():
                    return val.strip()

    for result in data.get("evaluationResults", []):
        for key in ["default_response", "defaultResponse", "response", "message"]:
            val = result.get(key)
            if val and isinstance(val, str) and val.strip():
                return val.strip()

    return None


def _identifyViolation(data):
    results = data.get("evaluationResults", [])

    for result in results:
        rtype = result.get("type", "").lower()
        rname = result.get("name", "").lower()

        for sub in result.get("results", []):
            label = sub.get("label", "").lower()
            scoreVal = sub.get("score", 100)

            if label in ("fail", "unsafe", "detected", "true") or scoreVal < 50:
                for keySource in [rtype, rname]:
                    for pattern, responseKey in TYPE_MAP.items():
                        if pattern in keySource:
                            return responseKey

    return "default"


if __name__ == "__main__":
    log("\nQualifire Guardrail Webhook Ready")
    log(f"Guardrails: prompt injection, Financial/tax/legal, Medical")
    log(f"API Key set: {bool(QUALIFIRE_API_KEY)}")
    log(f"Webhook secret set: {bool(WEBHOOK_SECRET)}")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
