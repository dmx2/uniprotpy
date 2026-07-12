import json
from pathlib import Path

import pytest

from uniprotpy.cache import resolve_cache_dir
from uniprotpy.client import EntryResponse, ResponseMetadata
from uniprotpy.models import UniProtEntry
from uniprotpy.release import ReleaseMismatchError, UniProtRelease


FIXTURES = Path(__file__).parent / "fixtures"


class FakeClient:
  def __init__(self, responses=None):
    self.responses = responses or {}
    self.calls = []

  def get_entry(self, accession):
    self.calls.append(accession)
    if accession not in self.responses:
      raise AssertionError("unexpected client I/O for {}".format(accession))
    return self.responses[accession]


def entry_response(release="2026_02"):
  with (FIXTURES / "p04637.json").open(encoding="utf-8") as handle:
    entry = UniProtEntry.from_json(json.load(handle))
  return EntryResponse(
    entry=entry,
    metadata=ResponseMetadata(
      release=release,
      release_date="2026-02-18",
      total_results=None,
      url="https://rest.uniprot.org/uniprotkb/P04637.json",
      content_type="application/json",
    ),
  )


def test_cache_root_precedence_and_platform_default_shape(tmp_path, monkeypatch):
  explicit = tmp_path / "explicit"
  configured = tmp_path / "configured"
  platform_root = tmp_path / "platform-cache"
  monkeypatch.setenv("UNIPROTPY_CACHE_DIR", str(configured))
  monkeypatch.setenv("XDG_CACHE_HOME", str(platform_root))
  monkeypatch.setattr("uniprotpy.cache.sys.platform", "linux")

  assert resolve_cache_dir(explicit) == explicit
  assert resolve_cache_dir() == configured

  monkeypatch.delenv("UNIPROTPY_CACHE_DIR")
  assert resolve_cache_dir() == platform_root / "uniprotpy"
  assert not explicit.exists()
  assert not configured.exists()
  assert not platform_root.exists()


def test_constructor_performs_no_client_or_filesystem_io(tmp_path):
  cache_root = tmp_path / "cache"
  client = FakeClient()

  release = UniProtRelease("2026_02", cache_dir=cache_root, client=client)

  assert client.calls == []
  assert release.cache_root == cache_root
  assert release.cache_dir == cache_root / "2026_02"
  assert release.database_path == cache_root / "2026_02" / "entries.sqlite3"
  assert not cache_root.exists()


def test_install_rejects_an_observed_release_mismatch_without_creating_cache(tmp_path):
  cache_root = tmp_path / "cache"
  client = FakeClient({"P04637": entry_response(release="2026_01")})
  release = UniProtRelease("2026_02", cache_dir=cache_root, client=client)

  with pytest.raises(ReleaseMismatchError, match="2026_02.*2026_01"):
    release.install_entries(["P04637"])

  assert client.calls == ["P04637"]
  assert not cache_root.exists()


def test_explicit_install_close_and_reopen_supports_cached_queries(tmp_path):
  cache_root = tmp_path / "cache"
  client = FakeClient({"P04637": entry_response()})
  release = UniProtRelease("2026_02", cache_dir=cache_root, client=client)

  assert release.install_entries(["P04637"]) == 1
  store = release.store
  assert release.store is store
  assert release.database_path == cache_root / "2026_02" / "entries.sqlite3"
  release.close()

  offline_client = FakeClient()
  reopened = UniProtRelease("2026_02", cache_dir=cache_root, client=offline_client)
  entry = reopened.entry("P04637")

  assert entry is not None
  assert entry.accession == "P04637"
  assert [item.accession for item in reopened.entries_by_gene("tp53")] == ["P04637"]
  assert [item.accession for item in reopened.entries_by_name(
    "cellular tumor antigen p53"
  )] == ["P04637"]
  assert reopened.store.release_metadata["observed_release"] == "2026_02"
  assert offline_client.calls == []
  reopened.close()


def test_install_entries_rejects_fifty_or_more_before_client_io(tmp_path):
  client = FakeClient()
  release = UniProtRelease("2026_02", cache_dir=tmp_path / "cache", client=client)

  with pytest.raises(ValueError, match="fewer than 50"):
    release.install_entries(["P04637"] * 50)

  assert client.calls == []
  assert not release.cache_root.exists()
