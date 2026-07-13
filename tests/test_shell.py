from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from uniprotpy import shell
from uniprotpy.models import UniProtEntry


class FakeClient:
  def __init__(self, *, entry=None, fasta_text=None):
    self.entry = entry
    self.fasta_text = fasta_text
    self.calls = []

  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc_value, traceback):
    return None

  def get_entry(self, accession):
    self.calls.append(("get_entry", accession))
    return SimpleNamespace(entry=self.entry)

  def get_entry_text(self, accession, format):
    self.calls.append(("get_entry_text", accession, format))
    return SimpleNamespace(text=self.fasta_text)


class FakeStore:
  def __init__(self, release):
    self.release = release

  def entries_for_proteome(self, upid):
    self.release.calls.append(("entries_for_proteome", upid))
    return list(self.release.results[("entries_for_proteome", upid)])

  def list(self):
    self.release.calls.append(("list",))
    return list(self.release.results[("list",)])


class FakeRelease:
  def __init__(self, *, results=None, install_counts=None):
    self.results = results or {}
    self.install_counts = install_counts or {}
    self.calls = []
    self.database_path = Path("cache-root/2026_02/entries.sqlite3")
    self.store = FakeStore(self)

  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc_value, traceback):
    return None

  def entry(self, accession):
    self.calls.append(("entry", accession))
    return self.results[("entry", accession)]

  def entries_by_gene(self, gene):
    self.calls.append(("entries_by_gene", gene))
    return list(self.results[("entries_by_gene", gene)])

  def entries_by_name(self, name):
    self.calls.append(("entries_by_name", name))
    return list(self.results[("entries_by_name", name)])

  def install_entries(self, accessions):
    values = tuple(accessions)
    self.calls.append(("install_entries", values))
    return self.install_counts[("install_entries", values)]

  def install_proteome(self, upid):
    self.calls.append(("install_proteome", upid))
    return self.install_counts[("install_proteome", upid)]


def patch_client(monkeypatch, client):
  monkeypatch.setattr(shell, "UniProtClient", lambda: client)


def patch_release(monkeypatch, release):
  constructor_calls = []

  def factory(release_name, cache_dir=None):
    constructor_calls.append((release_name, cache_dir))
    return release

  monkeypatch.setattr(shell, "UniProtRelease", factory)
  return constructor_calls


def test_direct_entry_json_preserves_the_complete_raw_document(monkeypatch):
  document = {
    "primaryAccession": "P04637",
    "proteinDescription": {"recommendedName": {"fullName": {"value": "p53 β"}}},
    "futureSchemaField": {"nested": [{"flag": True, "score": 0.125}]},
  }
  client = FakeClient(entry=UniProtEntry(document))
  patch_client(monkeypatch, client)
  stdout = StringIO()

  shell.run(
    ["entry", "get", "P04637", "--format", "json"],
    stdout=stdout,
  )

  assert stdout.getvalue() == (
    "{\n"
    '  "primaryAccession": "P04637",\n'
    '  "proteinDescription": {\n'
    '    "recommendedName": {\n'
    '      "fullName": {\n'
    '        "value": "p53 β"\n'
    "      }\n"
    "    }\n"
    "  },\n"
    '  "futureSchemaField": {\n'
    '    "nested": [\n'
    "      {\n"
    '        "flag": true,\n'
    '        "score": 0.125\n'
    "      }\n"
    "    ]\n"
    "  }\n"
    "}\n"
  )
  assert client.calls == [("get_entry", "P04637")]


def test_direct_fasta_writes_the_api_text_unchanged_to_output_file(
  monkeypatch, tmp_path
):
  fasta_text = ">sp|P04637|P53_HUMAN API-owned header\nMEEPQSDPSV EPPLSQETF\n"
  client = FakeClient(fasta_text=fasta_text)
  patch_client(monkeypatch, client)
  output = tmp_path / "entry.fasta"
  stdout = StringIO()

  shell.run(
    [
      "entry", "get", "P04637", "--format", "fasta",
      "--output", str(output),
    ],
    stdout=stdout,
  )

  assert output.read_bytes() == fasta_text.encode("utf-8")
  assert stdout.getvalue() == ""
  assert client.calls == [("get_entry_text", "P04637", "fasta")]


def test_entry_tsv_emits_selected_scalar_and_nested_raw_fields(monkeypatch):
  entry = UniProtEntry({
    "primaryAccession": "Q9TEST",
    "entryType": "UniProtKB reviewed (Swiss-Prot)",
    "organism": {"scientificName": "Mäus musculus", "taxonId": 10090},
    "future": {"labels": ["α", {"rank": 2}]},
  })
  client = FakeClient(entry=entry)
  patch_client(monkeypatch, client)
  stdout = StringIO()

  shell.run(
    [
      "entry", "get", "Q9TEST", "--format", "tsv", "--fields",
      "accession,reviewed,organism.taxonId,future.labels",
    ],
    stdout=stdout,
  )

  assert stdout.getvalue() == (
    "accession\treviewed\torganism.taxonId\tfuture.labels\n"
    'Q9TEST\ttrue\t10090\t["α",{"rank":2}]\n'
  )
  assert client.calls == [("get_entry", "Q9TEST")]


@pytest.mark.parametrize(
  "argv,install_counts,expected_call,expected_output",
  [
    (
      [
        "install", "entries", "P04637", "P38398",
        "--release", "2026_02", "--cache-dir", "cache-root",
      ],
      {("install_entries", ("P04637", "P38398")): 2},
      ("install_entries", ("P04637", "P38398")),
      "Installed 2 entries into cache-root/2026_02/entries.sqlite3\n",
    ),
    (
      [
        "install", "proteome", "UP000005640",
        "--release", "2026_02", "--cache-dir", "cache-root",
      ],
      {("install_proteome", "UP000005640"): 20421},
      ("install_proteome", "UP000005640"),
      "Installed 20421 entries into cache-root/2026_02/entries.sqlite3\n",
    ),
  ],
  ids=("entries", "proteome"),
)
def test_install_commands_delegate_and_report_the_installed_count(
  monkeypatch, argv, install_counts, expected_call, expected_output
):
  release = FakeRelease(install_counts=install_counts)
  constructor_calls = patch_release(monkeypatch, release)
  stdout = StringIO()

  shell.run(argv, stdout=stdout)

  assert release.calls == [expected_call]
  assert constructor_calls == [("2026_02", Path("cache-root"))]
  assert stdout.getvalue() == expected_output


QUERY_DOCUMENT = {
  "primaryAccession": "P04637",
  "unknown": {"labels": ["α", None], "enabled": True},
}
QUERY_JSON = (
  "[\n"
  "  {\n"
  '    "primaryAccession": "P04637",\n'
  '    "unknown": {\n'
  '      "labels": [\n'
  '        "α",\n'
  "        null\n"
  "      ],\n"
  '      "enabled": true\n'
  "    }\n"
  "  }\n"
  "]\n"
)


@pytest.mark.parametrize(
  "selector,result_key,expected_call",
  [
    (["--accession", "P04637"], ("entry", "P04637"), ("entry", "P04637")),
    (["--gene", "TP53"], ("entries_by_gene", "TP53"), ("entries_by_gene", "TP53")),
    (
      ["--name", "Cellular tumor antigen p53"],
      ("entries_by_name", "Cellular tumor antigen p53"),
      ("entries_by_name", "Cellular tumor antigen p53"),
    ),
    (
      ["--proteome", "UP000005640"],
      ("entries_for_proteome", "UP000005640"),
      ("entries_for_proteome", "UP000005640"),
    ),
    (["--all"], ("list",), ("list",)),
  ],
  ids=("accession", "gene", "name", "proteome", "all"),
)
def test_query_selectors_emit_a_faithful_json_list_from_the_matching_cache_boundary(
  monkeypatch, selector, result_key, expected_call
):
  entry = UniProtEntry(QUERY_DOCUMENT)
  configured_result = entry if result_key[0] == "entry" else [entry]
  release = FakeRelease(results={result_key: configured_result})
  constructor_calls = patch_release(monkeypatch, release)
  stdout = StringIO()

  shell.run(
    [
      "query", "--release", "2026_02", "--cache-dir", "cache-root",
      *selector, "--format", "json",
    ],
    stdout=stdout,
  )

  assert stdout.getvalue() == QUERY_JSON
  assert release.calls == [expected_call]
  assert constructor_calls == [("2026_02", Path("cache-root"))]


def test_cached_fasta_preserves_store_order_and_wraps_sequences_at_sixty_columns(
  monkeypatch,
):
  first = UniProtEntry({
    "primaryAccession": "P00001",
    "proteinDescription": {
      "recommendedName": {"fullName": {"value": "Long protein"}},
    },
    "sequence": {"value": "A" * 61},
  })
  second = UniProtEntry({
    "primaryAccession": "P00002",
    "sequence": {"value": "MPEP"},
  })
  release = FakeRelease(results={("list",): [first, second]})
  patch_release(monkeypatch, release)
  stdout = StringIO()

  shell.run(
    ["query", "--release", "2026_02", "--all", "--format", "fasta"],
    stdout=stdout,
  )

  assert stdout.getvalue() == (
    ">P00001 Long protein\n"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
    "A\n"
    ">P00002\n"
    "MPEP\n"
  )
  assert release.calls == [("list",)]


def test_cached_tsv_emits_selected_fields_and_json_encodes_nested_values(
  monkeypatch,
):
  entries = [
    UniProtEntry({
      "primaryAccession": "P04637",
      "entryType": "UniProtKB reviewed (Swiss-Prot)",
      "metadata": {"labels": ["tumor", {"rank": 1}]},
    }),
    UniProtEntry({
      "primaryAccession": "Q9TEST",
      "entryType": "UniProtKB unreviewed (TrEMBL)",
      "metadata": {"labels": ["β"]},
    }),
  ]
  key = ("entries_by_gene", "TP53")
  release = FakeRelease(results={key: entries})
  patch_release(monkeypatch, release)
  stdout = StringIO()

  shell.run(
    [
      "query", "--release", "2026_02", "--gene", "TP53",
      "--format", "tsv", "--fields", "accession,reviewed,metadata.labels",
    ],
    stdout=stdout,
  )

  assert stdout.getvalue() == (
    "accession\treviewed\tmetadata.labels\n"
    'P04637\ttrue\t["tumor",{"rank":1}]\n'
    'Q9TEST\tfalse\t["β"]\n'
  )
  assert release.calls == [("entries_by_gene", "TP53")]


def test_tsv_rejects_a_request_without_explicit_fields(monkeypatch, capsys):
  key = ("entry", "P04637")
  release = FakeRelease(results={key: UniProtEntry(QUERY_DOCUMENT)})
  patch_release(monkeypatch, release)

  with pytest.raises(SystemExit) as raised:
    shell.run([
      "query", "--release", "2026_02", "--accession", "P04637",
      "--format", "tsv",
    ])

  captured = capsys.readouterr()
  assert raised.value.code == 2
  assert captured.out == ""
  assert captured.err.splitlines()[-1] == (
    "uniprotpy: error: --fields is required when --format tsv"
  )
