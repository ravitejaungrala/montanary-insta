"""Kick off a Remotion Lambda render and wait for the MP4 URL.

Phase 1 implementation deliberately uses the **Remotion CLI** via
`subprocess` because the CLI handles every quirk of the Remotion Lambda
invoke protocol (version-matching, input-prop serialization, render-id
polling, output URL extraction). Replicating the same logic in Python
with raw `boto3.lambda.invoke()` calls is fragile across Remotion
upgrades and would slow this phase down.

DEPLOYMENT NOTE: the production backend Lambda runs Python and CANNOT
shell out to Node. Before we deploy, this module needs to be replaced
with one of:
  (a) A small Node "renderer-shim" Lambda that wraps `renderMediaOnLambda`
      and is invoked by the backend via boto3.lambda.invoke
  (b) A direct boto3 invocation that mirrors the Remotion Lambda
      payload protocol (more brittle, requires version pinning)

For local development today (`python main.py` on a dev machine that
already has Node + the video-renderer package installed), the subprocess
path works fine — the CLI is in $PATH via npx.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger("pipelyt.video.dispatcher")

# Where the video-renderer package lives relative to this file. Used as
# the CWD for `npx remotion lambda render` so the CLI can find its
# bundled Remotion binary + node_modules.
_RENDERER_DIR = Path(__file__).resolve().parents[3] / "video-renderer"

# Required env. Populated when we deploy + push the site (see README in
# apps/video-renderer/). The dispatcher refuses to call the CLI if any
# of these are missing — fail loud rather than render to the wrong place.
_REQUIRED_ENV = (
    "REMOTION_AWS_ACCESS_KEY_ID",
    "REMOTION_AWS_SECRET_ACCESS_KEY",
    "REMOTION_AWS_REGION",
    "REMOTION_SERVE_URL",
)

# Composition id registered in apps/video-renderer/src/Root.tsx.
_COMPOSITION_ID = "brand-promo-9x16"

# Regex to pluck the final MP4 URL out of the CLI's stdout. The CLI
# prints lines like:
#     + S3   https://s3.us-east-1.amazonaws.com/.../out.mp4   4.2 MB
_S3_URL_RE = re.compile(r"https://s3\.[a-z0-9-]+\.amazonaws\.com/\S+\.mp4")


def _load_env_from_renderer() -> dict[str, str]:
    """Populate Remotion env vars from `apps/video-renderer/.env` if the
    backend's own process env doesn't have them. The CLI looks at the
    renderer dir's .env automatically when we set CWD there, so this is
    really just so we can print a clean error message on missing vars."""
    env_path = _RENDERER_DIR / ".env"
    out: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _check_env() -> None:
    backend_env = os.environ
    file_env = _load_env_from_renderer()
    merged = {**file_env, **backend_env}
    missing = [k for k in _REQUIRED_ENV if not merged.get(k)]
    if missing:
        raise RuntimeError(
            f"Missing Remotion env vars: {missing}. Either set them in the "
            f"backend's process env or in apps/video-renderer/.env"
        )


def render_video(
    *,
    storyboard: dict,
    timeout_sec: int = 600,
) -> str:
    """Render the given storyboard JSON via Remotion Lambda.

    Returns the public S3 URL of the finished MP4. Blocks until the
    render completes or the timeout is hit.

    Args:
        storyboard: a BrandPromoStoryboard dict produced by
                    services.video.storyboard_agent.generate_storyboard
        timeout_sec: max wall-clock to wait for the render to finish
    """
    # AWS Lambda preflight — the FastAPI Lambda image does NOT contain
    # Node.js, the apps/video-renderer/ directory, or `npx`. The
    # subprocess approach we use locally cannot run there. Fail loudly
    # with a clear, user-facing message instead of crashing with
    # "[Errno 2] No such file or directory: '/var/video-renderer'"
    # which confuses users into thinking it's a deployment-config bug.
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        raise RuntimeError(
            "Video generation isn't available on this deployment yet. "
            "The render pipeline needs Node.js + the Remotion CLI which "
            "the FastAPI Lambda image doesn't ship. Migration plan: a "
            "separate Node-based renderer-shim Lambda invoked via boto3. "
            "Until then, video generation only works on the local dev "
            "server (apps/video-renderer/ checked out + npm install run)."
        )

    _check_env()

    # Write storyboard JSON to a temp file — we pass --props=<path> to the
    # CLI. Inline --props=<json> works for small payloads but breaks on
    # Windows when the JSON contains quotes; the file path is universal.
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump({"storyboard": storyboard}, f, ensure_ascii=False)
        props_path = f.name

    try:
        cmd = [
            "npx", "remotion", "lambda", "render",
            os.environ.get("REMOTION_SERVE_URL")
                or _load_env_from_renderer().get("REMOTION_SERVE_URL", ""),
            _COMPOSITION_ID,
            f"--props={props_path}",
        ]
        logger.info(f"[dispatcher] running: {' '.join(cmd[:6])} ... --props=<{len(json.dumps(storyboard))} bytes>")
        proc = subprocess.run(
            cmd,
            cwd=str(_RENDERER_DIR),
            capture_output=True,
            text=True,
            shell=True,  # required on Windows so npx is resolved
            timeout=timeout_sec,
            encoding="utf-8",
            errors="replace",
        )
    finally:
        try:
            os.unlink(props_path)
        except OSError:
            pass

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    combined = stdout + "\n" + stderr

    if proc.returncode != 0:
        # Surface the most informative tail of the CLI output to the API.
        tail = combined.strip().splitlines()[-15:]
        raise RuntimeError(
            "Remotion render failed (exit "
            f"{proc.returncode}):\n" + "\n".join(tail)
        )

    match = _S3_URL_RE.search(combined)
    if not match:
        tail = combined.strip().splitlines()[-15:]
        raise RuntimeError(
            "Render finished but no S3 URL found in output:\n" + "\n".join(tail)
        )

    url = match.group(0)
    logger.info(f"[dispatcher] render OK -> {url}")
    return url
