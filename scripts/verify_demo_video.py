from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

_VIDEO_NAME = "driftproof-demo.mp4"
_TRANSCRIPT_NAME = "driftproof-demo-transcript.md"
_STORYBOARD_NAME = "driftproof-demo-storyboard.json"
_SOURCE_MANIFEST_NAME = "driftproof-demo-source-manifest.json"
_SCENE_DURATIONS_NAME = "driftproof-demo-scene-durations.json"
_VERIFICATION_NAME = "driftproof-demo-verification.json"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


class VideoVerificationError(RuntimeError):
    """Raised when the solution-video delivery is missing or inconsistent."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular(path: Path) -> Path:
    if path.is_symlink() or not path.is_file():
        raise VideoVerificationError(f"required media artifact is missing or unsafe: {path.name}")
    return path


def _load_object(path: Path) -> dict[str, Any]:
    _regular(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VideoVerificationError(f"invalid JSON media artifact: {path.name}") from exc
    if not isinstance(value, dict):
        raise VideoVerificationError(f"media artifact must be a JSON object: {path.name}")
    return value


def _run_json(*argv: str, timeout: int = 120) -> dict[str, Any]:
    completed = subprocess.run(
        list(argv),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise VideoVerificationError(
            f"command failed ({' '.join(argv)}): {completed.stderr[-2000:]}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise VideoVerificationError(f"command did not return JSON: {' '.join(argv)}") from exc
    if not isinstance(value, dict):
        raise VideoVerificationError(f"command JSON must be an object: {' '.join(argv)}")
    return value


def _probe(video: Path) -> dict[str, Any]:
    return _run_json(
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(video),
    )


def _decode(video: Path) -> None:
    completed = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video), "-f", "null", "-"],
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if completed.returncode != 0 or completed.stderr.strip():
        raise VideoVerificationError(f"complete media decode failed: {completed.stderr[-2000:]}")


def _mean_volume(video: Path) -> float:
    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(video),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if completed.returncode != 0:
        raise VideoVerificationError("audio volume analysis failed")
    match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", completed.stderr)
    if match is None:
        raise VideoVerificationError("video audio is absent or silent")
    return float(match.group(1))


def _stream(probe: dict[str, Any], kind: str) -> dict[str, Any]:
    streams = probe.get("streams")
    if not isinstance(streams, list):
        raise VideoVerificationError("ffprobe response lacks streams")
    matching = [
        stream
        for stream in streams
        if isinstance(stream, dict) and stream.get("codec_type") == kind
    ]
    if len(matching) != 1:
        raise VideoVerificationError(f"video must contain exactly one {kind} stream")
    return matching[0]


def verify_video_delivery(
    directory: Path,
    *,
    source_root: Path | None = None,
    expected_commit: str | None = None,
) -> dict[str, Any]:
    directory = directory.expanduser().resolve(strict=True)
    if directory.is_symlink() or not directory.is_dir():
        raise VideoVerificationError("media delivery directory is missing or unsafe")
    video = _regular(directory / _VIDEO_NAME)
    transcript_path = _regular(directory / _TRANSCRIPT_NAME)
    storyboard_path = _regular(directory / _STORYBOARD_NAME)
    source_manifest_path = _regular(directory / _SOURCE_MANIFEST_NAME)
    durations_path = _regular(directory / _SCENE_DURATIONS_NAME)

    source_manifest = _load_object(source_manifest_path)
    storyboard = _load_object(storyboard_path)
    durations = _load_object(durations_path)
    transcript = transcript_path.read_text(encoding="utf-8", errors="strict")

    if source_manifest.get("protocol") != "driftproof.demo-video-source.v1":
        raise VideoVerificationError("unexpected video source-manifest protocol")
    source_commit = source_manifest.get("source_commit")
    if not isinstance(source_commit, str) or _COMMIT.fullmatch(source_commit) is None:
        raise VideoVerificationError("video source commit is invalid")
    if expected_commit is not None and source_commit != expected_commit:
        raise VideoVerificationError(
            f"video source commit differs from expected commit: {source_commit} != {expected_commit}"
        )

    readiness = source_manifest.get("readiness")
    if (
        not isinstance(readiness, dict)
        or readiness.get("protocol") != "driftproof.demo-video-readiness.v1"
        or readiness.get("ready") is not True
        or readiness.get("source_commit") != source_commit
        or readiness.get("worktree_clean") is not True
        or readiness.get("candidate_code_executed") is not False
        or readiness.get("files_created") is not False
        or readiness.get("problems") != []
        or readiness.get("human_approval_required") is not True
        or readiness.get("consequential_action_taken") is not False
    ):
        raise VideoVerificationError("video source readiness receipt is invalid")
    selected_tools = readiness.get("selected_tools")
    if (
        not isinstance(selected_tools, dict)
        or set(selected_tools) != {"ffmpeg", "ffprobe", "speech", "image", "git"}
        or any(not isinstance(value, str) or not value for value in selected_tools.values())
    ):
        raise VideoVerificationError("video source readiness lacks selected tools")

    generated = source_manifest.get("generated_inputs")
    expected_generated = {
        _TRANSCRIPT_NAME: transcript_path,
        _STORYBOARD_NAME: storyboard_path,
        _SCENE_DURATIONS_NAME: durations_path,
    }
    if not isinstance(generated, dict) or set(generated) != set(expected_generated):
        raise VideoVerificationError("video source manifest generated-input set is incomplete")
    for name, path in expected_generated.items():
        metadata = generated.get(name)
        if (
            not isinstance(metadata, dict)
            or metadata.get("bytes") != path.stat().st_size
            or metadata.get("sha256") != _sha256(path)
        ):
            raise VideoVerificationError(f"video source manifest does not bind {name}")

    evidence = source_manifest.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise VideoVerificationError("video source manifest lacks evidence identities")
    for item in evidence:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("path"), str)
            or not isinstance(item.get("bytes"), int)
            or not isinstance(item.get("sha256"), str)
            or _SHA256.fullmatch(str(item.get("sha256"))) is None
        ):
            raise VideoVerificationError("video source evidence record is malformed")
        if source_root is not None:
            source = source_root.resolve() / str(item["path"])
            if (
                source.is_symlink()
                or not source.is_file()
                or source.stat().st_size != item["bytes"]
                or _sha256(source) != item["sha256"]
            ):
                raise VideoVerificationError(
                    f"video source evidence differs from repository: {item['path']}"
                )

    if storyboard.get("protocol") != "driftproof.demo-video-storyboard.v1":
        raise VideoVerificationError("unexpected storyboard protocol")
    if storyboard.get("source_commit") != source_commit:
        raise VideoVerificationError("storyboard source commit differs from source manifest")
    scenes = storyboard.get("scenes")
    if not isinstance(scenes, list) or len(scenes) != 9:
        raise VideoVerificationError("storyboard must contain exactly nine scenes")
    titles: list[str] = []
    for index, scene in enumerate(scenes, 1):
        if (
            not isinstance(scene, dict)
            or scene.get("index") != index
            or not isinstance(scene.get("title"), str)
            or not isinstance(scene.get("narration"), str)
        ):
            raise VideoVerificationError(f"storyboard scene {index} is malformed")
        titles.append(str(scene["title"]))

    if durations.get("protocol") != "driftproof.demo-video-durations.v1":
        raise VideoVerificationError("unexpected scene-duration protocol")
    raw_scene_durations = durations.get("scenes")
    if not isinstance(raw_scene_durations, list) or len(raw_scene_durations) != len(scenes):
        raise VideoVerificationError("scene-duration set differs from storyboard")
    total_scene_duration = 0.0
    for index, item in enumerate(raw_scene_durations, 1):
        if (
            not isinstance(item, dict)
            or item.get("index") != index
            or not isinstance(item.get("duration_seconds"), (int, float))
            or float(item["duration_seconds"]) <= 0
        ):
            raise VideoVerificationError(f"scene duration {index} is malformed")
        total_scene_duration += float(item["duration_seconds"])

    if not transcript.startswith("# DriftProof solution video transcript\n"):
        raise VideoVerificationError("unexpected video transcript header")
    for title in titles:
        if title not in transcript:
            raise VideoVerificationError(f"transcript omits scene title: {title}")
    forbidden_claims = (
        "100% accuracy",
        "macro-F1 1.000",
        "guaranteed rank 1",
        "guarantees universal correctness",
        "proves universal correctness",
    )
    lowered_transcript = transcript.lower()
    if any(claim.lower() in lowered_transcript for claim in forbidden_claims):
        raise VideoVerificationError("video transcript contains an unsupported claim")

    probe = _probe(video)
    format_record = probe.get("format")
    if not isinstance(format_record, dict):
        raise VideoVerificationError("ffprobe response lacks format metadata")
    try:
        duration = float(format_record["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise VideoVerificationError("video duration is unavailable") from exc
    if duration < 90 or duration >= 300:
        raise VideoVerificationError(f"video duration must be 90-299.999 seconds: {duration}")
    if abs(duration - total_scene_duration) > 2.0:
        raise VideoVerificationError(
            "video duration differs materially from the scene-duration receipt"
        )

    video_stream = _stream(probe, "video")
    audio_stream = _stream(probe, "audio")
    if (
        video_stream.get("codec_name") != "h264"
        or video_stream.get("width") != 1920
        or video_stream.get("height") != 1080
        or video_stream.get("pix_fmt") != "yuv420p"
    ):
        raise VideoVerificationError("video stream must be 1920x1080 H.264 yuv420p")
    if audio_stream.get("codec_name") != "aac" or audio_stream.get("sample_rate") != "48000":
        raise VideoVerificationError("audio stream must be AAC at 48 kHz")

    _decode(video)
    mean_volume_db = _mean_volume(video)
    if mean_volume_db < -45.0:
        raise VideoVerificationError(f"video narration is too quiet: {mean_volume_db} dB")

    return {
        "schema_version": 1,
        "protocol": "driftproof.demo-video-verification.v1",
        "verified": True,
        "source_commit": source_commit,
        "video": {
            "file": _VIDEO_NAME,
            "bytes": video.stat().st_size,
            "sha256": _sha256(video),
            "duration_seconds": duration,
            "width": video_stream["width"],
            "height": video_stream["height"],
            "video_codec": video_stream["codec_name"],
            "pixel_format": video_stream["pix_fmt"],
            "audio_codec": audio_stream["codec_name"],
            "audio_sample_rate": int(audio_stream["sample_rate"]),
            "mean_volume_db": mean_volume_db,
            "complete_decode": True,
        },
        "assets": {
            name: {
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for name, path in sorted(
                {
                    _TRANSCRIPT_NAME: transcript_path,
                    _STORYBOARD_NAME: storyboard_path,
                    _SOURCE_MANIFEST_NAME: source_manifest_path,
                    _SCENE_DURATIONS_NAME: durations_path,
                }.items()
            )
        },
        "scene_count": len(scenes),
        "duration_below_five_minutes": True,
        "human_approval_required": True,
        "consequential_action_taken": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify the complete DriftProof solution-video delivery."
    )
    parser.add_argument("directory", type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--expected-commit")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = verify_video_delivery(
        args.directory,
        source_root=args.source_root,
        expected_commit=args.expected_commit,
    )
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
