"""Run the shipped frontend in Node with an offline DOM/network boundary."""

import json
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ShellParser(HTMLParser):
    """Retain the actual shell's controls, attributes and initial options."""

    VOID = (
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    )

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = {"tag": "document", "attrs": {}, "children": []}
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attrs": dict(attrs), "children": []}
        self.stack[-1]["children"].append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1]["children"].append(data)


def run_frontend(scenario):
    node = shutil.which("node")
    assert node, "Node is required to execute the real frontend runtime tests"
    parser = ShellParser()
    parser.feed((ROOT / "app/static/index.html").read_text(encoding="utf-8"))
    result = subprocess.run(
        [
            node,
            str(ROOT / "tests/frontend_runtime.cjs"),
            str(ROOT / "app/static/app.js"),
            scenario,
        ],
        input=json.dumps(parser.root),
        text=True,
        capture_output=True,
        timeout=20,
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"PASS {scenario}" in result.stdout


def test_saved_voicebox_populates_connections_without_cloud_key_controls():
    run_frontend("saved_voicebox")


def test_provider_and_endpoint_changes_reset_unsaved_voice_credentials_and_choices():
    run_frontend("switch_provider")


def test_voicebox_actions_send_selected_provider_and_never_send_a_stale_cloud_key():
    run_frontend("voicebox_actions")


def test_stale_voice_responses_cannot_replace_a_new_provider_or_endpoint_selection():
    run_frontend("stale_responses")


def test_serialized_voice_saves_leave_voicebox_and_no_cloud_key_persisted():
    run_frontend("serialized_voice_saves")


def test_voice_save_queue_recovers_after_a_failed_write():
    run_frontend("voice_save_after_failure")


def test_clear_key_writes_share_save_order_and_only_the_latest_acknowledges_the_ui():
    run_frontend("clear_key_write_ordering")


def test_preview_after_an_endpoint_edit_requires_a_current_voice_before_requesting():
    run_frontend("preview_requires_current_voice")


def test_elevenlabs_regional_endpoints_keys_clear_and_preview_still_work():
    run_frontend("elevenlabs_actions")


def test_voicebox_failures_stay_local_and_do_not_fall_back_to_cloud():
    run_frontend("voicebox_errors")
