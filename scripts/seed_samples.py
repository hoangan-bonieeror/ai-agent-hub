"""Load the sample agents and workflows into a running Hub through its API.

python scripts/seed_samples.py                     # OpenAI agents (needs OPENAI_API_KEY)
python scripts/seed_samples.py --runtime fake      # offline: API needs ENABLE_FAKE_RUNTIME=true

Agents that already exist are left as they are. Each run adds a new version
of the sample workflows.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.samples import SAMPLE_AGENTS, SAMPLE_WORKFLOWS


def post(base_url: str, path: str, body: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Actor": "seed-script"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--runtime", default="openai_agents", choices=["openai_agents", "fake"])
    parser.add_argument("--model", default="gpt-4.1-mini")
    args = parser.parse_args()
    api = f"{args.base_url.rstrip('/')}/api/v1"

    for agent in SAMPLE_AGENTS:
        code, body = post(
            api,
            "/agents",
            {
                "slug": agent.id,
                "name": agent.name,
                "description": agent.description,
                "runtime": args.runtime,
                "model": args.model,
                "instructions": agent.instructions,
                "tools": agent.tools,
                "capabilities": agent.capabilities,
                "limits": {
                    "max_steps": agent.limits.max_steps,
                    "timeout_seconds": int(agent.limits.timeout_seconds),
                },
            },
        )
        state = {201: "created", 409: "already exists"}.get(code, f"error {code}: {body}")
        print(f"agent {agent.id}: {state}")

    for workflow in SAMPLE_WORKFLOWS.values():
        code, body = post(api, "/workflows", workflow.model_dump(mode="json", exclude={"version"}))
        if code == 201:
            print(f"workflow {workflow.name}: version {body['version']}")
        else:
            print(f"workflow {workflow.name}: error {code}: {body}")


if __name__ == "__main__":
    main()
