from __future__ import annotations

import argparse
import hashlib
import html
import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(ROOT))

from scripts.verify_demo_video import verify_video_delivery  # noqa: E402

_VIDEO_NAME = "driftproof-demo.mp4"
_TRANSCRIPT_NAME = "driftproof-demo-transcript.md"
_STORYBOARD_NAME = "driftproof-demo-storyboard.json"
_SOURCE_MANIFEST_NAME = "driftproof-demo-source-manifest.json"
_SCENE_DURATIONS_NAME = "driftproof-demo-scene-durations.json"
_VERIFICATION_NAME = "driftproof-demo-verification.json"
_FONT = Path("/usr/share/fonts/TTF/DejaVuSans.ttf")
_FONT_BOLD = Path("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf")


class VideoRenderError(RuntimeError):
    """Raised when an evidence-derived solution video cannot be rendered safely."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise VideoRenderError(f"required video evidence is missing or unsafe: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VideoRenderError(f"invalid JSON video evidence: {path}") from exc
    if not isinstance(value, dict):
        raise VideoRenderError(f"video evidence must be a JSON object: {path}")
    return value


def _run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        argv,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise VideoRenderError(
            f"command failed ({' '.join(argv)}): {(completed.stderr or completed.stdout)[-4000:]}"
        )
    return completed


def _git(root: Path, *args: str) -> str:
    return _run(["git", "-C", str(root), *args], timeout=120).stdout.strip()


def _tool_version(tool: str) -> str:
    name = Path(tool).name
    completed = _run([tool, "-version"] if name in {"ffmpeg", "ffprobe"} else [tool, "--version"])
    return (completed.stdout or completed.stderr).splitlines()[0]


def _first_available(*names: str) -> str | None:
    for name in names:
        path = shutil.which(name)
        if path is not None:
            return path
    return None


def video_readiness(
    root: Path = ROOT,
    *,
    expected_commit: str | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    selected = {
        "ffmpeg": _first_available("ffmpeg"),
        "ffprobe": _first_available("ffprobe"),
        "speech": _first_available("espeak-ng", "espeak"),
        "image": _first_available("magick", "convert"),
        "git": _first_available("git"),
    }
    problems = [f"missing tool: {name}" for name, value in selected.items() if value is None]
    for font in (_FONT, _FONT_BOLD):
        if font.is_symlink() or not font.is_file():
            problems.append(f"missing regular font: {font}")

    source_commit: str | None = None
    worktree_clean = False
    if selected["git"] is not None:
        try:
            source_commit = _git(root, "rev-parse", "HEAD")
            worktree_clean = not bool(_git(root, "status", "--porcelain=v1"))
        except VideoRenderError as exc:
            problems.append(str(exc))
    if not worktree_clean:
        problems.append("video source worktree must be clean")
    if expected_commit is not None and source_commit != expected_commit:
        problems.append(
            f"source commit differs from expected commit: {source_commit} != {expected_commit}"
        )

    return {
        "schema_version": 1,
        "protocol": "driftproof.demo-video-readiness.v1",
        "ready": not problems,
        "source_commit": source_commit,
        "expected_commit": expected_commit,
        "worktree_clean": worktree_clean,
        "selected_tools": selected,
        "fonts": [str(_FONT), str(_FONT_BOLD)],
        "problems": problems,
        "render_argv": [
            "python",
            "scripts/render_demo_video.py",
            "--output",
            "release/video",
            "--expected-commit",
            source_commit or "<commit>",
        ],
        "candidate_code_executed": False,
        "files_created": False,
        "human_approval_required": True,
        "consequential_action_taken": False,
    }


def _percentage(value: float) -> str:
    return f"{100.0 * value:.1f}%"


def _metrics(root: Path) -> dict[str, Any]:
    comparison = _load_object(root / "results/driftproof-comparison/comparison.json")
    try:
        baseline = comparison["baseline"]
        advanced = comparison["advanced"]
        cases = int(advanced["cases"])
        safe_total = int(advanced["safe_class"]["tp"]) + int(advanced["safe_class"]["fn"])
        unsafe_total = int(advanced["unsafe_class"]["tp"]) + int(advanced["unsafe_class"]["fn"])
        metrics = {
            "cases": cases,
            "safe_total": safe_total,
            "unsafe_total": unsafe_total,
            "baseline_macro_f1": float(baseline["safe_approval_macro_f1"]),
            "advanced_macro_f1": float(advanced["safe_approval_macro_f1"]),
            "baseline_accuracy": float(baseline["accuracy"]),
            "advanced_accuracy": float(advanced["accuracy"]),
            "baseline_escape": float(baseline["unsafe_repair_escape_rate"]),
            "advanced_escape": float(advanced["unsafe_repair_escape_rate"]),
            "safe_approved": int(advanced["safe_class"]["tp"]),
            "unsafe_blocked": int(advanced["unsafe_class"]["tp"]),
            "human_reviews": round(float(advanced["human_review_rate"]) * cases),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise VideoRenderError(
            "comparison evidence does not match the expected metric contract"
        ) from exc
    if safe_total + unsafe_total != cases or metrics["advanced_escape"] != 0.0:
        raise VideoRenderError("comparison evidence failed class-count or safety invariants")
    return metrics


def build_video_plan(root: Path = ROOT, *, source_commit: str | None = None) -> dict[str, Any]:
    root = root.resolve()
    commit = source_commit or _git(root, "rev-parse", "HEAD")
    metrics = _metrics(root)
    scenes = [
        {
            "index": 1,
            "title": "The problem: green builds can still be wrong",
            "kicker": "PROBLEM & USER VALUE",
            "bullets": [
                "User: software leads reviewing agent-authored dbt repairs",
                "Bottleneck: build success does not prove business correctness",
                "Goal: one verified, approval-ready evidence bundle",
            ],
            "narration": (
                "Agent-authored data repairs can pass every build check and still violate a visible "
                "business rule. DriftProof is for software leads who need to decide whether a dbt "
                "repair is safe to approve. Instead of another model opinion, it produces a verified "
                "evidence bundle and keeps the final decision with a qualified human."
            ),
            "evidence": ["oracle/problem-brief.md", "docs/requirements.md"],
        },
        {
            "index": 2,
            "title": "Fair comparison: the same 24 candidates",
            "kicker": "FROZEN BASELINE",
            "bullets": [
                f"{metrics['cases']} paired cases: {metrics['safe_total']} safe, {metrics['unsafe_total']} green-but-wrong",
                "Baseline and DriftProof receive identical candidate, context and build command",
                "Gold labels open only after predictions are written",
            ],
            "narration": (
                f"The benchmark is frozen and balanced: {metrics['cases']} project-authored cases, "
                f"with {metrics['safe_total']} externally safe repairs and {metrics['unsafe_total']} "
                "repairs that build green but violate the visible contract. The baseline and DriftProof "
                "receive the same candidate, context, trajectory and dbt build command. Labels are "
                "opened only after both predictions are recorded."
            ),
            "evidence": [
                "benchmark_dbt/manifest.json",
                "results/driftproof-comparison/comparison.json",
            ],
        },
        {
            "index": 3,
            "title": "How DriftProof works",
            "kicker": "AGENTIC ENGINEERING",
            "bullets": [
                "Immutable source snapshot + networkless bubblewrap build",
                "Typed visible-contract compiler + bounded optional clarifier",
                "Deterministic checks → hash-bound report → human checkpoint",
            ],
            "narration": (
                "DriftProof snapshots the candidate, copies it to a disposable worktree and runs dbt "
                "inside a networkless bubblewrap namespace. It compiles visible business context into "
                "typed checks. An optional bounded clarifier can propose typed rules, but cannot run code "
                "or approve a change. Deterministic checks, source-integrity verification and a hash-bound "
                "bundle lead to a fixed human checkpoint."
            ),
            "evidence": ["docs/architecture.md", "src/driftproof/runner.py"],
        },
        {
            "index": 4,
            "title": "One command shows the failure mode",
            "kicker": "HUMAN ERGONOMICS",
            "bullets": [
                "uv run driftproof demo",
                "Build-only baseline: safe PASS, unsafe PASS",
                "DriftProof: safe APPROVE, unsafe REJECT",
            ],
            "narration": (
                "A judge can run one credential-free command: uv run driftproof demo. The command creates "
                "two transparent projects. Both pass the same build-only baseline. DriftProof approves "
                "the correct sales minus refunds repair, rejects the green-but-wrong sales plus refunds "
                "repair, verifies both bundles and prints the human report paths."
            ),
            "evidence": [
                "src/driftproof/demo.py",
                "reviews/2026-08-31-round-5-installed-demo/qualification.json",
            ],
        },
        {
            "index": 5,
            "title": "AI agents get a typed state machine",
            "kicker": "AGENT ERGONOMICS",
            "bullets": [
                "fingerprint → agent → verify-response",
                "Exactly one JSON object; stable exits 0, 10, 20 and 30",
                "Response, request and evidence identities are independently bound",
            ],
            "narration": (
                "Autonomous agents do not scrape terminal prose. They fingerprint immutable input, invoke "
                "a strict one-object protocol, then independently verify that the response matches its "
                "claimed bundle and request identity. Stable exits separate approval, rejection, human "
                "review and invalid execution. Content-bound retry identifiers change when the candidate "
                "or context changes."
            ),
            "evidence": [
                "docs/driftproof-agent-protocol.md",
                "reviews/2026-08-31-round-6-response-binding/qualification.json",
            ],
        },
        {
            "index": 6,
            "title": "Measured improvement with a visible trade-off",
            "kicker": "RESULTS",
            "bullets": [
                f"Safe-approval macro-F1: {metrics['baseline_macro_f1']:.3f} → {metrics['advanced_macro_f1']:.3f}",
                f"Accuracy: {_percentage(metrics['baseline_accuracy'])} → {_percentage(metrics['advanced_accuracy'])}",
                f"Unsafe escapes: {_percentage(metrics['baseline_escape'])} → {_percentage(metrics['advanced_escape'])}",
                f"Safe auto-approvals: {metrics['safe_approved']}/{metrics['safe_total']}; human escalations: {metrics['human_reviews']}/{metrics['cases']}",
            ],
            "narration": (
                f"On the frozen benchmark, safe-approval macro F one rises from "
                f"{metrics['baseline_macro_f1']:.3f} to {metrics['advanced_macro_f1']:.3f}. Accuracy rises "
                f"from {_percentage(metrics['baseline_accuracy'])} to "
                f"{_percentage(metrics['advanced_accuracy'])}. Most importantly, unsafe-repair escapes "
                f"fall from {_percentage(metrics['baseline_escape'])} to "
                f"{_percentage(metrics['advanced_escape'])}. The cost is explicit: only "
                f"{metrics['safe_approved']} of {metrics['safe_total']} safe cases are auto-approved, and "
                f"{metrics['human_reviews']} cases escalate to a human."
            ),
            "evidence": ["results/driftproof-comparison/comparison.json"],
        },
        {
            "index": 7,
            "title": "Traces and claims are judge-ready",
            "kicker": "REPRODUCIBILITY",
            "bullets": [
                "24 canonical baseline traces + contract-clarifier live/replay trace",
                "Eight headline claims bound to exact evidence and limitations",
                "Six rubric criteria mapped to exactly 100 points",
            ],
            "narration": (
                "The submission no longer asks a judge to hunt through directories. It indexes all twenty "
                "four canonical baseline traces and the contract clarifier live and replay trace. A claim "
                "ledger binds eight headline claims to exact byte lengths and hashes. A rubric map links "
                "all six scoring criteria to evidence and executable checks totaling one hundred points."
            ),
            "evidence": [
                "submission/AGENT_TRAJECTORIES.json",
                "submission/CLAIM_LEDGER.json",
                "submission/RUBRIC_MAP.json",
            ],
        },
        {
            "index": 8,
            "title": "Adversarially hardened delivery",
            "kicker": "HOSTILE REVIEW",
            "bullets": [
                "No-clobber publication and symlink/path rejection",
                "Rehashed trajectory and verifier substitutions fail closed",
                "Downloaded release verifies with Python + Git only",
            ],
            "narration": (
                "Fresh adversarial rounds attacked first contact, concurrent agents, response substitution, "
                "judge navigation and downloaded delivery. The release uses no-clobber publication, rejects "
                "unsafe paths and symlinks, and cross-binds trajectories, claims, rubric, archives and the "
                "embedded Git commit. Even rehashed trajectory and verifier substitutions fail closed."
            ),
            "evidence": [
                "reviews/2026-08-31-round-7-judge-packet/qualification.json",
                "reviews/2026-08-31-round-8-standalone-verifier/qualification.json",
            ],
        },
        {
            "index": 9,
            "title": "Hot take: optimize safety before approval rate",
            "kicker": "INSIGHT & LIMITATIONS",
            "bullets": [
                "A green build proves executability, not business correctness",
                "Visible uncertainty is better than confident unsafe approval",
                "Synthetic benchmark; typed rule language; human approval remains mandatory",
            ],
            "narration": (
                "The evidence supports one hot take: a green build is evidence about executability, not "
                "business correctness. A useful release agent should minimize unsafe escapes first and "
                "make uncertainty visible instead of hiding it behind a high approval rate. This is a "
                "balanced synthetic benchmark with a finite typed rule language. It is not universal "
                "correctness, and every result still requires qualified human approval."
            ),
            "evidence": ["CHANGELOG.md", "submission/CLAIM_LEDGER.json"],
        },
    ]
    return {
        "schema_version": 1,
        "protocol": "driftproof.demo-video-plan.v1",
        "source_commit": commit,
        "metrics": metrics,
        "scenes": scenes,
        "human_approval_required": True,
        "consequential_action_taken": False,
    }


def _wrap_lines(value: str, width: int) -> list[str]:
    return textwrap.wrap(value, width=width, break_long_words=False, break_on_hyphens=False) or [""]


def _svg(scene: dict[str, Any]) -> str:
    title_lines = _wrap_lines(str(scene["title"]), 38)
    bullets = scene["bullets"]
    if not isinstance(bullets, list):
        raise VideoRenderError("scene bullets must be a list")
    title_markup = "".join(
        f'<tspan x="150" dy="{0 if index == 0 else 82}">{html.escape(line)}</tspan>'
        for index, line in enumerate(title_lines)
    )
    bullet_markup: list[str] = []
    y = 500
    for bullet in bullets:
        lines = _wrap_lines(str(bullet), 68)
        bullet_markup.append(f'<circle cx="174" cy="{y - 13}" r="8" fill="#52d3a2"/>')
        for line_index, line in enumerate(lines):
            bullet_markup.append(
                f'<text x="205" y="{y + line_index * 52}" class="bullet">{html.escape(line)}</text>'
            )
        y += max(82, len(lines) * 52 + 34)
    evidence = "  ·  ".join(str(item) for item in scene["evidence"])
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080">
<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#071321"/>
    <stop offset="1" stop-color="#122840"/>
  </linearGradient>
  <style>
    .kicker {{ font: 700 28px 'DejaVu Sans'; letter-spacing: 4px; fill: #52d3a2; }}
    .title {{ font: 700 68px 'DejaVu Sans'; fill: #f4f8ff; }}
    .bullet {{ font: 36px 'DejaVu Sans'; fill: #dce8f5; }}
    .small {{ font: 22px 'DejaVu Sans'; fill: #8fa9c3; }}
    .scene {{ font: 700 24px 'DejaVu Sans'; fill: #071321; }}
  </style>
</defs>
<rect width="1920" height="1080" fill="url(#bg)"/>
<rect x="0" y="0" width="24" height="1080" fill="#52d3a2"/>
<rect x="150" y="92" width="520" height="5" rx="2" fill="#31516f"/>
<text x="150" y="145" class="kicker">{html.escape(str(scene["kicker"]))}</text>
<text x="150" y="255" class="title">{title_markup}</text>
{"".join(bullet_markup)}
<rect x="150" y="965" width="1620" height="1" fill="#31516f"/>
<text x="150" y="1012" class="small">{html.escape(evidence[:150])}</text>
<circle cx="1740" cy="137" r="48" fill="#52d3a2"/>
<text x="1740" y="146" text-anchor="middle" class="scene">{scene["index"]}/9</text>
</svg>\n"""


def _transcript(plan: dict[str, Any]) -> str:
    lines = [
        "# DriftProof solution video transcript",
        "",
        f"Source commit: `{plan['source_commit']}`",
        "",
        "This transcript is generated from committed evidence. The video makes no claim of universal correctness or guaranteed competition placement.",
        "",
    ]
    for scene in plan["scenes"]:
        lines.extend(
            [
                f"## Scene {scene['index']} — {scene['title']}",
                "",
                str(scene["narration"]),
                "",
                "Evidence:",
                *[f"- `{item}`" for item in scene["evidence"]],
                "",
            ]
        )
    lines.extend(
        [
            "## Fixed authority boundary",
            "",
            "- Human approval required: `true`",
            "- Consequential action taken: `false`",
            "",
        ]
    )
    return "\n".join(lines)


def _evidence_records(root: Path, plan: dict[str, Any]) -> list[dict[str, Any]]:
    paths = sorted({str(item) for scene in plan["scenes"] for item in scene["evidence"]})
    records: list[dict[str, Any]] = []
    for relative in paths:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise VideoRenderError(f"video evidence is missing or unsafe: {relative}")
        records.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return records


def _audio_duration(path: Path) -> float:
    value = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    ).stdout.strip()
    try:
        return float(value)
    except ValueError as exc:
        raise VideoRenderError(f"could not determine audio duration: {path}") from exc


def _render_scene(
    scene: dict[str, Any],
    work: Path,
    *,
    image_tool: str,
    speech_tool: str,
) -> tuple[Path, float]:
    index = int(scene["index"])
    base = work / f"scene-{index:02d}"
    svg = base.with_suffix(".svg")
    png = base.with_suffix(".png")
    wav = base.with_suffix(".wav")
    mp4 = base.with_suffix(".mp4")
    svg.write_text(_svg(scene), encoding="utf-8")
    _run([image_tool, str(svg), str(png)])
    _run(
        [
            speech_tool,
            "-v",
            "en-gb",
            "-s",
            "154",
            "-p",
            "45",
            "-a",
            "165",
            "-w",
            str(wav),
            str(scene["narration"]),
        ]
    )
    _audio_duration(wav)
    _run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-loop",
            "1",
            "-framerate",
            "30",
            "-i",
            str(png),
            "-i",
            str(wav),
            "-filter_complex",
            "[1:a]apad=pad_dur=1.0[a]",
            "-map",
            "0:v:0",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-tune",
            "stillimage",
            "-crf",
            "21",
            "-r",
            "30",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-ar",
            "48000",
            "-shortest",
            "-movflags",
            "+faststart",
            str(mp4),
        ],
        timeout=600,
    )
    return mp4, _audio_duration(mp4)


def _render_video(
    plan: dict[str, Any],
    destination: Path,
    work: Path,
    *,
    image_tool: str,
    speech_tool: str,
) -> list[dict[str, Any]]:
    scene_records: list[dict[str, Any]] = []
    scene_paths: list[Path] = []
    for scene in plan["scenes"]:
        path, duration = _render_scene(
            scene,
            work,
            image_tool=image_tool,
            speech_tool=speech_tool,
        )
        scene_paths.append(path)
        scene_records.append(
            {
                "index": scene["index"],
                "title": scene["title"],
                "duration_seconds": duration,
                "sha256": _sha256(path),
            }
        )
    total = sum(float(item["duration_seconds"]) for item in scene_records)
    if total < 90 or total >= 295:
        raise VideoRenderError(f"scene duration must be 90-294.999 seconds: {total}")
    concat = work / "concat.txt"
    concat.write_text(
        "".join(f"file '{path.as_posix()}'\n" for path in scene_paths),
        encoding="utf-8",
    )
    _run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(destination),
        ],
        timeout=600,
    )
    return scene_records


def render_video_delivery(
    output: Path,
    *,
    root: Path = ROOT,
    expected_commit: str | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    output = output.expanduser().resolve(strict=False)
    if output.is_symlink() or output.exists():
        raise VideoRenderError(f"video output must be an absent, non-symlink path: {output}")
    readiness = video_readiness(root, expected_commit=expected_commit)
    if readiness["ready"] is not True:
        raise VideoRenderError("; ".join(str(item) for item in readiness["problems"]))
    source_commit = str(readiness["source_commit"])
    selected_tools = readiness["selected_tools"]
    if not isinstance(selected_tools, dict):
        raise VideoRenderError("video readiness did not select rendering tools")
    image_tool = selected_tools.get("image")
    speech_tool = selected_tools.get("speech")
    if not isinstance(image_tool, str) or not isinstance(speech_tool, str):
        raise VideoRenderError("video readiness omitted image or speech tooling")

    plan = build_video_plan(root, source_commit=source_commit)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    work = Path(tempfile.mkdtemp(prefix="driftproof-video-work-"))
    try:
        transcript_path = temporary / _TRANSCRIPT_NAME
        storyboard_path = temporary / _STORYBOARD_NAME
        durations_path = temporary / _SCENE_DURATIONS_NAME
        source_manifest_path = temporary / _SOURCE_MANIFEST_NAME
        video_path = temporary / _VIDEO_NAME
        verification_path = temporary / _VERIFICATION_NAME

        transcript_path.write_text(_transcript(plan), encoding="utf-8")
        scene_records = _render_video(
            plan,
            video_path,
            work,
            image_tool=image_tool,
            speech_tool=speech_tool,
        )
        storyboard = {
            "schema_version": 1,
            "protocol": "driftproof.demo-video-storyboard.v1",
            "source_commit": source_commit,
            "scenes": [
                {
                    **scene,
                    "duration_seconds": scene_records[index]["duration_seconds"],
                }
                for index, scene in enumerate(plan["scenes"])
            ],
            "human_approval_required": True,
            "consequential_action_taken": False,
        }
        storyboard_path.write_text(
            json.dumps(storyboard, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        durations = {
            "schema_version": 1,
            "protocol": "driftproof.demo-video-durations.v1",
            "source_commit": source_commit,
            "total_duration_seconds": sum(
                float(item["duration_seconds"]) for item in scene_records
            ),
            "scenes": scene_records,
        }
        durations_path.write_text(
            json.dumps(durations, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        generated_inputs = {
            path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
            for path in (transcript_path, storyboard_path, durations_path)
        }
        source_manifest = {
            "schema_version": 1,
            "protocol": "driftproof.demo-video-source.v1",
            "source_commit": source_commit,
            "renderer": {
                "path": "scripts/render_demo_video.py",
                "sha256": _sha256(root / "scripts/render_demo_video.py"),
            },
            "verifier": {
                "path": "scripts/verify_demo_video.py",
                "sha256": _sha256(root / "scripts/verify_demo_video.py"),
            },
            "generated_inputs": generated_inputs,
            "evidence": _evidence_records(root, plan),
            "tools": {
                "ffmpeg": _tool_version(str(selected_tools["ffmpeg"])),
                "ffprobe": _tool_version(str(selected_tools["ffprobe"])),
                Path(speech_tool).name: _tool_version(speech_tool),
                Path(image_tool).name: _tool_version(image_tool),
            },
            "readiness": readiness,
            "human_approval_required": True,
            "consequential_action_taken": False,
        }
        source_manifest_path.write_text(
            json.dumps(source_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        verification = verify_video_delivery(
            temporary,
            source_root=root,
            expected_commit=source_commit,
        )
        verification_path.write_text(
            json.dumps(verification, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        expected_files = {
            _VIDEO_NAME,
            _TRANSCRIPT_NAME,
            _STORYBOARD_NAME,
            _SOURCE_MANIFEST_NAME,
            _SCENE_DURATIONS_NAME,
            _VERIFICATION_NAME,
        }
        observed = {path.name for path in temporary.iterdir() if path.is_file()}
        if observed != expected_files:
            raise VideoRenderError(f"video delivery file set mismatch: {sorted(observed)}")
        temporary.replace(output)
        return verification
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
        if work.exists():
            shutil.rmtree(work)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render or preflight the evidence-derived DriftProof solution video."
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--expected-commit")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Emit one non-mutating readiness JSON object without rendering media.",
    )
    args = parser.parse_args()
    if args.check:
        payload = video_readiness(args.root, expected_commit=args.expected_commit)
        print(json.dumps(payload, indent=2, sort_keys=True))
        raise SystemExit(0 if payload["ready"] is True else 30)
    if args.output is None:
        parser.error("--output is required unless --check is used")
    payload = render_video_delivery(
        args.output,
        root=args.root,
        expected_commit=args.expected_commit,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
