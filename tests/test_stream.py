"""The live streamer Track B tails."""
import json
from pathlib import Path

from backend.stream import stream

SCRATCH = Path("/private/tmp/claude-501/-Users-ericcho-Documents-dnhacks/487d6a6d-9b17-4600-903b-c9aabc72ab5f/scratchpad")


def test_stream_appends_every_record_intact(tmp_path):
    src = tmp_path / "src.jsonl"
    recs = [{"timestamp": f"t{i}", "confidence": i / 10} for i in range(20)]
    src.write_text("".join(json.dumps(r) + "\n" for r in recs))
    dest = tmp_path / "live.jsonl"
    n = stream(src, dest, rate=500.0)
    assert n == 20
    got = [json.loads(l) for l in dest.read_text().splitlines()]
    assert got == recs


def test_rerun_truncates_the_hard_reset_path(tmp_path):
    src = tmp_path / "src.jsonl"
    src.write_text('{"a": 1}\n{"a": 2}\n')
    dest = tmp_path / "live.jsonl"
    stream(src, dest, rate=500.0)
    stream(src, dest, rate=500.0, count=1)
    assert dest.read_text() == '{"a": 1}\n'


def test_slice_streams_the_requested_window(tmp_path):
    src = tmp_path / "src.jsonl"
    src.write_text("".join(f'{{"i": {i}}}\n' for i in range(10)))
    dest = tmp_path / "live.jsonl"
    n = stream(src, dest, rate=500.0, start=4, count=3)
    assert n == 3
    assert [json.loads(l)["i"] for l in dest.read_text().splitlines()] == [4, 5, 6]
