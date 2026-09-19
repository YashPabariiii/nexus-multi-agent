"""End-to-end live demo of the Nexus pipeline against a running API.

Usage:
    python demo/run_demo.py [--api-base http://127.0.0.1:8099] [--depth standard]

Requires the API + Celery worker to be up with real GROQ_API_KEY / GEMINI_API_KEY /
TAVILY_API_KEY configured (see RUNBOOK.md) — this hits live LLM/search providers.
"""

import argparse
import json
import sys
import time
from datetime import datetime

import requests
import websocket

DEMO_TOPIC = "Anthropic vs OpenAI competitive landscape 2025"
POLL_TIMEOUT_SECONDS = 30 * 60


def register_and_login(api_base: str) -> tuple[str, str]:
    email = f"demo-{int(time.time())}@example.com"
    reg = requests.post(f"{api_base}/v1/auth/register", json={"name": "Demo", "email": email})
    reg.raise_for_status()
    api_key = reg.json()["api_key"]
    print(f"[1/9] Registered tenant {reg.json()['tenant_id']} — api_key={api_key[:12]}...")

    tok = requests.post(f"{api_base}/v1/auth/token", json={"api_key": api_key})
    tok.raise_for_status()
    return tok.json()["access_token"], email


def preview(api_base: str, token: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.post(
        f"{api_base}/v1/briefs/preview",
        headers=headers,
        json={"topic": DEMO_TOPIC, "scope": "market share, product velocity, funding, enterprise adoption", "domain": "tech"},
    )
    resp.raise_for_status()
    data = resp.json()
    print(f"[2/9] Preview — complexity={data['complexity']} estimated={data['estimated_minutes']}min related_briefs={data['related_briefs_found']}")
    for q in data["key_research_questions"]:
        print(f"        - {q}")


def create_brief(api_base: str, token: str, email: str, depth: str) -> str:
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.post(
        f"{api_base}/v1/briefs",
        headers=headers,
        json={
            "topic": DEMO_TOPIC,
            "scope": "market share, product velocity, funding, enterprise adoption",
            "domain": "tech",
            "depth": depth,
            "audience": "executive",
            "consent_store": True,
            "email": email,
        },
    )
    resp.raise_for_status()
    brief_id = resp.json()["brief_id"]
    print(f"[3/9] Brief created: {brief_id} (depth={depth})")
    return brief_id


def stream_events(ws_base: str, token: str, brief_id: str) -> None:
    print("[4/9] Streaming live agent events:")
    ws = websocket.WebSocket()
    ws.settimeout(10)
    ws.connect(f"{ws_base}/v1/briefs/{brief_id}/stream?token={token}")

    while True:
        try:
            raw = ws.recv()
        except websocket.WebSocketTimeoutException:
            continue
        if not raw:
            break
        event = json.loads(raw)
        event_type = event.get("event_type")
        if event_type == "heartbeat":
            continue

        agent = event.get("agent_type", "")
        message = event.get("message", "")
        provider = "groq" if agent in ("supervisor", "web_search", "domain_knowledge", "fact_checker", "writer") else "gemini"

        if event_type == "claim_disputed":
            print(f"  [DISPUTE] {message}")
        elif event_type == "debate_resolved":
            print(f"  [DEBATE] {message}")
        elif event_type in ("human_review", "awaiting_review"):
            print(f"  [REVIEW] {message}")
            break
        else:
            print(f"  [{agent.upper()}] [{provider.upper()}] {message}")

        if event_type in ("brief_complete", "brief_failed"):
            break

    ws.close()


def approve_if_awaiting(api_base: str, token: str, brief_id: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    brief = requests.get(f"{api_base}/v1/briefs/{brief_id}", headers=headers).json()
    if brief["status"] != "awaiting_review":
        return

    claims = requests.get(f"{api_base}/v1/briefs/{brief_id}/claims", headers=headers).json()
    verified = sum(1 for c in claims if c.get("verified"))
    disputed = sum(1 for c in claims if c.get("disputed_by_agent"))
    print(f"[5/9] Claim stats — total={len(claims)} verified={verified} disputed={disputed}")

    print("[6/9] Auto-approving (demo mode)...")
    requests.post(f"{api_base}/v1/briefs/{brief_id}/resume", headers=headers, json={"action": "approve"}).raise_for_status()


def poll_until_complete(api_base: str, token: str, brief_id: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    print("[7/9] Polling until complete...")
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    while time.time() < deadline:
        brief = requests.get(f"{api_base}/v1/briefs/{brief_id}", headers=headers).json()
        if brief["status"] == "complete":
            return brief
        if brief["status"] == "failed":
            print(f"Brief failed: {brief.get('error_message')}")
            sys.exit(1)
        time.sleep(5)
    print("Timed out waiting for completion.")
    sys.exit(1)


def print_report_and_stats(api_base: str, token: str, brief_id: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    report = requests.get(f"{api_base}/v1/briefs/{brief_id}/report/download/json", headers=headers).json()

    print(f"\n[8/9] {report['title']}")
    print(f"Overall confidence: {report['overall_confidence']:.0%}")
    print(report["executive_summary"])

    pdf = requests.get(f"{api_base}/v1/briefs/{brief_id}/report/download/pdf", headers=headers)
    pdf.raise_for_status()
    out_path = "demo_report.pdf"
    with open(out_path, "wb") as f:
        f.write(pdf.content)
    print(f"\n[9/9] Saved report -> {out_path} ({len(pdf.content):,} bytes)")

    traces = requests.get(f"{api_base}/v1/briefs/{brief_id}/traces", headers=headers).json()
    claims = requests.get(f"{api_base}/v1/briefs/{brief_id}/claims", headers=headers).json()

    verified = sum(1 for c in claims if c.get("verified"))
    disputed = sum(1 for c in claims if c.get("disputed_by_agent"))
    debate_rounds = sum(1 for t in traces if t["agent_type"].endswith("_debate_verdict"))
    groq_tokens = sum((t.get("prompt_tokens") or 0) + (t.get("completion_tokens") or 0) for t in traces if t["llm_provider"] == "groq")
    gemini_tokens = sum((t.get("prompt_tokens") or 0) + (t.get("completion_tokens") or 0) for t in traces if t["llm_provider"] == "gemini")

    print("\n--- Run stats ---")
    print(f"Agents run:      {len({t['agent_type'] for t in traces})}")
    print(f"Claims:          {len(claims)} (verified={verified}, disputed={disputed})")
    print(f"Debate rounds:   {debate_rounds}")
    print(f"Confidence:      {report['overall_confidence']:.0%}")
    print(f"Groq tokens:     {groq_tokens:,}")
    print(f"Gemini tokens:   {gemini_tokens:,}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", default="http://127.0.0.1:8099")
    parser.add_argument("--depth", default="standard", choices=["quick", "standard", "deep"])
    args = parser.parse_args()

    ws_base = args.api_base.replace("http://", "ws://").replace("https://", "wss://")

    start = datetime.now()
    token, email = register_and_login(args.api_base)
    preview(args.api_base, token)
    brief_id = create_brief(args.api_base, token, email, args.depth)
    stream_events(ws_base, token, brief_id)
    approve_if_awaiting(args.api_base, token, brief_id)
    poll_until_complete(args.api_base, token, brief_id)
    print_report_and_stats(args.api_base, token, brief_id)
    print(f"\nTotal wall time: {(datetime.now() - start).total_seconds():.0f}s")


if __name__ == "__main__":
    main()
