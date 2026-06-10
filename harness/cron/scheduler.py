"""
Cron job scheduler - executes due jobs.

Provides tick() which checks for due jobs and runs them. The gateway
calls this every 60 seconds from a background thread.

Uses a file-based lock (~/.plutus-agent/cron/.tick.lock) so only one tick
runs at a time if multiple processes overlap.
"""

import time
import asyncio
import concurrent.futures
import contextvars
import json
import logging
import os
import subprocess
import sys

# fcntl is Unix-only; on Windows use msvcrt for file locking
try:
    import fcntl
except ImportError:
    fcntl = None
    try:
        import msvcrt
    except ImportError:
        msvcrt = None
from pathlib import Path
from typing import List, Optional

# Add parent directory to path for imports BEFORE repo-level imports.
# Without this, standalone invocations (e.g. after `hermes update` reloads
# the module) fail with ModuleNotFoundError for plutus_time et al.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from harness.constants import get_hermes_home
from harness.cli.config import load_config
from harness.clock import now as _hermes_now

logger = logging.getLogger(__name__)

# Valid delivery platforms — used to validate user-supplied platform names
# in cron delivery targets, preventing env var enumeration via crafted names.
_KNOWN_DELIVERY_PLATFORMS = frozenset({
    "telegram", "discord", "slack", "webhook",
})

# Platforms that support a configured cron/notification home target, mapped to
# the environment variable used by gateway setup/runtime config.
_HOME_TARGET_ENV_VARS = {
    "telegram": "TELEGRAM_HOME_CHANNEL",
    "discord": "DISCORD_HOME_CHANNEL",
    "slack": "SLACK_HOME_CHANNEL",
}

# Legacy env var names kept for back-compat.  Each entry is the current
# primary env var → the previous name.  _get_home_target_chat_id falls
# back to the legacy name if the primary is unset, so users who set the
# old name before the rename keep working until they migrate.
_LEGACY_HOME_TARGET_ENV_VARS = {}

from harness.cron.jobs import get_due_jobs, mark_job_run, save_job_output, advance_next_run

# Sentinel: when a cron agent has nothing new to report, it can start its
# response with this marker to suppress delivery.  Output is still saved
# locally for audit.
SILENT_MARKER = "[SILENT]"

# Resolve Hermes home directory (respects HERMES_HOME override)
_hermes_home = get_hermes_home()

# File-based lock prevents concurrent ticks from gateway + daemon + systemd timer
_LOCK_DIR = _hermes_home / "cron"
_LOCK_FILE = _LOCK_DIR / ".tick.lock"


_PRIMARY_SESSION_CACHE: dict = {"loaded": False, "value": None}


def _load_primary_session_origin() -> Optional[dict]:
    """Resolve the operator's primary session for synthetic injection fallback.

    Used when a cron job doesn't carry its own ``origin`` field (e.g.
    plutus-heartbeat seeded via the helper, wake-event one-shot jobs
    created by ``watchers/poller.py:schedule_wake_session``, jobs created
    via API). Cached once per process — re-cache requires a gateway
    restart.

    Resolution order:

    1. ``notifications.primary_session`` in config.yaml (explicit, preferred):

       .. code:: yaml

          notifications:
            primary_session:
              platform: telegram
              chat_id: "1054536871"
              user_id: "1054536871"
              chat_type: dm

    2. Per-platform home-channel env vars (``TELEGRAM_HOME_CHANNEL``,
       ``DISCORD_HOME_CHANNEL``, etc.) — first one set wins. Lets the
       existing single-operator setup unlock unified-session injection
       without any new config.

    Returns None when no fallback is configured; callers then route to
    the legacy fresh-session path.
    """
    if _PRIMARY_SESSION_CACHE["loaded"]:
        return _PRIMARY_SESSION_CACHE["value"]
    _PRIMARY_SESSION_CACHE["loaded"] = True

    # 1. Explicit config
    try:
        cfg = load_config() or {}
    except Exception:
        cfg = {}
    notif = cfg.get("notifications", {}) if isinstance(cfg, dict) else {}
    ps = notif.get("primary_session") if isinstance(notif, dict) else None
    if isinstance(ps, dict):
        platform = str(ps.get("platform") or "").strip()
        chat_id = str(ps.get("chat_id") or "").strip()
        if platform and chat_id:
            out = {
                "platform": platform,
                "chat_id": chat_id,
                "user_id": str(ps.get("user_id") or "").strip() or None,
                "user_name": str(ps.get("user_name") or "").strip() or None,
                "chat_type": str(ps.get("chat_type") or "dm").strip(),
                "thread_id": str(ps.get("thread_id") or "").strip() or None,
            }
            _PRIMARY_SESSION_CACHE["value"] = out
            return out

    # 2. Home-channel env var fallback
    for platform_name, env_var in _HOME_TARGET_ENV_VARS.items():
        chat_id = os.getenv(env_var, "").strip()
        if not chat_id:
            legacy = _LEGACY_HOME_TARGET_ENV_VARS.get(env_var)
            if legacy:
                chat_id = os.getenv(legacy, "").strip()
        if chat_id:
            out = {
                "platform": platform_name,
                "chat_id": chat_id,
                "user_id": chat_id if platform_name == "telegram" else None,
                "user_name": None,
                "chat_type": "dm",
                "thread_id": None,
            }
            _PRIMARY_SESSION_CACHE["value"] = out
            return out

    return None


def _resolve_origin(job: dict) -> Optional[dict]:
    """Extract origin info from a job, preserving any extra routing metadata.

    Returns the job's own ``origin`` field when present and well-formed
    (both platform + chat_id set). Returns None otherwise. NOTE: this
    helper deliberately does NOT consult the primary-session fallback —
    callers that route to the unified-session injection path should use
    ``_resolve_origin_for_injection`` instead, which consults the
    fallback. This split preserves the legacy contract for delivery-
    target resolution (``deliver: origin`` still respects "no origin
    configured" and falls back to the home channel as before).
    """
    origin = job.get("origin")
    if not origin:
        return None
    platform = origin.get("platform")
    chat_id = origin.get("chat_id")
    if platform and chat_id:
        return origin
    return None


def _resolve_origin_for_injection(job: dict) -> Optional[dict]:
    """Like ``_resolve_origin`` but with the primary-session fallback.

    Used by the unified-session synthetic-injection path so jobs without
    explicit origins (heartbeats seeded via the helper, wake-event one-shot
    jobs from the watcher) still land in the operator's session when a
    primary session is configured.
    """
    origin = _resolve_origin(job)
    if origin:
        return origin
    return _load_primary_session_origin()


def _get_home_target_chat_id(platform_name: str) -> str:
    """Return the configured home target chat/room ID for a delivery platform."""
    env_var = _HOME_TARGET_ENV_VARS.get(platform_name.lower())
    if not env_var:
        return ""
    value = os.getenv(env_var, "")
    if not value:
        legacy = _LEGACY_HOME_TARGET_ENV_VARS.get(env_var)
        if legacy:
            value = os.getenv(legacy, "")
    return value


def _resolve_single_delivery_target(job: dict, deliver_value: str) -> Optional[dict]:
    """Resolve one concrete auto-delivery target for a cron job."""

    origin = _resolve_origin(job)

    if deliver_value == "local":
        return None

    if deliver_value == "origin":
        if origin:
            return {
                "platform": origin["platform"],
                "chat_id": str(origin["chat_id"]),
                "thread_id": origin.get("thread_id"),
            }
        # Origin missing (e.g. job created via API/script) — try each
        # platform's home channel as a fallback instead of silently dropping.
        for platform_name in _HOME_TARGET_ENV_VARS:
            chat_id = _get_home_target_chat_id(platform_name)
            if chat_id:
                logger.info(
                    "Job '%s' has deliver=origin but no origin; falling back to %s home channel",
                    job.get("name", job.get("id", "?")),
                    platform_name,
                )
                return {
                    "platform": platform_name,
                    "chat_id": chat_id,
                    "thread_id": None,
                }
        return None

    if ":" in deliver_value:
        platform_name, rest = deliver_value.split(":", 1)
        platform_key = platform_name.lower()

        from harness.tools.send_message_tool import _parse_target_ref

        parsed_chat_id, parsed_thread_id, is_explicit = _parse_target_ref(platform_key, rest)
        if is_explicit:
            chat_id, thread_id = parsed_chat_id, parsed_thread_id
        else:
            chat_id, thread_id = rest, None

        # Resolve human-friendly labels like "Alice (dm)" to real IDs.
        try:
            from harness.gateway.channel_directory import resolve_channel_name
            resolved = resolve_channel_name(platform_key, chat_id)
            if resolved:
                parsed_chat_id, parsed_thread_id, resolved_is_explicit = _parse_target_ref(platform_key, resolved)
                if resolved_is_explicit:
                    chat_id, thread_id = parsed_chat_id, parsed_thread_id
                else:
                    chat_id = resolved
        except Exception:
            pass

        return {
            "platform": platform_name,
            "chat_id": chat_id,
            "thread_id": thread_id,
        }

    platform_name = deliver_value
    if origin and origin.get("platform") == platform_name:
        return {
            "platform": platform_name,
            "chat_id": str(origin["chat_id"]),
            "thread_id": origin.get("thread_id"),
        }

    if platform_name.lower() not in _KNOWN_DELIVERY_PLATFORMS:
        return None
    chat_id = _get_home_target_chat_id(platform_name)
    if not chat_id:
        return None

    return {
        "platform": platform_name,
        "chat_id": chat_id,
        "thread_id": None,
    }


def _resolve_delivery_targets(job: dict) -> List[dict]:
    """Resolve all concrete auto-delivery targets for a cron job (supports comma-separated deliver)."""
    deliver = job.get("deliver", "local")
    if deliver == "local":
        return []
    parts = [p.strip() for p in str(deliver).split(",") if p.strip()]
    seen = set()
    targets = []
    for part in parts:
        target = _resolve_single_delivery_target(job, part)
        if target:
            key = (target["platform"].lower(), str(target["chat_id"]), target.get("thread_id"))
            if key not in seen:
                seen.add(key)
                targets.append(target)
    return targets


def _resolve_delivery_target(job: dict) -> Optional[dict]:
    """Resolve the concrete auto-delivery target for a cron job, if any."""
    targets = _resolve_delivery_targets(job)
    return targets[0] if targets else None


# Media extension sets — keep in sync with gateway/platforms/base.py:_process_message_background
_AUDIO_EXTS = frozenset({'.ogg', '.opus', '.mp3', '.wav', '.m4a'})
_VIDEO_EXTS = frozenset({'.mp4', '.mov', '.avi', '.mkv', '.webm', '.3gp'})
_IMAGE_EXTS = frozenset({'.jpg', '.jpeg', '.png', '.webp', '.gif'})


def _send_media_via_adapter(adapter, chat_id: str, media_files: list, metadata: dict | None, loop, job: dict) -> None:
    """Send extracted MEDIA files as native platform attachments via a live adapter.

    Routes each file to the appropriate adapter method (send_voice, send_image_file,
    send_video, send_document) based on file extension — mirroring the routing logic
    in ``BasePlatformAdapter._process_message_background``.
    """
    from pathlib import Path

    for media_path, _is_voice in media_files:
        try:
            ext = Path(media_path).suffix.lower()
            if ext in _AUDIO_EXTS:
                coro = adapter.send_voice(chat_id=chat_id, audio_path=media_path, metadata=metadata)
            elif ext in _VIDEO_EXTS:
                coro = adapter.send_video(chat_id=chat_id, video_path=media_path, metadata=metadata)
            elif ext in _IMAGE_EXTS:
                coro = adapter.send_image_file(chat_id=chat_id, image_path=media_path, metadata=metadata)
            else:
                coro = adapter.send_document(chat_id=chat_id, file_path=media_path, metadata=metadata)

            future = asyncio.run_coroutine_threadsafe(coro, loop)
            try:
                result = future.result(timeout=30)
            except TimeoutError:
                future.cancel()
                raise
            if result and not getattr(result, "success", True):
                logger.warning(
                    "Job '%s': media send failed for %s: %s",
                    job.get("id", "?"), media_path, getattr(result, "error", "unknown"),
                )
        except Exception as e:
            logger.warning("Job '%s': failed to send media %s: %s", job.get("id", "?"), media_path, e)


def _deliver_result(job: dict, content: str, adapters=None, loop=None) -> Optional[str]:
    """
    Deliver job output to the configured target(s) (origin chat, specific platform, etc.).

    When ``adapters`` and ``loop`` are provided (gateway is running), tries to
    use the live adapter first — this supports E2EE rooms (e.g. Matrix) where
    the standalone HTTP path cannot encrypt.  Falls back to standalone send if
    the adapter path fails or is unavailable.

    Returns None on success, or an error string on failure.
    """
    targets = _resolve_delivery_targets(job)
    if not targets:
        if job.get("deliver", "local") != "local":
            msg = f"no delivery target resolved for deliver={job.get('deliver', 'local')}"
            logger.warning("Job '%s': %s", job["id"], msg)
            return msg
        return None  # local-only jobs don't deliver — not a failure

    from harness.tools.send_message_tool import _send_to_platform
    from harness.gateway.config import load_gateway_config, Platform

    platform_map = {
        "telegram": Platform.TELEGRAM,
        "discord": Platform.DISCORD,
        "slack": Platform.SLACK,
    }

    # Optionally wrap the content with a header/footer so the user knows this
    # is a cron delivery.  Wrapping is on by default; set cron.wrap_response: false
    # in config.yaml for clean output.
    wrap_response = True
    try:
        user_cfg = load_config()
        wrap_response = user_cfg.get("cron", {}).get("wrap_response", True)
    except Exception:
        pass

    if wrap_response:
        task_name = job.get("name", job["id"])
        job_id = job.get("id", "")
        delivery_content = (
            f"Cronjob Response: {task_name}\n"
            f"(job_id: {job_id})\n"
            f"-------------\n\n"
            f"{content}\n\n"
            f"To stop or manage this job, send me a new message (e.g. \"stop reminder {task_name}\")."
        )
    else:
        delivery_content = content

    # Extract MEDIA: tags so attachments are forwarded as files, not raw text
    from harness.gateway.platforms.base import BasePlatformAdapter
    media_files, cleaned_delivery_content = BasePlatformAdapter.extract_media(delivery_content)

    try:
        config = load_gateway_config()
    except Exception as e:
        msg = f"failed to load gateway config: {e}"
        logger.error("Job '%s': %s", job["id"], msg)
        return msg

    delivery_errors = []

    for target in targets:
        platform_name = target["platform"]
        chat_id = target["chat_id"]
        thread_id = target.get("thread_id")

        # Diagnostic: log thread_id for topic-aware delivery debugging
        origin = job.get("origin") or {}
        origin_thread = origin.get("thread_id")
        if origin_thread and not thread_id:
            logger.warning(
                "Job '%s': origin has thread_id=%s but delivery target lost it "
                "(deliver=%s, target=%s)",
                job["id"], origin_thread, job.get("deliver", "local"), target,
            )
        elif thread_id:
            logger.debug(
                "Job '%s': delivering to %s:%s thread_id=%s",
                job["id"], platform_name, chat_id, thread_id,
            )

        platform = platform_map.get(platform_name.lower())
        if not platform:
            msg = f"unknown platform '{platform_name}'"
            logger.warning("Job '%s': %s", job["id"], msg)
            delivery_errors.append(msg)
            continue

        # Prefer the live adapter when the gateway is running — this supports E2EE
        # rooms (e.g. Matrix) where the standalone HTTP path cannot encrypt.
        runtime_adapter = (adapters or {}).get(platform)
        delivered = False
        if runtime_adapter is not None and loop is not None and getattr(loop, "is_running", lambda: False)():
            send_metadata = {"thread_id": thread_id} if thread_id else None
            try:
                # Send cleaned text (MEDIA tags stripped) — not the raw content
                text_to_send = cleaned_delivery_content.strip()
                adapter_ok = True
                if text_to_send:
                    future = asyncio.run_coroutine_threadsafe(
                        runtime_adapter.send(chat_id, text_to_send, metadata=send_metadata),
                        loop,
                    )
                    try:
                        send_result = future.result(timeout=60)
                    except TimeoutError:
                        future.cancel()
                        raise
                    if send_result and not getattr(send_result, "success", True):
                        err = getattr(send_result, "error", "unknown")
                        logger.warning(
                            "Job '%s': live adapter send to %s:%s failed (%s), falling back to standalone",
                            job["id"], platform_name, chat_id, err,
                        )
                        adapter_ok = False  # fall through to standalone path

                # Send extracted media files as native attachments via the live adapter
                if adapter_ok and media_files:
                    _send_media_via_adapter(runtime_adapter, chat_id, media_files, send_metadata, loop, job)

                if adapter_ok:
                    logger.info("Job '%s': delivered to %s:%s via live adapter", job["id"], platform_name, chat_id)
                    delivered = True
            except Exception as e:
                logger.warning(
                    "Job '%s': live adapter delivery to %s:%s failed (%s), falling back to standalone",
                    job["id"], platform_name, chat_id, e,
                )

        if not delivered:
            pconfig = config.platforms.get(platform)
            if not pconfig or not pconfig.enabled:
                msg = f"platform '{platform_name}' not configured/enabled"
                logger.warning("Job '%s': %s", job["id"], msg)
                delivery_errors.append(msg)
                continue

            # Standalone path: run the async send in a fresh event loop (safe from any thread)
            coro = _send_to_platform(platform, pconfig, chat_id, cleaned_delivery_content, thread_id=thread_id, media_files=media_files)
            try:
                result = asyncio.run(coro)
            except RuntimeError:
                # asyncio.run() checks for a running loop before awaiting the coroutine;
                # when it raises, the original coro was never started — close it to
                # prevent "coroutine was never awaited" RuntimeWarning, then retry in a
                # fresh thread that has no running loop.
                coro.close()
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(asyncio.run, _send_to_platform(platform, pconfig, chat_id, cleaned_delivery_content, thread_id=thread_id, media_files=media_files))
                    result = future.result(timeout=30)
            except Exception as e:
                msg = f"delivery to {platform_name}:{chat_id} failed: {e}"
                logger.error("Job '%s': %s", job["id"], msg)
                delivery_errors.append(msg)
                continue

            if result and result.get("error"):
                msg = f"delivery error: {result['error']}"
                logger.error("Job '%s': %s", job["id"], msg)
                delivery_errors.append(msg)
                continue

            logger.info("Job '%s': delivered to %s:%s", job["id"], platform_name, chat_id)

    if delivery_errors:
        return "; ".join(delivery_errors)
    return None


_DEFAULT_SCRIPT_TIMEOUT = 120  # seconds
# Backward-compatible module override used by tests and emergency monkeypatches.
_SCRIPT_TIMEOUT = _DEFAULT_SCRIPT_TIMEOUT


def _get_script_timeout() -> int:
    """Resolve cron pre-run script timeout from module/env/config with a safe default."""
    if _SCRIPT_TIMEOUT != _DEFAULT_SCRIPT_TIMEOUT:
        try:
            timeout = int(float(_SCRIPT_TIMEOUT))
            if timeout > 0:
                return timeout
        except Exception:
            logger.warning("Invalid patched _SCRIPT_TIMEOUT=%r; using env/config/default", _SCRIPT_TIMEOUT)

    env_value = os.getenv("HERMES_CRON_SCRIPT_TIMEOUT", "").strip()
    if env_value:
        try:
            timeout = int(float(env_value))
            if timeout > 0:
                return timeout
        except Exception:
            logger.warning("Invalid HERMES_CRON_SCRIPT_TIMEOUT=%r; using config/default", env_value)

    try:
        cfg = load_config() or {}
        cron_cfg = cfg.get("cron", {}) if isinstance(cfg, dict) else {}
        configured = cron_cfg.get("script_timeout_seconds")
        if configured is not None:
            timeout = int(float(configured))
            if timeout > 0:
                return timeout
    except Exception as exc:
        logger.debug("Failed to load cron script timeout from config: %s", exc)

    return _DEFAULT_SCRIPT_TIMEOUT


def _run_job_script(script_path: str) -> tuple[bool, str]:
    """Execute a cron job's data-collection script and capture its output.

    Scripts must reside within HERMES_HOME/scripts/.  Both relative and
    absolute paths are resolved and validated against this directory to
    prevent arbitrary script execution via path traversal or absolute
    path injection.

    Args:
        script_path: Path to a Python script.  Relative paths are resolved
            against HERMES_HOME/scripts/.  Absolute and ~-prefixed paths
            are also validated to ensure they stay within the scripts dir.

    Returns:
        (success, output) — on failure *output* contains the error message so the
        LLM can report the problem to the user.
    """
    from harness.constants import get_hermes_home

    scripts_dir = get_hermes_home() / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir_resolved = scripts_dir.resolve()

    raw = Path(script_path).expanduser()
    if raw.is_absolute():
        path = raw.resolve()
    else:
        path = (scripts_dir / raw).resolve()

    # Guard against path traversal, absolute path injection, and symlink
    # escape — scripts MUST reside within HERMES_HOME/scripts/.
    try:
        path.relative_to(scripts_dir_resolved)
    except ValueError:
        return False, (
            f"Blocked: script path resolves outside the scripts directory "
            f"({scripts_dir_resolved}): {script_path!r}"
        )

    if not path.exists():
        return False, f"Script not found: {path}"
    if not path.is_file():
        return False, f"Script path is not a file: {path}"

    script_timeout = _get_script_timeout()

    try:
        result = subprocess.run(
            [sys.executable, str(path)],
            capture_output=True,
            text=True,
            timeout=script_timeout,
            cwd=str(path.parent),
        )
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()

        # Redact secrets from both stdout and stderr before any return path.
        try:
            from harness.agent.redact import redact_sensitive_text
            stdout = redact_sensitive_text(stdout)
            stderr = redact_sensitive_text(stderr)
        except Exception:
            pass

        if result.returncode != 0:
            parts = [f"Script exited with code {result.returncode}"]
            if stderr:
                parts.append(f"stderr:\n{stderr}")
            if stdout:
                parts.append(f"stdout:\n{stdout}")
            return False, "\n".join(parts)

        return True, stdout

    except subprocess.TimeoutExpired:
        return False, f"Script timed out after {script_timeout}s: {path}"
    except Exception as exc:
        return False, f"Script execution failed: {exc}"


def _parse_wake_gate(script_output: str) -> bool:
    """Parse the last non-empty stdout line of a cron job's pre-check script
    as a wake gate.

    The convention (ported from nanoclaw #1232): if the last stdout line is
    JSON like ``{"wakeAgent": false}``, the agent is skipped entirely — no
    LLM run, no delivery. Any other output (non-JSON, missing flag, gate
    absent, or ``wakeAgent: true``) means wake the agent normally.

    Returns True if the agent should wake, False to skip.
    """
    if not script_output:
        return True
    stripped_lines = [line for line in script_output.splitlines() if line.strip()]
    if not stripped_lines:
        return True
    last_line = stripped_lines[-1].strip()
    try:
        gate = json.loads(last_line)
    except (json.JSONDecodeError, ValueError):
        return True
    if not isinstance(gate, dict):
        return True
    return gate.get("wakeAgent", True) is not False


def _build_job_prompt(job: dict, prerun_script: Optional[tuple] = None) -> str:
    """Build the effective prompt for a cron job, optionally loading one or more skills first.

    Args:
        job: The cron job dict.
        prerun_script: Optional ``(success, stdout)`` from a script that has
            already been executed by the caller (e.g. for a wake-gate check).
            When provided, the script is not re-executed and the cached
            result is used for prompt injection. When omitted, the script
            (if any) runs inline as before.
    """
    prompt = job.get("prompt", "")
    skills = job.get("skills")

    # Run data-collection script if configured, inject output as context.
    script_path = job.get("script")
    if script_path:
        if prerun_script is not None:
            success, script_output = prerun_script
        else:
            success, script_output = _run_job_script(script_path)
        if success:
            if script_output:
                prompt = (
                    "## Script Output\n"
                    "The following data was collected by a pre-run script. "
                    "Use it as context for your analysis.\n\n"
                    f"```\n{script_output}\n```\n\n"
                    f"{prompt}"
                )
            else:
                prompt = (
                    "[Script ran successfully but produced no output.]\n\n"
                    f"{prompt}"
                )
        else:
            prompt = (
                "## Script Error\n"
                "The data-collection script failed. Report this to the user.\n\n"
                f"```\n{script_output}\n```\n\n"
                f"{prompt}"
            )

    # Always prepend cron execution guidance so the agent knows how
    # delivery works and can suppress delivery when appropriate.
    cron_hint = (
        "[SYSTEM: You are running as a scheduled cron job. "
        "DELIVERY: Your final response will be automatically delivered "
        "to the user — do NOT use send_message or try to deliver "
        "the output yourself. Just produce your report/output as your "
        "final response and the system handles the rest. "
        "SILENT: If there is genuinely nothing new to report, respond "
        "with exactly \"[SILENT]\" (nothing else) to suppress delivery. "
        "Never combine [SILENT] with content — either report your "
        "findings normally, or say [SILENT] and nothing more.]\n\n"
    )
    prompt = cron_hint + prompt
    if skills is None:
        legacy = job.get("skill")
        skills = [legacy] if legacy else []

    skill_names = [str(name).strip() for name in skills if str(name).strip()]
    if not skill_names:
        return prompt

    from harness.tools.skills_tool import skill_view

    parts = []
    skipped: list[str] = []
    for skill_name in skill_names:
        loaded = json.loads(skill_view(skill_name))
        if not loaded.get("success"):
            error = loaded.get("error") or f"Failed to load skill '{skill_name}'"
            logger.warning("Cron job '%s': skill not found, skipping — %s", job.get("name", job.get("id")), error)
            skipped.append(skill_name)
            continue

        content = str(loaded.get("content") or "").strip()
        if parts:
            parts.append("")
        parts.extend(
            [
                f'[SYSTEM: The user has invoked the "{skill_name}" skill, indicating they want you to follow its instructions. The full skill content is loaded below.]',
                "",
                content,
            ]
        )

    if skipped:
        notice = (
            f"[SYSTEM: The following skill(s) were listed for this job but could not be found "
            f"and were skipped: {', '.join(skipped)}. "
            f"Start your response with a brief notice so the user is aware, e.g.: "
            f"'⚠️ Skill(s) not found and skipped: {', '.join(skipped)}']"
        )
        parts.insert(0, notice)

    if prompt:
        parts.extend(["", f"The user has provided the following instruction alongside the skill invocation: {prompt}"])
    return "\n".join(parts)


def _wrap_synthetic_prompt(prompt: str, *, kind: str, ts) -> str:
    """Wrap a cron-tick or wake prompt with a [SYSTEM TICK ...] marker.

    Lets Plutus distinguish self-prompts (cron tick, wake event) from
    operator turns when they share the same conversation history in the
    unified-session model.
    """
    iso = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"[SYSTEM TICK — {kind} — {iso}]\n{prompt}"


def _run_job_via_synthetic(
    job: dict,
    prompt: str,
    origin: dict,
    gateway,
    loop,
    timeout_s: float,
) -> tuple[bool, str, str, Optional[str]]:
    """Execute a cron job by injecting it into the operator's session.

    Bridges from the cron worker thread to the gateway's asyncio loop via
    ``run_coroutine_threadsafe``. The synthetic prompt is queued on the
    operator's chat session — auto-compacted by the gateway's existing
    pre-compress + agent-level compress paths once context grows. The
    operator sees nothing for a quiet tick (the synthetic prompt itself
    is never echoed; Plutus's response is delivered only if non-empty).

    Returns the same tuple as the legacy path so save/deliver/mark all
    work without changes.
    """
    from harness.gateway.config import Platform
    from harness.clock import now as _hermes_now

    job_id = job["id"]
    job_name = job["name"]
    _start_time = time.time()
    kind = f"cron:{job_id}"
    wrapped = _wrap_synthetic_prompt(prompt, kind=kind, ts=_hermes_now())

    try:
        platform = Platform(str(origin["platform"]).lower())
    except Exception as e:
        msg = f"unknown origin platform: {origin.get('platform')!r}"
        logger.error("Job '%s': %s", job_id, msg)
        _log_job_completion(
            job, start_time=_start_time, success=False,
            final_response="", error=msg, mode="synthetic",
        )
        return False, _build_failure_doc(job, prompt, msg), "", msg

    coro = gateway.deliver_synthetic_message(
        platform=platform,
        chat_id=str(origin["chat_id"]),
        text=wrapped,
        kind=kind,
        user_id=origin.get("user_id"),
        user_name=origin.get("user_name") or origin.get("chat_name"),
        chat_type=origin.get("chat_type", "dm"),
        thread_id=origin.get("thread_id"),
    )

    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        # `timeout_s` here is wall-clock, not inactivity. The gateway's
        # AIAgent has its own internal inactivity tracking and will time
        # out long-stuck tools naturally; we just guard against the entire
        # synthetic injection hanging forever (e.g. event loop wedged).
        response = future.result(timeout=timeout_s if timeout_s > 0 else None)
    except concurrent.futures.TimeoutError:
        future.cancel()
        msg = f"synthetic injection timed out after {int(timeout_s)}s"
        logger.error("Job '%s': %s", job_id, msg)
        _log_job_completion(
            job, start_time=_start_time, success=False,
            final_response="", error=msg, mode="synthetic",
        )
        return False, _build_failure_doc(job, prompt, msg), "", msg
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        logger.exception("Job '%s' synthetic injection failed: %s", job_id, msg)
        _log_job_completion(
            job, start_time=_start_time, success=False,
            final_response="", error=msg, mode="synthetic",
        )
        return False, _build_failure_doc(job, prompt, msg), "", msg

    final_response = (response or "").strip() if isinstance(response, str) else ""
    if final_response == "(No response generated)":
        final_response = ""

    output = (
        f"# Cron Job: {job_name}\n\n"
        f"**Job ID:** {job_id}\n"
        f"**Run Time:** {_hermes_now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"**Schedule:** {job.get('schedule_display', 'N/A')}\n"
        f"**Mode:** unified-session synthetic injection\n\n"
        "## Prompt\n\n"
        f"{wrapped}\n\n"
        "## Response\n\n"
        f"{final_response or '(No response generated)'}\n"
    )
    _log_job_completion(
        job, start_time=_start_time, success=True,
        final_response=final_response, mode="synthetic",
    )
    return True, output, final_response, None


def _log_job_completion(
    job: dict,
    *,
    start_time: float,
    success: bool,
    final_response: str,
    error: Optional[str] = None,
    model: Optional[str] = None,
    api_calls: int = 0,
    mode: str = "unknown",
):
    """Log a structured completion line for every cron job exit."""
    job_name = job.get("name", job.get("id", "?"))
    job_id = job.get("id", "?")
    duration = time.time() - start_time
    output_chars = len(final_response) if final_response else 0
    _model = model or job.get("model", "default")

    if success:
        logger.info(
            "harness.cron.scheduler: Job '%s' (ID %s) completed in %.2fs — "
            "model=%s, mode=%s, api_calls=%d, output_chars=%d",
            job_name, job_id, duration, _model, mode, api_calls, output_chars,
        )
    else:
        logger.error(
            "harness.cron.scheduler: Job '%s' (ID %s) FAILED after %.2fs — "
            "model=%s, mode=%s, error=%s",
            job_name, job_id, duration, _model, mode, error or "unknown",
        )


def _build_failure_doc(job: dict, prompt: str, error: str) -> str:
    """Build the standard failure markdown doc for a cron job."""
    return (
        f"# Cron Job: {job['name']} (FAILED)\n\n"
        f"**Job ID:** {job['id']}\n"
        f"**Run Time:** {_hermes_now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"**Schedule:** {job.get('schedule_display', 'N/A')}\n\n"
        "## Prompt\n\n"
        f"{prompt}\n\n"
        "## Error\n\n"
        f"```{error}\n```\n"
    )


def run_job(job: dict, gateway=None) -> tuple[bool, str, str, Optional[str]]:
    """
    Execute a single cron job.

    Two execution paths:

    1. **Unified-session (preferred)** — when ``gateway`` is provided AND
       the job has an ``origin`` (platform + chat_id), the prompt is
       injected as a synthetic message into the operator's persistent
       chat session via ``gateway.deliver_synthetic_message``. Plutus's
       cron ticks share working memory with the operator's chat;
       auto-compaction is handled by the gateway's existing pre-compress
       and agent-level compress paths.

    2. **Legacy fresh-session (fallback)** — when no gateway is available
       (standalone ``plutus-agent cron tick`` daemon, tests) or the job
       has no origin (created via API/script without binding to a chat),
       spawns a brand-new ``AIAgent`` with a synthetic ``cron_<job>_<ts>``
       session_id. This is the pre-Phase-5 behavior, preserved for
       back-compat.

    Returns:
        Tuple of (success, full_output_doc, final_response, error_message)
    """
    job_id = job["id"]
    job_name = job["name"]

    # ── Path 0: desk-agent job (rebuild R4) ──────────────────────────
    # A job with `agent: plutus-ops` runs that AGENT.md recipe directly on
    # its own declared model/toolsets via harness.spawn — no AIAgent
    # middleman, no prompt building. The cron prompt becomes the spawn
    # task. Silent toward the operator chat; wakes the agent enqueues are
    # drained into plutus-main by the gateway ticker.
    desk_agent = job.get("agent")
    if desk_agent:
        from harness.spawn import spawn_agent
        try:
            result = spawn_agent(
                desk_agent,
                job.get("prompt") or f"{job_name} tick",
                session_name=f"cron-{job_id}",
            )
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            logger.exception("Desk-agent job '%s' (%s) failed: %s", job_name, desk_agent, msg)
            return False, _build_failure_doc(job, job.get("prompt") or "", msg), "", msg
        doc = (
            f"# Desk agent job: {job_name}\n\n"
            f"**Agent:** {desk_agent}\n**ok:** {result['ok']}\n"
            f"**duration_s:** {result.get('duration_s')}\n"
            f"**transcript:** {result.get('transcript')}\n"
            f"**problems:** {result.get('problems') or []}\n"
        )
        err = None if result["ok"] else "; ".join(str(x) for x in (result.get("problems") or ["unknown"]))
        return result["ok"], doc, SILENT_MARKER, err

    # Wake-gate: if this job has a pre-check script, run it BEFORE building
    # the prompt so a ``{"wakeAgent": false}`` response can short-circuit
    # the whole agent run. We pass the result into _build_job_prompt so
    # the script is only executed once.
    prerun_script = None
    script_path = job.get("script")
    if script_path:
        prerun_script = _run_job_script(script_path)
        _ran_ok, _script_output = prerun_script
        if _ran_ok and not _parse_wake_gate(_script_output):
            logger.info(
                "Job '%s' (ID: %s): wakeAgent=false, skipping agent run",
                job_name, job_id,
            )
            silent_doc = (
                f"# Cron Job: {job_name}\n\n"
                f"**Job ID:** {job_id}\n"
                f"**Run Time:** {_hermes_now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                "Script gate returned `wakeAgent=false` — agent skipped.\n"
            )
            return True, silent_doc, SILENT_MARKER, None

    prompt = _build_job_prompt(job, prerun_script=prerun_script)
    # For unified-session injection: prefer the job's explicit origin, but
    # fall back to notifications.primary_session (or per-platform home
    # channel) so heartbeats and wake-event jobs without explicit origin
    # still land in the operator's session.
    injection_origin = _resolve_origin_for_injection(job)
    # For the legacy fallback path: only the job's explicit origin matters
    # (preserves the pre-Phase-5 contract for the rare standalone-daemon path).
    legacy_origin = _resolve_origin(job)

    logger.info("Running job '%s' (ID: %s)", job_name, job_id)
    logger.info("Prompt: %s", prompt[:100])

    # ── Path 1: unified-session via gateway ──────────────────────────
    # Requires gateway + injection-eligible origin AND no per-job model
    # override. Jobs with `model` set (V2: plutus-ops on deepseek-v4-flash,
    # plutus-thesis spawns, one-shot future-checks) need legacy fresh-session
    # because synthetic injection runs against the operator chat's persistent
    # AIAgent — that agent's model was bound at session creation and the
    # synthetic-injection path has no mechanism to swap it per-tick. Routing
    # an override through synthetic would silently use the wrong model (the
    # operator session's default) instead of what the cron requested.
    has_model_override = bool(job.get("model"))
    if gateway is not None and injection_origin and not has_model_override:
        gw_loop = getattr(gateway, "_event_loop", None)
        if gw_loop is None:
            try:
                # Gateway exposes its loop via an attribute we set in
                # _start_cron_ticker, but fall back to introspection if
                # callers wired it differently.
                gw_loop = asyncio.get_event_loop_policy().get_event_loop()
            except Exception:
                gw_loop = None
        if gw_loop is not None:
            _cron_timeout = float(os.getenv("PLUTUS_CRON_TIMEOUT") or os.getenv("HERMES_CRON_TIMEOUT", 600))
            return _run_job_via_synthetic(
                job, prompt, injection_origin, gateway, gw_loop, _cron_timeout,
            )
        logger.warning(
            "Job '%s': gateway provided but no asyncio loop — falling back to legacy path",
            job_id,
        )

    # ── Path 2: legacy fresh-session ──────────────────────────────────
    # Reached when: no gateway, no injection origin, OR job has model override.
    if gateway is not None and not injection_origin:
        logger.info(
            "Job '%s': gateway provided but no primary session configured — using legacy fresh-session path",
            job_id,
        )
    elif gateway is not None and has_model_override:
        logger.info(
            "Job '%s': gateway provided but per-job model override (%s) set — using legacy fresh-session path",
            job_id,
            job.get("model"),
        )
    return _legacy_run_job(job, prompt, legacy_origin)


def _legacy_run_job(
    job: dict, prompt: str, origin: Optional[dict],
) -> tuple[bool, str, str, Optional[str]]:
    """Pre-unified-session execution: spawn a fresh AIAgent per tick.

    Used when no gateway is available (standalone daemon, tests) or when
    the job has no origin to route to. Mints a one-off ``cron_<job>_<ts>``
    session_id, runs the agent in a worker thread, ends the session.
    Same shape as the original ``run_job`` body before Phase 5.
    """
    from harness.run_agent import AIAgent

    _start_time = time.time()
    _job_id = job.get("id", "?")
    _job_name = job.get("name", _job_id)
    _model_used = job.get("model") or os.getenv("HERMES_MODEL") or ""

    # Initialize SQLite session store so cron job messages are persisted
    # and discoverable via session_search (same pattern as gateway/run.py).
    _session_db = None
    try:
        from harness.state import SessionDB
        _session_db = SessionDB()
    except Exception as e:
        logger.debug("Job '%s': SQLite session store not available: %s", _job_id, e)

    job_id = job["id"]
    job_name = job["name"]
    _cron_session_id = f"cron_{job_id}_{_hermes_now().strftime('%Y%m%d_%H%M%S')}"

    # Mark this as a cron session so the approval system can apply cron_mode.
    # This env var is process-wide and persists for the lifetime of the
    # scheduler process — every job this process runs is a cron job.
    os.environ["HERMES_CRON_SESSION"] = "1"

    # Use ContextVars for per-job session/delivery state so parallel jobs
    # don't clobber each other's targets (os.environ is process-global).
    from harness.gateway.session_context import set_session_vars, clear_session_vars, _VAR_MAP

    _ctx_tokens = set_session_vars(
        platform=origin["platform"] if origin else "",
        chat_id=str(origin["chat_id"]) if origin else "",
        chat_name=origin.get("chat_name", "") if origin else "",
    )

    try:
        # Re-read .env and config.yaml fresh every run so provider/key
        # changes take effect without a gateway restart.
        from dotenv import load_dotenv
        try:
            load_dotenv(str(_hermes_home / ".env"), override=True, encoding="utf-8")
        except UnicodeDecodeError:
            load_dotenv(str(_hermes_home / ".env"), override=True, encoding="latin-1")

        delivery_target = _resolve_delivery_target(job)
        if delivery_target:
            _VAR_MAP["HERMES_CRON_AUTO_DELIVER_PLATFORM"].set(delivery_target["platform"])
            _VAR_MAP["HERMES_CRON_AUTO_DELIVER_CHAT_ID"].set(str(delivery_target["chat_id"]))
            if delivery_target.get("thread_id") is not None:
                _VAR_MAP["HERMES_CRON_AUTO_DELIVER_THREAD_ID"].set(str(delivery_target["thread_id"]))

        model = job.get("model") or os.getenv("HERMES_MODEL") or ""

        # Load config.yaml for model, reasoning, prefill, toolsets, provider routing
        _cfg = {}
        try:
            import yaml
            _cfg_path = str(_hermes_home / "config.yaml")
            if os.path.exists(_cfg_path):
                with open(_cfg_path) as _f:
                    _cfg = yaml.safe_load(_f) or {}
                _model_cfg = _cfg.get("model", {})
                if not job.get("model"):
                    if isinstance(_model_cfg, str):
                        model = _model_cfg
                    elif isinstance(_model_cfg, dict):
                        model = _model_cfg.get("default", model)
        except Exception as e:
            logger.warning("Job '%s': failed to load config.yaml, using defaults: %s", job_id, e)

        # Apply IPv4 preference if configured.
        try:
            from harness.constants import apply_ipv4_preference
            _net_cfg = _cfg.get("network", {})
            if isinstance(_net_cfg, dict) and _net_cfg.get("force_ipv4"):
                apply_ipv4_preference(force=True)
        except Exception:
            pass

        # Reasoning config from config.yaml
        from harness.constants import parse_reasoning_effort
        effort = str(_cfg.get("agent", {}).get("reasoning_effort", "")).strip()
        reasoning_config = parse_reasoning_effort(effort)

        # Prefill messages from env or config.yaml
        prefill_messages = None
        prefill_file = os.getenv("HERMES_PREFILL_MESSAGES_FILE", "") or _cfg.get("prefill_messages_file", "")
        if prefill_file:
            pfpath = Path(prefill_file).expanduser()
            if not pfpath.is_absolute():
                pfpath = _hermes_home / pfpath
            if pfpath.exists():
                try:
                    with open(pfpath, "r", encoding="utf-8") as _pf:
                        prefill_messages = json.load(_pf)
                    if not isinstance(prefill_messages, list):
                        prefill_messages = None
                except Exception as e:
                    logger.warning("Job '%s': failed to parse prefill messages file '%s': %s", job_id, pfpath, e)
                    prefill_messages = None

        # Max iterations
        max_iterations = _cfg.get("agent", {}).get("max_turns") or _cfg.get("max_turns") or 90

        # Provider routing
        pr = _cfg.get("provider_routing", {})

        from harness.cli.runtime_provider import (
            resolve_runtime_provider,
            format_runtime_provider_error,
        )
        try:
            runtime_kwargs = {
                "requested": job.get("provider") or os.getenv("HERMES_INFERENCE_PROVIDER"),
            }
            if job.get("base_url"):
                runtime_kwargs["explicit_base_url"] = job.get("base_url")
            runtime = resolve_runtime_provider(**runtime_kwargs)
        except Exception as exc:
            message = format_runtime_provider_error(exc)
            _log_job_completion(
                job, start_time=_start_time, success=False,
                final_response="", error=message, model=_model_used, mode="legacy",
            )
            raise RuntimeError(message) from exc

        fallback_model = _cfg.get("fallback_providers") or _cfg.get("fallback_model") or None
        credential_pool = None
        runtime_provider = str(runtime.get("provider") or "").strip().lower()
        if runtime_provider:
            try:
                from harness.agent.credential_pool import load_pool
                pool = load_pool(runtime_provider)
                if pool.has_credentials():
                    credential_pool = pool
                    logger.info(
                        "Job '%s': loaded credential pool for provider %s with %d entries",
                        job_id,
                        runtime_provider,
                        len(pool.entries()),
                    )
            except Exception as e:
                logger.debug("Job '%s': failed to load credential pool for %s: %s", job_id, runtime_provider, e)

        agent = AIAgent(
            model=model,
            api_key=runtime.get("api_key"),
            base_url=runtime.get("base_url"),
            provider=runtime.get("provider"),
            api_mode=runtime.get("api_mode"),
            acp_command=runtime.get("command"),
            acp_args=runtime.get("args"),
            max_iterations=max_iterations,
            reasoning_config=reasoning_config,
            prefill_messages=prefill_messages,
            fallback_model=fallback_model,
            credential_pool=credential_pool,
            providers_allowed=pr.get("only"),
            providers_ignored=pr.get("ignore"),
            providers_order=pr.get("order"),
            provider_sort=pr.get("sort"),
            enabled_toolsets=job.get("enabled_toolsets") or None,
            disabled_toolsets=["cronjob", "messaging", "clarify"],
            quiet_mode=True,
            skip_context_files=True,  # No cwd AGENTS.md/CLAUDE.md walk (PLUTUS.md identity still loads from HERMES_HOME)
            skip_memory=True,  # Cron system prompts would corrupt user representations
            platform="cron",
            session_id=_cron_session_id,
            session_db=_session_db,
        )
        
        # Run the agent with an *inactivity*-based timeout: the job can run
        # for hours if it's actively calling tools / receiving stream tokens,
        # but a hung API call or stuck tool with no activity for the configured
        # duration is caught and killed.  Default 600s (10 min inactivity);
        # override via HERMES_CRON_TIMEOUT env var.  0 = unlimited.
        #
        # Uses the agent's built-in activity tracker (updated by
        # _touch_activity() on every tool call, API call, and stream delta).
        _cron_timeout = float(os.getenv("HERMES_CRON_TIMEOUT", 600))
        _cron_inactivity_limit = _cron_timeout if _cron_timeout > 0 else None
        _POLL_INTERVAL = 5.0
        _cron_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        # Preserve scheduler-scoped ContextVar state (for example skill-declared
        # env passthrough registrations) when the cron run hops into the worker
        # thread used for inactivity timeout monitoring.
        _cron_context = contextvars.copy_context()
        _cron_future = _cron_pool.submit(_cron_context.run, agent.run_conversation, prompt)
        _inactivity_timeout = False
        try:
            if _cron_inactivity_limit is None:
                # Unlimited — just wait for the result.
                result = _cron_future.result()
            else:
                result = None
                while True:
                    done, _ = concurrent.futures.wait(
                        {_cron_future}, timeout=_POLL_INTERVAL,
                    )
                    if done:
                        result = _cron_future.result()
                        break
                    # Agent still running — check inactivity.
                    _idle_secs = 0.0
                    if hasattr(agent, "get_activity_summary"):
                        try:
                            _act = agent.get_activity_summary()
                            _idle_secs = _act.get("seconds_since_activity", 0.0)
                        except Exception:
                            pass
                    if _idle_secs >= _cron_inactivity_limit:
                        _inactivity_timeout = True
                        break
        except Exception:
            _cron_pool.shutdown(wait=False, cancel_futures=True)
            raise
        finally:
            _cron_pool.shutdown(wait=False, cancel_futures=True)

        if _inactivity_timeout:
            # Build diagnostic summary from the agent's activity tracker.
            _activity = {}
            if hasattr(agent, "get_activity_summary"):
                try:
                    _activity = agent.get_activity_summary()
                except Exception:
                    pass
            _last_desc = _activity.get("last_activity_desc", "unknown")
            _secs_ago = _activity.get("seconds_since_activity", 0)
            _cur_tool = _activity.get("current_tool")
            _iter_n = _activity.get("api_call_count", 0)
            _iter_max = _activity.get("max_iterations", 0)

            logger.error(
                "Job '%s' idle for %.0fs (inactivity limit %.0fs) "
                "| last_activity=%s | iteration=%s/%s | tool=%s",
                job_name, _secs_ago, _cron_inactivity_limit,
                _last_desc, _iter_n, _iter_max,
                _cur_tool or "none",
            )
            if hasattr(agent, "interrupt"):
                agent.interrupt("Cron job timed out (inactivity)")
            _err_msg = (
                f"Cron job '{job_name}' idle for "
                f"{int(_secs_ago)}s (limit {int(_cron_inactivity_limit)}s) "
                f"— last activity: {_last_desc}"
            )
            _log_job_completion(
                job, start_time=_start_time, success=False,
                final_response="", error=_err_msg, model=_model_used,
                api_calls=_iter_n, mode="legacy",
            )
            raise TimeoutError(_err_msg)

        # Guard against non-dict returns from run_conversation under error conditions
        if not isinstance(result, dict):
            _err_msg = (
                f"agent.run_conversation returned {type(result).__name__} instead of dict: {result!r}"
            )
            _log_job_completion(
                job, start_time=_start_time, success=False,
                final_response="", error=_err_msg, model=_model_used,
                api_calls=0, mode="legacy",
            )
            raise RuntimeError(_err_msg)

        final_response = result.get("final_response", "") or ""
        # Strip leaked placeholder text that upstream may inject on empty completions.
        if final_response.strip() == "(No response generated)":
            final_response = ""
        # Use a separate variable for log display; keep final_response clean
        # for delivery logic (empty response = no delivery).
        logged_response = final_response if final_response else "(No response generated)"
        
        # Extract api_call_count if available
        _api_calls = 0
        try:
            _api_calls = result.get("api_call_count", 0)
            if not _api_calls and hasattr(agent, "get_activity_summary"):
                _api_calls = agent.get_activity_summary().get("api_call_count", 0)
        except Exception:
            pass

        output = f"""# Cron Job: {job_name}

**Job ID:** {job_id}
**Run Time:** {_hermes_now().strftime('%Y-%m-%d %H:%M:%S')}
**Schedule:** {job.get('schedule_display', 'N/A')}

## Prompt

{prompt}

## Response

{logged_response}
"""
        
        _log_job_completion(
            job, start_time=_start_time, success=True,
            final_response=final_response, model=model,
            api_calls=_api_calls, mode="legacy",
        )
        return True, output, final_response, None
        
    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        logger.exception("Job '%s' failed: %s", job_name, error_msg)
        
        # Extract best-effort api_calls even on failure
        _fail_api_calls = 0
        try:
            if hasattr(agent, "get_activity_summary"):
                _fail_api_calls = agent.get_activity_summary().get("api_call_count", 0)
        except Exception:
            pass

        _log_job_completion(
            job, start_time=_start_time, success=False,
            final_response="", error=error_msg, model=_model_used,
            api_calls=_fail_api_calls, mode="legacy",
        )

        output = f"""# Cron Job: {job_name} (FAILED)

**Job ID:** {job_id}
**Run Time:** {_hermes_now().strftime('%Y-%m-%d %H:%M:%S')}
**Schedule:** {job.get('schedule_display', 'N/A')}

## Prompt

{prompt}

## Error

```
{error_msg}
```
"""
        return False, output, "", error_msg

    finally:
        # Clean up ContextVar session/delivery state for this job.
        clear_session_vars(_ctx_tokens)
        if _session_db:
            try:
                _session_db.end_session(_cron_session_id, "cron_complete")
            except (Exception, KeyboardInterrupt) as e:
                logger.debug("Job '%s': failed to end session: %s", job_id, e)
            try:
                _session_db.close()
            except (Exception, KeyboardInterrupt) as e:
                logger.debug("Job '%s': failed to close SQLite session store: %s", job_id, e)


def tick(verbose: bool = True, adapters=None, loop=None, gateway=None) -> int:
    """
    Check and run all due jobs.

    Uses a file lock so only one tick runs at a time, even if the gateway's
    in-process ticker and a standalone daemon or manual tick overlap.

    Args:
        verbose: Whether to print status messages
        adapters: Optional dict mapping Platform → live adapter (from gateway)
        loop: Optional asyncio event loop (from gateway) for live adapter sends
        gateway: Optional GatewayRunner instance. When provided AND a job
            has an origin (platform+chat_id), the cron tick injects as a
            synthetic message into the operator's persistent platform
            session via gateway.deliver_synthetic_message (unified-session
            model). When None, falls back to legacy fresh-session-per-tick.

    Returns:
        Number of jobs executed (0 if another tick is already running)
    """
    _LOCK_DIR.mkdir(parents=True, exist_ok=True)

    # Cross-platform file locking: fcntl on Unix, msvcrt on Windows
    lock_fd = None
    try:
        lock_fd = open(_LOCK_FILE, "w")
        if fcntl:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif msvcrt:
            msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
    except (OSError, IOError):
        logger.debug("Tick skipped — another instance holds the lock")
        if lock_fd is not None:
            lock_fd.close()
        return 0

    try:
        due_jobs = get_due_jobs()

        if verbose and not due_jobs:
            logger.info("%s - No jobs due", _hermes_now().strftime('%H:%M:%S'))
            return 0

        if verbose:
            logger.info("%s - %s job(s) due", _hermes_now().strftime('%H:%M:%S'), len(due_jobs))

        # Advance next_run_at for all recurring jobs FIRST, under the file lock,
        # before any execution begins.  This preserves at-most-once semantics.
        for job in due_jobs:
            advance_next_run(job["id"])

        # Resolve max parallel workers: env var > config.yaml > unbounded.
        # Set HERMES_CRON_MAX_PARALLEL=1 to restore old serial behaviour.
        _max_workers: Optional[int] = None
        try:
            _env_par = os.getenv("HERMES_CRON_MAX_PARALLEL", "").strip()
            if _env_par:
                _max_workers = int(_env_par) or None
        except (ValueError, TypeError):
            logger.warning("Invalid HERMES_CRON_MAX_PARALLEL value; defaulting to unbounded")
        if _max_workers is None:
            try:
                _ucfg = load_config() or {}
                _cfg_par = (
                    _ucfg.get("cron", {}) if isinstance(_ucfg, dict) else {}
                ).get("max_parallel_jobs")
                if _cfg_par is not None:
                    _max_workers = int(_cfg_par) or None
            except Exception:
                pass

        if verbose:
            logger.info(
                "Running %d job(s) in parallel (max_workers=%s)",
                len(due_jobs),
                _max_workers if _max_workers else "unbounded",
            )

        def _process_job(job: dict) -> bool:
            """Run one due job end-to-end: execute, save, deliver, mark."""
            try:
                success, output, final_response, error = run_job(job, gateway=gateway)

                output_file = save_job_output(job["id"], output)
                if verbose:
                    logger.info("Output saved to: %s", output_file)

                # Deliver the final response to the origin/target chat.
                # If the agent responded with [SILENT], skip delivery (but
                # output is already saved above).  Failed jobs always deliver.
                deliver_content = final_response if success else f"⚠️ Cron job '{job.get('name', job['id'])}' failed:\n{error}"
                should_deliver = bool(deliver_content)
                if should_deliver and success and SILENT_MARKER in deliver_content.strip().upper():
                    logger.info("Job '%s': agent returned %s — skipping delivery", job["id"], SILENT_MARKER)
                    should_deliver = False

                delivery_error = None
                if should_deliver:
                    try:
                        delivery_error = _deliver_result(job, deliver_content, adapters=adapters, loop=loop)
                    except Exception as de:
                        delivery_error = str(de)
                        logger.error("Delivery failed for job %s: %s", job["id"], de)

                # Treat empty final_response as a soft failure so last_status
                # is not "ok" — the agent ran but produced nothing useful.
                # (issue #8585)
                if success and not final_response:
                    success = False
                    error = "Agent completed but produced empty response (model error, timeout, or misconfiguration)"

                mark_job_run(job["id"], success, error, delivery_error=delivery_error)
                return True

            except Exception as e:
                logger.error("Error processing job %s: %s", job['id'], e)
                mark_job_run(job["id"], False, str(e))
                return False

        # Run all due jobs concurrently, each in its own ContextVar copy
        # so session/delivery state stays isolated per-thread.
        with concurrent.futures.ThreadPoolExecutor(max_workers=_max_workers) as _tick_pool:
            _futures = []
            for job in due_jobs:
                _ctx = contextvars.copy_context()
                _futures.append(_tick_pool.submit(_ctx.run, _process_job, job))
            _results = [f.result() for f in _futures]

        return sum(_results)
    finally:
        if fcntl:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        elif msvcrt:
            try:
                msvcrt.locking(lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
            except (OSError, IOError):
                pass
        lock_fd.close()


if __name__ == "__main__":
    tick(verbose=True)
