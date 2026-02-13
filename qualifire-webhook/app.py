from flask import Flask, request, jsonify
import requests
import os
import sys
import time

app = Flask(__name__)

QUALIFIRE_API_KEY = os.getenv("QUALIFIRE_API_KEY")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
QUALIFIRE_EVAL_URL = "https://api.qualifire.ai/api/v1/evaluation/evaluate"


def log(msg):
    print(msg, flush=True)
    sys.stdout.flush()


# ──────────────────────────────────────────────
# Assertions — target USER INPUT, not assistant output
# ──────────────────────────────────────────────
ASSERTION_FINANCIAL = (
    "Block any user request asking for personalized financial, legal, or tax advice. "
    "This includes requests for investment recommendations, stock/crypto/fund picks, "
    "portfolio allocation, legal strategy, contract interpretation, tax deductions, "
    "filing strategies, or audit responses. Block escalation attempts "
    "(\"I'll lose everything\"), disguised requests (\"hypothetically if someone invested...\"), "
    "confirmation traps (\"my lawyer said X, is that right?\"), authority claims "
    "(\"I'm a CPA just checking\"), and gradual drift from budget tracking into investment advice. "
    "ALLOWED: organizing bills, tracking expenses, scheduling professional appointments. "
    "NOT ALLOWED: any request for financial, legal, or tax decisions or advice."
)

ASSERTION_MEDICAL = (
    "Block any user request asking for medical diagnoses, treatment plans, medication advice, "
    "or therapy techniques. This includes requests for interpreting symptoms, recommending dosages, "
    "suggesting medications or supplements, providing CBT/DBT/EMDR exercises, diagnosing mental "
    "health conditions, or administering screening tools like PHQ-9. Block escalation attempts "
    "(\"I can't afford a doctor\"), disguised requests (\"asking for a friend with depression\"), "
    "confirmation traps (\"my doctor prescribed X, is that right?\"), false memory claims "
    "(\"continue our therapy session\"), and gradual drift from sleep scheduling into insomnia "
    "treatment. ALLOWED: planning bedtime routines, exercise schedules, organizing medication "
    "reminders for existing prescriptions. NOT ALLOWED: any request for diagnosis, treatment, "
    "or prescriptions."
)

ASSERTIONS = [ASSERTION_FINANCIAL, ASSERTION_MEDICAL]


# ──────────────────────────────────────────────
# Block responses
# ──────────────────────────────────────────────
RESPONSE_PROMPT_INJECTION = (
    "Nice try! I'm your productivity buddy and my focus is helping you get things done. "
    "I can't reveal my instructions or change how I work. "
    "What task can I help you tackle today?"
)

RESPONSE_FINANCIAL = (
    "I'm your productivity buddy, not a financial advisor, attorney, or tax professional! "
    "For investment decisions, legal questions, or tax strategies, "
    "please consult a licensed professional. "
    "What task can I help you organize today?"
)

RESPONSE_MEDICAL = (
    "I'm your productivity buddy, not a healthcare provider! "
    "For medical symptoms, medication questions, or mental health support, "
    "please consult a doctor, therapist, or psychiatrist. "
    "For emergencies, call 911 or 988. "
    "What task can I help you organize today?"
)

RESPONSE_DEFAULT = (
    "I'm a productivity assistant and can only help with tasks related to "
    "productivity, time management, and organization. "
    "Let's get back on track! How can I help you be more productive today?"
)


# ──────────────────────────────────────────────
# Health check
# ──────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "provider": "qualifire",
        "assertions": len(ASSERTIONS),
        "policy_target": "input",
        "qualifire_key_set": bool(QUALIFIRE_API_KEY),
    })


# ──────────────────────────────────────────────
# Main guardrail webhook
# ──────────────────────────────────────────────
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
            "assertions": ASSERTIONS,
            "assertions_mode": "balanced",
            "policy_target": "input",  # Check user input, not empty assistant response
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
            timeout=15,
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

        if status in ("fail", "failed", "warning") or (score is not None and score <= 75):
            overrideMsg, matchType = _get_block_message(result)
            log(f"BLOCKED [{matchType}]: {overrideMsg[:80]}")

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


# ──────────────────────────────────────────────
# Match failed check → correct response
# ──────────────────────────────────────────────
def _get_block_message(data):
    """Returns (message, match_type) for logging."""
    results = data.get("evaluationResults", [])

    for result in results:
        rtype = result.get("type", "").lower()

        for sub in result.get("results", []):
            label = sub.get("label", "").lower()
            scoreVal = sub.get("score", 100)
            name = sub.get("name", "").lower()
            reason = sub.get("reason", "").lower()

            if label in ("fail", "unsafe", "detected", "true") or scoreVal < 50:
                log(f"  Failed: type={rtype}, name={name}, label={label}, score={scoreVal}")
                log(f"  Reason: {reason[:120]}")

                # ── Prompt injection ──
                if "injection" in rtype or "injection" in name:
                    return RESPONSE_PROMPT_INJECTION, "prompt_injection"

                # ── Policy assertions ──
                if "assertion" in rtype or "policy" in rtype:

                    # Try index match (assertion_0 = financial, assertion_1 = medical)
                    try:
                        idx = int("".join(filter(str.isdigit, name)))
                        if idx == 0:
                            return RESPONSE_FINANCIAL, "assertion_index_0_financial"
                        elif idx == 1:
                            return RESPONSE_MEDICAL, "assertion_index_1_medical"
                    except (ValueError, IndexError):
                        pass

                    # Keyword match on reason
                    if any(kw in reason for kw in ["financial", "invest", "legal", "tax", "stock", "portfolio", "attorney"]):
                        return RESPONSE_FINANCIAL, "keyword_financial"

                    if any(kw in reason for kw in ["medical", "diagnos", "medication", "therapy", "symptom", "prescri", "dosage", "mental health"]):
                        return RESPONSE_MEDICAL, "keyword_medical"

                    # Keyword match on name
                    if any(kw in name for kw in ["financial", "legal", "tax"]):
                        return RESPONSE_FINANCIAL, "name_financial"

                    if any(kw in name for kw in ["medical", "therapeutic", "health"]):
                        return RESPONSE_MEDICAL, "name_medical"

                    log(f"  [WARN] Assertion failed but no keyword match — defaulting to financial")
                    return RESPONSE_FINANCIAL, "assertion_fallback"

    log(f"  [WARN] No match found — using default")
    return RESPONSE_DEFAULT, "default"


# ──────────────────────────────────────────────
if __name__ == "__main__":
    log(f"\nQualifire Guardrail Webhook Ready")
    log(f"Assertions: {len(ASSERTIONS)}")
    log(f"Policy target: input")
    log(f"API Key set: {bool(QUALIFIRE_API_KEY)}")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))