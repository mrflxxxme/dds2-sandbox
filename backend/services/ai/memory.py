"""
Obsidian-style memory layer for the multi-agent AI system.

Provides persistent, file-based memory organized by project and brand.
Agents use this to remember past insights, track anomalies, and build
brand profiles over time.

Storage layout:
    /data/ai_memory/
      {project_id}/
        {brand}/
          profile.md      — auto-generated brand summary
          insights.md     — agent-generated insights (append-only)
          anomalies.md    — detected anomalies with status
        _all/
          insights.md     — project-wide insights (no brand filter)
"""

import asyncio
import logging
import os
import re
import tempfile

from backend.config import settings
from backend.utils.time import utcnow

logger = logging.getLogger("dds.ai.memory")


def _get_memory_base() -> str:
    """Resolve memory base directory. Falls back to temp dir if configured path is not writable."""
    base = settings.AI_MEMORY_DIR
    try:
        os.makedirs(base, exist_ok=True)
        # Quick write test
        test_file = os.path.join(base, ".write_test")
        with open(test_file, "w") as f:
            f.write("ok")
        os.remove(test_file)
        return base
    except OSError:
        fallback = os.path.join(tempfile.gettempdir(), "dds_ai_memory")
        os.makedirs(fallback, exist_ok=True)
        logger.warning("AI memory dir %s not writable, using fallback: %s", base, fallback)
        return fallback


MEMORY_BASE = _get_memory_base()
MAX_INSIGHTS_PER_BRAND = 50
MAX_INSIGHT_AGE_DAYS = 90

# Regex to split insight blocks: each starts with "### "
_INSIGHT_BLOCK_RE = re.compile(r"(?=^### )", re.MULTILINE)

# Characters unsafe for directory/file names
_UNSAFE_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sanitize_brand(brand: str) -> str:
    """Sanitize brand name for safe use as directory name."""
    sanitized = _UNSAFE_CHARS_RE.sub("_", brand.strip())
    sanitized = sanitized.strip(". ")
    if not sanitized:
        sanitized = "_unnamed"
    return sanitized[:200]


async def _ensure_dirs(project_id: int, brand: str | None) -> str:
    """Create directory structure if needed. Return brand dir path."""
    safe_brand = _sanitize_brand(brand) if brand is not None else "_all"

    dir_path = os.path.join(MEMORY_BASE, str(project_id), safe_brand)

    try:
        await asyncio.to_thread(os.makedirs, dir_path, exist_ok=True)
    except OSError as exc:
        logger.warning("Failed to create memory dir %s: %s", dir_path, exc)

    return dir_path


async def _read_file(path: str) -> str:
    """Read file, return empty string if not exists."""
    try:
        return await asyncio.to_thread(_read_file_sync, path)
    except FileNotFoundError:
        return ""
    except OSError as exc:
        logger.warning("Failed to read memory file %s: %s", path, exc)
        return ""


def _read_file_sync(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


async def _append_file(path: str, content: str) -> None:
    """Append content to file, create if not exists."""
    try:
        await asyncio.to_thread(_append_file_sync, path, content)
    except OSError as exc:
        logger.warning("Failed to append to memory file %s: %s", path, exc)


def _append_file_sync(path: str, content: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(content)


async def _write_file(path: str, content: str) -> None:
    """Atomically write content to file (write to tmp, then rename)."""
    try:
        await asyncio.to_thread(_write_file_sync, path, content)
    except OSError as exc:
        logger.warning("Failed to write memory file %s: %s", path, exc)


def _write_file_sync(path: str, content: str) -> None:
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp_path, path)


def _split_insight_blocks(text: str) -> list[str]:
    """Split markdown text into insight blocks (each starting with '### ')."""
    blocks = _INSIGHT_BLOCK_RE.split(text.strip())
    return [b.strip() for b in blocks if b.strip()]


def _extract_insight_text(block: str) -> str:
    """Extract the insight text from a block, ignoring the header line."""
    lines = block.strip().splitlines()
    body_lines = [ln.strip().lstrip("- ") for ln in lines[1:] if ln.strip()]
    return " ".join(body_lines)


async def _rotate_insights(path: str, max_count: int) -> None:
    """Keep only last max_count insight blocks in file."""
    content = await _read_file(path)
    if not content:
        return

    blocks = _split_insight_blocks(content)
    if len(blocks) <= max_count:
        return

    trimmed = blocks[-max_count:]
    await _write_file(path, "\n\n".join(trimmed) + "\n")
    logger.info("Rotated insights in %s: %d -> %d blocks", path, len(blocks), max_count)


async def get_memory_context(project_id: int, brand: str | None) -> str:
    """Load relevant memory for an agent. Returns markdown string.

    Reads: brand profile + last 10 insights + active anomalies.
    If brand is None, reads from _all/.
    Returns empty string if no memory exists yet.
    """
    try:
        dir_path = await _ensure_dirs(project_id, brand)
        parts: list[str] = []

        # Brand profile (only for specific brands)
        if brand is not None:
            profile = await _read_file(os.path.join(dir_path, "profile.md"))
            if profile:
                parts.append(f"## Brand Profile: {brand}\n{profile}")

        # Last 10 insights
        insights_text = await _read_file(os.path.join(dir_path, "insights.md"))
        if insights_text:
            blocks = _split_insight_blocks(insights_text)
            last_blocks = blocks[-10:]
            if last_blocks:
                header = "## Recent Insights"
                parts.append(f"{header}\n" + "\n\n".join(last_blocks))

        # Active anomalies
        anomalies_text = await _read_file(os.path.join(dir_path, "anomalies.md"))
        if anomalies_text:
            blocks = _split_insight_blocks(anomalies_text)
            active = [b for b in blocks if "**Status:** active" in b]
            if active:
                parts.append("## Active Anomalies\n" + "\n\n".join(active))

        return "\n\n".join(parts)
    except Exception as exc:
        logger.warning(
            "Failed to load memory context for project=%s brand=%s: %s",
            project_id,
            brand,
            exc,
        )
        return ""


async def save_insights(project_id: int, brand: str | None, insights: list[str]) -> None:
    """Append new insights to insights.md with timestamp.

    Format per insight:
        ### YYYY-MM-DD HH:MM
        - insight text

    Deduplication: skip if identical insight exists in last 10 entries.
    Rotation: if > MAX_INSIGHTS_PER_BRAND, trim oldest.
    """
    if not insights:
        return

    try:
        dir_path = await _ensure_dirs(project_id, brand)
        insights_path = os.path.join(dir_path, "insights.md")

        # Read existing for dedup check
        existing = await _read_file(insights_path)
        existing_blocks = _split_insight_blocks(existing)
        recent_texts = {_extract_insight_text(b) for b in existing_blocks[-10:]}

        now = utcnow()
        timestamp = now.strftime("%Y-%m-%d %H:%M")
        new_content = ""

        for insight in insights:
            text = insight.strip()
            if not text:
                continue
            if text in recent_texts:
                logger.debug("Skipping duplicate insight: %.60s...", text)
                continue
            new_content += f"\n### {timestamp}\n- {text}\n"

        if new_content:
            await _append_file(insights_path, new_content)
            await _rotate_insights(insights_path, MAX_INSIGHTS_PER_BRAND)

    except Exception as exc:
        logger.warning(
            "Failed to save insights for project=%s brand=%s: %s",
            project_id,
            brand,
            exc,
        )


async def save_anomaly(
    project_id: int,
    brand: str | None,
    anomaly: str,
    severity: str = "warning",
) -> None:
    """Record an anomaly in anomalies.md.

    Format:
        ### YYYY-MM-DD HH:MM [severity]
        - anomaly text
        - **Status:** active
    """
    if not anomaly or not anomaly.strip():
        return

    try:
        dir_path = await _ensure_dirs(project_id, brand)
        anomalies_path = os.path.join(dir_path, "anomalies.md")

        now = utcnow()
        timestamp = now.strftime("%Y-%m-%d %H:%M")
        content = f"\n### {timestamp} [{severity}]\n" f"- {anomaly.strip()}\n" f"- **Status:** active\n"

        await _append_file(anomalies_path, content)

    except Exception as exc:
        logger.warning(
            "Failed to save anomaly for project=%s brand=%s: %s",
            project_id,
            brand,
            exc,
        )


async def update_brand_profile(project_id: int, brand: str, profile_text: str) -> None:
    """Overwrite brand profile.md with new profile."""
    try:
        dir_path = await _ensure_dirs(project_id, brand)
        profile_path = os.path.join(dir_path, "profile.md")
        await _write_file(profile_path, profile_text)
    except Exception as exc:
        logger.warning(
            "Failed to update brand profile for project=%s brand=%s: %s",
            project_id,
            brand,
            exc,
        )
