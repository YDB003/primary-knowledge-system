from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

from pks.cli import main
from pks.service import KnowledgeService


def learn_request() -> dict:
    return {
        "callerId": "cli-agent",
        "requestId": "request-1",
        "schemaVersion": "1.0",
        "query": "What is a fraction?",
        "candidate": {
            "title": "Fraction",
            "answer": "A fraction represents a part of a whole.",
            "aliases": [],
        },
        "sources": [],
        "context": {"subject": "math", "actualStudyGrade": 3},
    }


def test_cli_learn_and_query_json_round_trip(tmp_path: Path, capsys) -> None:
    code = main(
        ["learn", "--vault", str(tmp_path), "--json", json.dumps(learn_request())]
    )
    learned = json.loads(capsys.readouterr().out)

    query = {
        "schemaVersion": "1.0",
        "mode": "search",
        "query": "Fraction",
        "filters": {},
    }
    query_code = main(
        ["query", "--vault", str(tmp_path), "--json", json.dumps(query)]
    )
    queried = json.loads(capsys.readouterr().out)

    assert code == 0
    assert query_code == 0
    assert learned["decision"] == "CREATED"
    assert queried["results"][0]["entityId"] == learned["entityId"]


def test_cli_reads_payload_file_and_runs_rebuild(tmp_path: Path, capsys) -> None:
    payload_path = tmp_path / "learn.json"
    payload_path.write_text(json.dumps(learn_request()), encoding="utf-8")

    assert main(["learn", "--vault", str(tmp_path), "--input", str(payload_path)]) == 0
    capsys.readouterr()
    assert main(["rebuild", "--vault", str(tmp_path)]) == 0
    report = json.loads(capsys.readouterr().out)

    assert report["indexStatus"] == "CURRENT"
    assert report["materializationStatus"] == "COMPLETE"


def test_cli_accepts_windows_powershell_utf8_bom_file(tmp_path: Path, capsys) -> None:
    payload_path = tmp_path / "powershell-learn.json"
    payload_path.write_bytes(
        b"\xef\xbb\xbf" + json.dumps(learn_request(), ensure_ascii=False).encode("utf-8")
    )

    code = main(["learn", "--vault", str(tmp_path), "--input", str(payload_path)])
    learned = json.loads(capsys.readouterr().out)

    assert code == 0
    assert learned["decision"] == "CREATED"


def test_cli_protocol_error_is_json_and_nonzero(tmp_path: Path, capsys) -> None:
    bad = {"schemaVersion": "2.0", "mode": "search", "query": "x", "filters": {}}

    code = main(["query", "--vault", str(tmp_path), "--json", json.dumps(bad)])
    captured = capsys.readouterr()

    assert code == 2
    assert captured.out == ""
    assert json.loads(captured.err)["errorCode"] == "UNSUPPORTED_SCHEMA_VERSION"


def test_cli_rejects_both_json_and_input(tmp_path: Path) -> None:
    code = main(
        [
            "learn",
            "--vault",
            str(tmp_path),
            "--json",
            "{}",
            "--input",
            "payload.json",
        ]
    )

    assert code == 2


def test_cli_decodes_utf8_stdin_independent_of_console_encoding(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    request = learn_request()
    request["query"] = "风"
    request["candidate"]["title"] = "风"
    request["candidate"]["answer"] = "自然界空气流动形成的现象。"
    request["context"]["subject"] = "chinese"
    KnowledgeService(tmp_path).learn(request)

    query = {
        "schemaVersion": "1.0",
        "mode": "search",
        "query": "风",
        "filters": {"subject": "chinese"},
    }
    stdin = io.TextIOWrapper(
        io.BytesIO(json.dumps(query, ensure_ascii=False).encode("utf-8")),
        encoding="gbk",
    )
    monkeypatch.setattr(sys, "stdin", stdin)

    code = main(["query", "--vault", str(tmp_path), "--input", "-"])
    response = json.loads(capsys.readouterr().out)

    assert code == 0
    assert response["status"] == "OK"
    assert response["results"][0]["title"] == "风"


def test_cli_writes_utf8_json_independent_of_console_encoding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    request = learn_request()
    request["query"] = "IPA long vowel"
    request["candidate"]["title"] = "IPA long vowel"
    request["candidate"]["answer"] = "The length mark is ː."
    request["context"]["subject"] = "english"
    KnowledgeService(tmp_path).learn(request)
    query = {
        "schemaVersion": "1.0",
        "mode": "search",
        "query": "IPA long vowel",
        "filters": {"subject": "english"},
    }
    output_bytes = io.BytesIO()
    stdout = io.TextIOWrapper(output_bytes, encoding="gbk")
    monkeypatch.setattr(sys, "stdout", stdout)

    code = main(
        ["query", "--vault", str(tmp_path), "--json", json.dumps(query)]
    )
    stdout.flush()
    response = json.loads(output_bytes.getvalue().decode("utf-8"))

    assert code == 0
    assert response["results"][0]["answer"] == "The length mark is ː."


def test_cli_utf8_round_trip_across_real_subprocess(tmp_path: Path) -> None:
    request = learn_request()
    request["query"] = "风"
    request["candidate"]["title"] = "风"
    request["candidate"]["answer"] = "自然界空气流动形成的现象。"
    request["context"]["subject"] = "chinese"
    KnowledgeService(tmp_path).learn(request)
    query = {
        "schemaVersion": "1.0",
        "mode": "search",
        "query": "风",
        "filters": {"subject": "chinese"},
    }
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pks",
            "query",
            "--vault",
            str(tmp_path),
            "--input",
            "-",
        ],
        input=json.dumps(query, ensure_ascii=False).encode("utf-8"),
        capture_output=True,
        check=False,
        env=environment,
        timeout=30,
    )
    response = json.loads(result.stdout.decode("utf-8"))

    assert result.returncode == 0
    assert response["results"][0]["title"] == "风"
