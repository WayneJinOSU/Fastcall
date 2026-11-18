#!/usr/bin/env python3
import argparse
import datetime
import json
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / "deploy/.env.compose"
TERMINAL_TASK_STATUSES = {"completed", "cancelled", "failed"}


def read_env(path):
    values = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip().replace("$$", "$")
    return values


def api_request(base_url, token, method, path, payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{path}", data=body, method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise RuntimeError(f"Open API {method} {path} returned {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Open API {method} {path} failed: {error.reason}") from error


def parse_arguments(config):
    parser = argparse.ArgumentParser(
        description="Create and start a normal outbound task for a registered SIP App."
    )
    parser.add_argument("--robot-code", required=True, help="Published Robot app code")
    parser.add_argument(
        "--phone", default=f"+{config.get('FREESWITCH_LOCAL_USER', '8615908280801')}",
        help="E.164 phone number registered by the SIP App",
    )
    parser.add_argument("--variables", default="{}", help="Contact variables as a JSON object")
    parser.add_argument("--customer-task-id")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    arguments = parser.parse_args()
    try:
        arguments.variables = json.loads(arguments.variables)
    except json.JSONDecodeError as error:
        parser.error(f"--variables must be valid JSON: {error}")
    if not isinstance(arguments.variables, dict):
        parser.error("--variables must be a JSON object")
    if arguments.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    if arguments.customer_task_id and not re.fullmatch(
        r"[A-Za-z0-9._-]+", arguments.customer_task_id
    ):
        parser.error("--customer-task-id may contain only letters, digits, '.', '_' and '-'")
    return arguments


def main():
    config = read_env(ENV_FILE) if ENV_FILE.exists() else {}
    arguments = parse_arguments(config)
    if not ENV_FILE.exists():
        raise RuntimeError("deploy/.env.compose is missing; run scripts/bootstrap_compose.sh first")
    token = config.get("FASTCALL_OPENAPI_TOKEN", "")
    if not token:
        raise RuntimeError("FASTCALL_OPENAPI_TOKEN is missing from deploy/.env.compose")
    base_url = f"http://localhost:{config.get('FASTCALL_HTTP_PORT', '8080')}"
    customer_task_id = arguments.customer_task_id or (
        "sip-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    )
    task = api_request(base_url, token, "PUT", f"/openapi/v1/outbound-tasks/{customer_task_id}", {
        "name": "SIP App verification",
        "robot_code": arguments.robot_code,
        "timezone": "Asia/Shanghai",
        "default_country_code": "86",
        "max_concurrent_calls": 1,
        "calls_per_minute": 1,
        "retry_policy": {
            "max_retries": 0,
            "retry_intervals_sec": [],
            "retry_on": [],
        },
    })
    task_id = task["task_id"]
    console_url = f"{base_url}/console/tasks/{task_id}"
    print(f"Task: {task_id}")
    print(f"Console: {console_url}")
    imported = api_request(
        base_url, token, "POST", f"/openapi/v1/outbound-tasks/{task_id}/contacts",
        {"contacts": [{
            "customer_contact_id": "sip-app",
            "phone": arguments.phone,
            "variables": arguments.variables,
        }]},
    )
    if not imported["accepted_contacts"]:
        raise RuntimeError(f"The SIP contact was not accepted: {imported['duplicate_contacts']}")
    started = api_request(
        base_url, token, "POST", f"/openapi/v1/outbound-tasks/{task_id}/start",
        {"expected_state_version": task["state_version"]},
    )
    print(f"Task status: {started['status']}; answer the SIP App when it rings.")

    deadline = time.monotonic() + arguments.timeout_seconds
    seen_calls = set()
    while time.monotonic() < deadline:
        current = api_request(
            base_url, token, "GET", f"/openapi/v1/outbound-tasks/{task_id}"
        )
        calls = api_request(
            base_url, token, "GET", f"/openapi/v1/outbound-tasks/{task_id}/calls"
        )["items"]
        for call in calls:
            signature = (call["call_id"], call["status"], call.get("result_code"))
            if signature not in seen_calls:
                seen_calls.add(signature)
                print(
                    f"Call {call['call_id']}: {call['status']} "
                    f"{call.get('result_code') or ''}".rstrip()
                )
                print(f"Call detail: {base_url}/console/calls/{call['call_id']}")
        if current["status"] in TERMINAL_TASK_STATUSES:
            print(f"Final task status: {current['status']}")
            succeeded = current["status"] == "completed" and any(
                call["status"] == "succeeded" for call in calls
            )
            return 0 if succeeded else 1
        time.sleep(2)
    print(f"Timed out waiting for task completion; inspect {console_url}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
