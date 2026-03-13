"""Ollama model registry: list, classify, VRAM estimation, pull lifecycle.

Plain English: The model catalog. Knows which AI models are installed locally,
how big they are, and how GPU-hungry each one is. Uses name-matching rules to
classify models into resource profiles (embed = lightweight, heavy = needs sole
access, ollama = standard) so the scheduler knows how many can run at once.

Decision it drives: How much VRAM will this job need, and can it run alongside
other jobs or does it need to run alone?
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ollama_queue.db import Database

_log = logging.getLogger(__name__)

# Profile rules: first match wins. (keywords, profile)
_PROFILE_RULES: list[tuple[list[str], str]] = [
    (["embed", "nomic", "mxbai", "bge-m3", "all-minilm"], "embed"),
    (
        ["70b", "34b", "32b", ":671b", "deepseek-r1:14", "deepseek-r1:32", "llama3.3:70", "qwen2.5:72"],
        "heavy",
    ),
]

# Type tag rules: first match wins. (keywords, type_tag)
_TYPE_RULES: list[tuple[list[str], str]] = [
    (["embed", "nomic", "mxbai", "bge"], "embed"),
    (["coder", "-coder", "deepseek-coder", "starcoder", "codellama"], "coding"),
    (["r1", "o1", "think", "reason"], "reasoning"),
]


def _parse_size_bytes(size_str: str) -> int:
    """Parse '4.7 GB', '274 MB', '39 GB' → bytes."""
    parts = size_str.strip().split()
    if len(parts) < 2:
        return 0
    try:
        val = float(parts[0])
        unit = parts[1].upper()
        multipliers = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
        return int(val * multipliers.get(unit, 1))
    except (ValueError, KeyError):
        return 0


class OllamaModels:
    """Interface to local Ollama model management."""

    # Class-level cache shared across instances — models change rarely
    _list_local_cache: tuple[float, list[dict]] | None = None
    _LIST_LOCAL_TTL = 60.0  # seconds
    _list_local_lock = threading.Lock()

    @classmethod
    def _invalidate_list_cache(cls) -> None:
        """Call after pulling/deleting a model to force a fresh fetch."""
        cls._list_local_cache = None

    @classmethod
    def list_local(cls) -> list[dict]:
        """Run `ollama list` and return [{name, size_bytes, modified}] with TTL cache.

        Returns empty list if ollama is not available.
        """
        with cls._list_local_lock:
            now = time.monotonic()
            if OllamaModels._list_local_cache is not None:
                ts, val = OllamaModels._list_local_cache
                if now - ts < OllamaModels._LIST_LOCAL_TTL:
                    return val
            result = cls._fetch_list_local()
            OllamaModels._list_local_cache = (now, result)
            return result

    @classmethod
    def _fetch_list_local(cls) -> list[dict]:
        """Run `ollama list` and parse output (uncached)."""
        try:
            result = subprocess.run(
                ["ollama", "list"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                _log.warning("ollama list returned %d", result.returncode)
                return []
            lines = result.stdout.strip().split("\n")
            if len(lines) < 2:
                return []
            # Skip header line
            models = []
            for line in lines[1:]:
                parts = line.split()
                if len(parts) < 4:
                    continue
                name = parts[0]
                # SIZE is parts[2] + parts[3] (e.g. "4.7" + "GB")
                size_str = f"{parts[2]} {parts[3]}"
                models.append(
                    {
                        "name": name,
                        "size_bytes": _parse_size_bytes(size_str),
                        "modified": " ".join(parts[4:]) if len(parts) > 4 else "",
                    }
                )
            return models
        except (OSError, subprocess.TimeoutExpired):
            _log.warning("ollama list failed — ollama may not be running")
            return []

    def get_loaded(self) -> list[dict]:
        """Run `ollama ps` and return all loaded models.

        Returns [{name, size_bytes, vram_pct, cpu_pct, until}].
        """
        try:
            result = subprocess.run(
                ["ollama", "ps"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return []
            lines = result.stdout.strip().split("\n")
            if len(lines) < 2:
                return []
            loaded = []
            for line in lines[1:]:
                parts = line.split()
                if len(parts) < 3:
                    continue
                name = parts[0]
                size_str = f"{parts[2]} {parts[3]}" if len(parts) > 3 else f"{parts[2]} B"
                vram_pct = 0.0
                cpu_pct = 0.0
                processor_str = parts[4] if len(parts) > 4 else ""
                if "/" in processor_str:
                    halves = processor_str.split("/")
                    try:
                        cpu_pct = float(halves[0].strip("%"))
                        vram_pct = float(halves[1].strip("%"))
                    except ValueError:
                        pass
                elif processor_str.endswith("%"):
                    try:
                        vram_pct = float(processor_str.strip("%"))
                    except ValueError:
                        pass
                loaded.append(
                    {
                        "name": name,
                        "size_bytes": _parse_size_bytes(size_str),
                        "vram_pct": vram_pct,
                        "cpu_pct": cpu_pct,
                        "until": " ".join(parts[5:]) if len(parts) > 5 else "",
                    }
                )
            return loaded
        except (OSError, subprocess.TimeoutExpired):
            return []

    def classify(self, model_name: str) -> dict:
        """Return {resource_profile, type_tag} based on model name heuristics."""
        name_lower = model_name.lower()

        resource_profile = "ollama"
        for keywords, profile in _PROFILE_RULES:
            if any(kw in name_lower for kw in keywords):
                resource_profile = profile
                break

        type_tag = "general"
        for keywords, tag in _TYPE_RULES:
            if any(kw in name_lower for kw in keywords):
                type_tag = tag
                break

        return {"resource_profile": resource_profile, "type_tag": type_tag}

    def min_estimated_vram_mb(self, db: Database, fallback_mb: int = 0) -> int:
        """Return the minimum VRAM estimate (MB) across all models in model_registry.

        Issues a single batch query for all registry rows instead of calling
        estimate_vram_mb() N times (which would issue N separate DB + settings queries).
        VRAM estimation logic mirrors estimate_vram_mb() exactly:
          1. vram_observed_mb if present
          2. size_bytes / 1_000_000 * vram_safety_factor
          3. 2000 MB hardcoded floor for unknown models

        Args:
            db: Database instance to query model_registry and settings.
            fallback_mb: If greater than the catalog minimum, this value is returned instead.
                         Use to enforce a floor (e.g., from a min_model_vram_mb setting).

        Returns:
            Minimum estimated VRAM in MB as int. Falls back to 2000 if registry is empty.
        """
        with db._lock:
            conn = db._connect()
            rows = conn.execute("SELECT vram_observed_mb, size_bytes FROM model_registry").fetchall()
            setting_row = conn.execute("SELECT value FROM settings WHERE key = 'vram_safety_factor'").fetchone()

        if not rows:
            return max(fallback_mb, 2000)

        safety = float(setting_row["value"]) if setting_row else 1.3

        estimates = []
        for row in rows:
            if row["vram_observed_mb"]:
                estimates.append(int(row["vram_observed_mb"]))
            elif row["size_bytes"]:
                estimates.append(int((row["size_bytes"] / 1_000_000) * safety))
            else:
                estimates.append(2000)

        return max(int(min(estimates)), fallback_mb)

    def estimate_vram_mb(self, model_name: str, db: Database) -> float:
        """Return estimated VRAM in MB.

        Priority: observed value in model_registry → disk size × safety factor → 4000 MB default.
        """
        # Both reads in one lock scope — prevents TOCTOU between registry and safety factor reads.
        with db._lock:
            conn = db._connect()
            row = conn.execute(
                "SELECT vram_observed_mb, size_bytes FROM model_registry WHERE name = ?",
                (model_name,),
            ).fetchone()
            setting_row = conn.execute("SELECT value FROM settings WHERE key = 'vram_safety_factor'").fetchone()

        if row and row["vram_observed_mb"]:
            return float(row["vram_observed_mb"])

        safety = float(setting_row["value"]) if setting_row else 1.3

        # Try model_registry size_bytes first
        if row and row["size_bytes"]:
            return (row["size_bytes"] / 1_000_000) * safety

        # Try list_local
        for m in self.list_local():
            if m["name"] == model_name and m["size_bytes"]:
                return (m["size_bytes"] / 1_000_000) * safety

        return 4000.0  # 4 GB unknown default

    def record_observed_vram(self, model_name: str, vram_mb: float, db: Database) -> None:
        """Update model_registry with observed VRAM using EMA (α=0.3)."""
        with db._lock:
            conn = db._connect()
            row = conn.execute(
                "SELECT vram_observed_mb FROM model_registry WHERE name = ?",
                (model_name,),
            ).fetchone()
            if row and row["vram_observed_mb"]:
                new_val = 0.3 * vram_mb + 0.7 * float(row["vram_observed_mb"])
            else:
                new_val = vram_mb
            conn.execute(
                """INSERT INTO model_registry (name, vram_observed_mb, last_seen)
                   VALUES (?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                       vram_observed_mb = excluded.vram_observed_mb,
                       last_seen = excluded.last_seen""",
                (model_name, new_val, time.time()),
            )
            conn.commit()

    def refresh_registry(self, db: Database) -> None:
        """Sync model_registry with current `ollama list` output."""
        models = self.list_local()
        now = time.time()
        with db._lock:
            conn = db._connect()
            for m in models:
                classification = self.classify(m["name"])
                conn.execute(
                    """INSERT INTO model_registry
                           (name, size_bytes, resource_profile, type_tag, last_seen)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(name) DO UPDATE SET
                           size_bytes = excluded.size_bytes,
                           resource_profile = excluded.resource_profile,
                           type_tag = excluded.type_tag,
                           last_seen = excluded.last_seen""",
                    (
                        m["name"],
                        m["size_bytes"],
                        classification["resource_profile"],
                        classification["type_tag"],
                        now,
                    ),
                )
            conn.commit()

    # --- Pull lifecycle ---

    def pull(self, model_name: str, db: Database) -> int:
        """Start `ollama pull <model>` in background. Returns pull_id."""
        now = time.time()
        with db._lock:
            conn = db._connect()
            conn.execute(
                "INSERT INTO model_pulls (model, status, progress_pct, started_at) VALUES (?,?,?,?)",
                (model_name, "pulling", 0.0, now),
            )
            conn.commit()
            pull_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        proc = subprocess.Popen(
            ["ollama", "pull", model_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        with db._lock:
            conn = db._connect()
            conn.execute("UPDATE model_pulls SET pid = ? WHERE id = ?", (proc.pid, pull_id))
            conn.commit()

        def _monitor() -> None:
            try:
                for line in proc.stdout:  # type: ignore[union-attr]
                    if "%" in line:
                        try:
                            pct_raw = next((p.rstrip("%") for p in line.split() if p.endswith("%")), None)
                            if pct_raw is not None:
                                pct = float(pct_raw)
                                try:
                                    with db._lock:
                                        conn = db._connect()
                                        conn.execute(
                                            "UPDATE model_pulls SET progress_pct = ? WHERE id = ?",
                                            (pct, pull_id),
                                        )
                                        conn.commit()
                                except Exception as exc:
                                    _log.error("Pull monitor: failed to update progress for %s: %s", model_name, exc)
                        except (ValueError, IndexError):
                            pass
                proc.wait()
                status = "completed" if proc.returncode == 0 else "failed"
                if status == "completed":
                    OllamaModels._invalidate_list_cache()
            except Exception as exc:
                status = "failed"
                _log.error("pull monitor error: %s", exc)
            try:
                with db._lock:
                    conn = db._connect()
                    conn.execute(
                        "UPDATE model_pulls SET status=?, completed_at=?, progress_pct=? WHERE id=?",
                        (status, time.time(), 100.0 if status == "completed" else None, pull_id),
                    )
                    conn.commit()
            except Exception:
                _log.exception("Failed to update pull status for model %s to %s", model_name, status)

        t = threading.Thread(target=_monitor, daemon=True, name=f"pull-{pull_id}")
        t.start()
        # Lesson #43: add done_callback for error visibility on daemon threads
        # (threading.Thread doesn't support callbacks natively — _monitor handles its own logging)
        return pull_id

    def get_pull_status(self, pull_id: int, db: Database) -> dict:
        """Return pull progress dict."""
        with db._lock:
            row = db._connect().execute("SELECT * FROM model_pulls WHERE id = ?", (pull_id,)).fetchone()
        if not row:
            return {"error": "not found"}
        return dict(row)

    def cancel_pull(self, pull_id: int, db: Database) -> bool:
        """SIGTERM the pull process and mark cancelled."""
        with db._lock:
            row = db._connect().execute("SELECT pid FROM model_pulls WHERE id = ?", (pull_id,)).fetchone()
        if not row or not row["pid"]:
            return False
        try:
            os.kill(row["pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass
        with db._lock:
            conn = db._connect()
            conn.execute(
                "UPDATE model_pulls SET status='cancelled', completed_at=? WHERE id=?",
                (time.time(), pull_id),
            )
            conn.commit()
        return True
