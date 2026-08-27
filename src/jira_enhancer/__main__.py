from __future__ import annotations

import argparse
import json
from pathlib import Path

from .api import serve
from .bootstrap import build_orchestrator
from .codex_orchestrator import serve_codex_orchestrator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Jira Enhancer MVP CLI")
    parser.add_argument("--storage-root", default="./runtime", help="Runtime root directory")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--scope-type", required=True, choices=["issue", "sprint", "epic", "release"])
    run_parser.add_argument("--scope-value", required=True)
    run_parser.add_argument("--requestor", required=True)

    show_parser = subparsers.add_parser("show-run")
    show_parser.add_argument("--run-id", required=True)

    approval_parser = subparsers.add_parser("approve")
    approval_parser.add_argument("--run-id", required=True)
    approval_parser.add_argument("--issue-key", required=True)
    approval_parser.add_argument("--decision", required=True, choices=["approve", "reject", "regenerate", "user_input", "prompt"])
    approval_parser.add_argument("--reviewer", required=True)
    approval_parser.add_argument("--reviewer-notes", default="")
    approval_parser.add_argument("--user-input", default="")

    writeback_parser = subparsers.add_parser("writeback")
    writeback_parser.add_argument("--run-id", required=True)
    writeback_parser.add_argument("--issue-key", required=True)

    replay_parser = subparsers.add_parser("replay")
    replay_parser.add_argument("--run-id", required=True)

    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8080)

    codex_parser = subparsers.add_parser("serve-codex-orchestrator")
    codex_parser.add_argument("--host", default="127.0.0.1")
    codex_parser.add_argument("--port", type=int, default=8090)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "serve":
        serve(args.storage_root, args.host, args.port)
        return
    if args.command == "serve-codex-orchestrator":
        serve_codex_orchestrator(args.storage_root, args.host, args.port)
        return
    orchestrator = build_orchestrator(Path(args.storage_root))
    if args.command == "run":
        result = orchestrator.create_run(args.scope_type, args.scope_value, args.requestor)
    elif args.command == "show-run":
        result = orchestrator.get_run(args.run_id)
    elif args.command == "approve":
        result = orchestrator.record_approval(
            args.run_id,
            args.issue_key,
            reviewer=args.reviewer,
            decision=args.decision,
            reviewer_notes=args.reviewer_notes,
            user_input={"details": args.user_input} if getattr(args, "user_input", "").strip() else None,
        )
    elif args.command == "writeback":
        result = orchestrator.writeback(args.run_id, args.issue_key)
    else:
        result = orchestrator.replay(args.run_id)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
