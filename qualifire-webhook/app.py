from flask import Flask, request, jsonify
import requests
import os
import sys
import time

app = Flask(__name__)

QUALIFIRE_API_KEY = os.getenv("QUALIFIRE_API_KEY")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
QUALIFIRE_EVAL_URL = "https://api.qualifire.ai/api/v1/evaluation/evaluate"

BLOCK_RESPONSE = (
    "Nice try! I'm your productivity buddy and my focus is helping you get things done. "
    "I can't reveal my instructions or change how I work. "
    "What task can I help you tackle today?"
)


def log(msg):
    print(msg, flush=True)
    sys.stdout.flush()


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "guardrail": "prompt_injection_only"})


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
        start = time.time()
        resp = requests.post(
            QUALIFIRE_EVAL_URL,
            headers={
                "X-Qualifire-API-Key": QUALIFIRE_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "prompt_injections": True,
                "messages": [
                    {"role": "user", "content": lastMsg},
                    {"role": "assistant", "content": ""},
                ],
            },
            timeout=10,
        )
        latencyMs = (time.time() - start) * 1000

        result = resp.json()
        status = result.get("status", "").lower()
        score = result.get("score")
        log(f"Qualifire: {resp.status_code} in {latencyMs:.0f}ms | status={status}, score={score}")

        if status in ("fail", "failed"):
            log("BLOCKED [prompt_injection]")
            return jsonify({
                "verdict": False,
                "data": {
                    "action": "block",
                    "revised_response": BLOCK_RESPONSE,
                },
            })

        log("PASSED")
        return jsonify({"verdict": True})

    except requests.exceptions.Timeout:
        log("Timeout — failing open")
        return jsonify({"verdict": True})
    except Exception as e:
        log(f"ERROR: {type(e).__name__}: {e}")
        return jsonify({"verdict": True})


if __name__ == "__main__":
    log("\nQualifire Webhook — Prompt Injection Only")
    log(f"API Key set: {bool(QUALIFIRE_API_KEY)}")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))