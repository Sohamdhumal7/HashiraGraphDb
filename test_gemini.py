"""Optional Gemini API smoke test; graph development does not depend on Gemini."""

from __future__ import annotations

import sys

from graph_store import load_configuration


def main() -> int:
    config = load_configuration()
    api_key = config.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("Gemini is not configured. Add GEMINI_API_KEY to .env to test it.")
        print("HashiraGraph's FalkorDB milestone can run without an LLM.")
        return 0

    model = config.get("GEMINI_MODEL", "").strip() or "gemini-3.8-flash"
    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents="Reply with exactly this text: HashiraGraph Gemini connection successful.",
        )
        response_text = (response.text or "").strip()
        if not response_text:
            raise RuntimeError("The model returned an empty text response")
        print(f"Gemini API request succeeded using model {model}.")
        print(f"Model response: {response_text}")
        return 0
    except Exception as exc:
        print(f"Gemini API test failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "Check the API key in .env, the selected model name, network access, "
            "and your Google AI Studio API quota.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
