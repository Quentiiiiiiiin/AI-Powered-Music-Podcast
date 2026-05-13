"""One-off LLM connectivity check; uses load_settings() (.env + config.yaml). Run from repo root."""
from __future__ import annotations

import argparse

from podcast_ai.infra.config import load_settings
from podcast_ai.infra.llm_client import OpenAICompatibleLLMClient
from podcast_ai.modules.theme.agent_response_schemas import (
    build_openrouter_response_format,
    build_script_writer_response_schema,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ping OpenRouter-compatible chat/completions.")
    parser.add_argument(
        "--structured-smoke",
        action="store_true",
        help="Also call with minimal json_schema strict (isolates structured-output path).",
    )
    parser.add_argument(
        "--structured-script-writer",
        action="store_true",
        help="Call with real Script Writer schema (segment_count=1); closer to stage-1 agents.",
    )
    args = parser.parse_args()

    s = load_settings()
    key = (s.llm.api_key or "").strip()
    print("base_url:", s.llm.base_url)
    print("model:", s.llm.model)
    print("api_key_present:", bool(key))
    print("timeout:", s.llm.timeout_seconds, "max_retries:", s.llm.max_retries)
    if not key:
        raise SystemExit(
            "Missing LLM api_key: set PODCAST_AI_LLM__API_KEY in .env or llm.api_key in config.yaml"
        )
    client = OpenAICompatibleLLMClient(s.llm)
    text = client.generate(
        [{"role": "user", "content": "Reply with exactly one word: OK"}],
        temperature=0,
    )
    print("simple_chat reply_preview:", repr(text[:200]))

    if args.structured_smoke:
        tiny = {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        }
        rf = build_openrouter_response_format("ping_structured", tiny)
        t2 = client.generate(
            [{"role": "user", "content": 'Return JSON with answer "hi".'}],
            temperature=0,
            response_format=rf,
        )
        print("structured_smoke reply_preview:", repr(t2[:300]))

    if args.structured_script_writer:
        rf = build_openrouter_response_format(
            "podcast_script_writer_response",
            build_script_writer_response_schema(1),
        )
        t3 = client.generate(
            [
                {
                    "role": "user",
                    "content": (
                        "You are filling script for one segment. Return strict JSON only. "
                        "segments[0].script.segment_intro: one short Chinese sentence. "
                        "segments[0].script.between_tracks: empty array []."
                    ),
                }
            ],
            temperature=0,
            response_format=rf,
        )
        print("script_writer_schema reply_preview:", repr(t3[:400]))


if __name__ == "__main__":
    main()
