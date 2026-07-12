"""Deterministic cache-directory resolution for UniProtPy."""

from pathlib import Path
import os
import sys
from typing import Optional, Union


_CACHE_ENVIRONMENT_VARIABLE = "UNIPROTPY_CACHE_DIR"


def _platform_cache_root() -> Path:
  if sys.platform == "darwin":
    return Path.home() / "Library" / "Caches"
  if os.name == "nt":
    configured = os.environ.get("LOCALAPPDATA")
    if configured:
      return Path(configured).expanduser()
    return Path.home() / "AppData" / "Local"
  configured = os.environ.get("XDG_CACHE_HOME")
  if configured:
    return Path(configured).expanduser()
  return Path.home() / ".cache"


def resolve_cache_dir(
  explicit: Optional[Union[str, os.PathLike]] = None,
) -> Path:
  """Resolve the cache root without creating or inspecting the directory.

  An explicit path wins over ``UNIPROTPY_CACHE_DIR``. If neither is set, the
  conventional per-user cache location for the current platform is used.
  """
  if explicit is not None:
    return Path(explicit).expanduser()
  configured = os.environ.get(_CACHE_ENVIRONMENT_VARIABLE)
  if configured:
    return Path(configured).expanduser()
  return _platform_cache_root() / "uniprotpy"
