from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
import sys
from pathlib import Path
from typing import Any

from .contracts import ProtocolError
from .public_data import (
    PublicDataError,
    build_public_repository,
    export_three_subjects,
    validate_public_repository,
)
from .public_sync import PublicSyncService
from .review import OpenAICompatibleReviewer
from .service import KnowledgeService


class CliUsageError(ValueError):
    pass


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliUsageError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(prog="pks")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("learn", "query"):
        command = commands.add_parser(name)
        command.add_argument("--vault", required=True)
        payload = command.add_mutually_exclusive_group(required=True)
        payload.add_argument("--json")
        payload.add_argument("--input")
    for name in ("rebuild", "repair"):
        command = commands.add_parser(name)
        command.add_argument("--vault", required=True)
    attach = commands.add_parser("attach")
    attach.add_argument("--vault", required=True)
    attach.add_argument("--repository-id", required=True)
    attach.add_argument("--root", required=True)
    attach.add_argument("--subject", required=True)
    attach.add_argument("--adapter", required=True)
    for name in ("scan", "import"):
        command = commands.add_parser(name)
        command.add_argument("--vault", required=True)
        command.add_argument("--repository-id", required=True)
    public_build = commands.add_parser("public-build")
    public_build.add_argument("--root", required=True)
    public_validate = commands.add_parser("public-validate")
    public_validate.add_argument("--root", required=True)
    public_validate.add_argument("--check-dist", action="store_true")
    public_export = commands.add_parser("public-export")
    public_export.add_argument("--output", required=True)
    public_export.add_argument("--math-root", required=True)
    public_export.add_argument("--chinese-root", required=True)
    public_export.add_argument("--english-root", required=True)
    public_sync = commands.add_parser("public-sync")
    public_sync.add_argument("--vault", required=True)
    public_sync.add_argument("--repository-id", required=True)
    public_sync.add_argument("--url", required=True)
    public_sync.add_argument("--branch", default="main")
    return parser


def _read_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.json is not None:
        raw = args.json
    elif args.input == "-":
        buffer = getattr(sys.stdin, "buffer", None)
        raw = (
            buffer.read().decode("utf-8-sig")
            if buffer is not None
            else sys.stdin.read()
        )
    else:
        raw = Path(args.input).read_text(encoding="utf-8-sig")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ProtocolError("INVALID_REQUEST", "payload must be a JSON object")
    return value


def _write_json(stream: Any, value: dict[str, Any]) -> None:
    encoded = (
        json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")
    buffer = getattr(stream, "buffer", None)
    if buffer is not None:
        buffer.write(encoded)
        buffer.flush()
        return
    stream.write(encoded.decode("utf-8"))
    stream.flush()


def _dispatch(service: KnowledgeService, args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "learn":
        return service.learn(_read_payload(args))
    if args.command == "query":
        return service.query(_read_payload(args))
    if args.command == "attach":
        return service.attach_repository(
            args.repository_id,
            args.root,
            args.subject,
            args.adapter,
        )
    if args.command == "scan":
        return service.scan_repository(args.repository_id)
    if args.command == "import":
        return service.import_repository(args.repository_id)
    if args.command == "rebuild":
        return service.rebuild()
    return service.repair()


def _dispatch_public(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "public-sync":
        return PublicSyncService(
            Path(args.vault), _configured_model_reviewer()
        ).sync(
            args.repository_id,
            args.url,
            branch=args.branch,
        )
    if args.command == "public-build":
        return build_public_repository(Path(args.root))
    if args.command == "public-validate":
        findings = validate_public_repository(
            Path(args.root), check_dist=args.check_dist
        )
        return {
            "status": "PASS" if not findings else "FAIL",
            "findings": [asdict(item) for item in findings],
        }
    return export_three_subjects(
        Path(args.output),
        {
            "math": (Path(args.math_root), "math-compiled-v1"),
            "chinese": (Path(args.chinese_root), "chinese-compiled-v1"),
            "english": (Path(args.english_root), "english-runtime-v1"),
        },
    )


def _configured_model_reviewer() -> OpenAICompatibleReviewer | None:
    endpoint = os.environ.get("PKS_REVIEW_ENDPOINT")
    model = os.environ.get("PKS_REVIEW_MODEL")
    if not endpoint and not model:
        return None
    if not endpoint or not model:
        raise CliUsageError(
            "PKS_REVIEW_ENDPOINT and PKS_REVIEW_MODEL must be configured together"
        )
    raw_timeout = os.environ.get("PKS_REVIEW_TIMEOUT_SECONDS", "30")
    try:
        timeout = float(raw_timeout)
    except ValueError as exc:
        raise CliUsageError(
            "PKS_REVIEW_TIMEOUT_SECONDS must be a positive number"
        ) from exc
    if timeout <= 0:
        raise CliUsageError(
            "PKS_REVIEW_TIMEOUT_SECONDS must be a positive number"
        )
    return OpenAICompatibleReviewer(
        endpoint=endpoint,
        api_key=os.environ.get("PKS_REVIEW_API_KEY"),
        model=model,
        timeout=timeout,
    )


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        if args.command.startswith("public-"):
            result = _dispatch_public(args)
        else:
            result = _dispatch(KnowledgeService(Path(args.vault)), args)
    except ProtocolError as exc:
        error = {"errorCode": exc.code, "message": exc.message}
        _write_json(sys.stderr, error)
        return 2
    except (
        CliUsageError,
        PublicDataError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        OSError,
    ) as exc:
        error = {"errorCode": "CLI_INPUT_ERROR", "message": str(exc)}
        _write_json(sys.stderr, error)
        return 2
    _write_json(sys.stdout, result)
    return 1 if result.get("status") == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
