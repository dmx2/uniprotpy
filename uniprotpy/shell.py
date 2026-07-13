"""Command-line interface over the UniProt client and release cache."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping, Optional, Sequence, TextIO

from .client import UniProtClient
from .models import UniProtEntry
from .release import UniProtRelease


_TSV_ACCESSORS = {
  "accession": "accession",
  "primary_accession": "primary_accession",
  "canonical_accession": "canonical_accession",
  "uniprotkb_id": "uniprotkb_id",
  "reviewed": "reviewed",
  "protein_name": "primary_protein_name",
  "gene_name": "primary_gene_name",
  "taxon_id": "taxon_id",
  "sequence": "sequence",
}


def _add_output_options(parser: argparse.ArgumentParser) -> None:
  parser.add_argument(
    "--format", choices=("json", "fasta", "tsv"), default="json"
  )
  parser.add_argument(
    "--fields",
    help="Comma-separated TSV fields (accessors, raw keys, or dotted raw paths).",
  )
  parser.add_argument(
    "-o", "--output", type=Path, help="Output file; stdout when omitted."
  )


def _add_release_options(parser: argparse.ArgumentParser) -> None:
  parser.add_argument("--release", required=True, help="UniProt data release.")
  parser.add_argument("--cache-dir", type=Path, help="Release cache root.")


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(prog="uniprotpy", description="UniProtPy")
  commands = parser.add_subparsers(dest="command", required=True)

  entry = commands.add_parser("entry", help="Fetch individual UniProtKB entries.")
  entry_commands = entry.add_subparsers(dest="entry_command", required=True)
  entry_get = entry_commands.add_parser("get", help="Fetch one entry.")
  entry_get.add_argument("accession")
  _add_output_options(entry_get)

  install = commands.add_parser("install", help="Install data into a release cache.")
  install_commands = install.add_subparsers(dest="install_command", required=True)
  install_entries = install_commands.add_parser(
    "entries", help="Install fewer than 50 individual entries."
  )
  install_entries.add_argument("accessions", nargs="+")
  _add_release_options(install_entries)
  install_proteome = install_commands.add_parser(
    "proteome", help="Install a complete proteome."
  )
  install_proteome.add_argument("upid")
  _add_release_options(install_proteome)

  query = commands.add_parser("query", help="Query an installed release cache.")
  _add_release_options(query)
  selector = query.add_mutually_exclusive_group(required=True)
  selector.add_argument("--accession")
  selector.add_argument("--gene")
  selector.add_argument("--name", help="Exact primary protein name.")
  selector.add_argument("--proteome", help="Installed proteome UPID.")
  selector.add_argument("--all", action="store_true", help="Return every cached entry.")
  _add_output_options(query)
  return parser


def parse_arguments(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
  return build_parser().parse_args(argv)


def _fields(value: Optional[str], parser: argparse.ArgumentParser) -> list[str]:
  if value is None:
    parser.error("--fields is required when --format tsv")
  fields = [field.strip() for field in value.split(",") if field.strip()]
  if not fields:
    parser.error("--fields must contain at least one field")
  return fields


def _field_value(entry: UniProtEntry, field: str) -> Any:
  accessor = _TSV_ACCESSORS.get(field)
  if accessor is not None:
    return getattr(entry, accessor)
  value: Any = entry.raw
  for part in field.split("."):
    if not isinstance(value, Mapping) or part not in value:
      return None
    value = value[part]
  return value


def _tsv_value(value: Any) -> str:
  if value is None:
    return ""
  if isinstance(value, bool):
    return "true" if value else "false"
  if isinstance(value, (Mapping, list, tuple)):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
  return str(value)


def _json_text(entries: Sequence[UniProtEntry], *, single: bool) -> str:
  value: Any = entries[0].to_dict() if single else [entry.to_dict() for entry in entries]
  return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _fasta_text(entries: Iterable[UniProtEntry]) -> str:
  records = []
  for entry in entries:
    if not entry.accession or entry.sequence is None:
      raise ValueError("cached entry is missing an accession or sequence")
    description = " " + entry.primary_protein_name if entry.primary_protein_name else ""
    sequence = entry.sequence
    lines = [sequence[offset:offset + 60] for offset in range(0, len(sequence), 60)]
    records.append(">{}{}\n{}".format(entry.accession, description, "\n".join(lines)))
  return "\n".join(records) + ("\n" if records else "")


def _tsv_text(entries: Iterable[UniProtEntry], fields: Sequence[str]) -> str:
  lines = ["\t".join(fields)]
  lines.extend(
    "\t".join(_tsv_value(_field_value(entry, field)) for field in fields)
    for entry in entries
  )
  return "\n".join(lines) + "\n"


def _write(text: str, output: Optional[Path], stdout: TextIO) -> None:
  if output is None:
    stdout.write(text)
  else:
    output.write_text(text, encoding="utf-8")


def _cached_entries(release: UniProtRelease, args: argparse.Namespace) -> list[UniProtEntry]:
  if args.accession is not None:
    entry = release.entry(args.accession)
    return [] if entry is None else [entry]
  if args.gene is not None:
    return release.entries_by_gene(args.gene)
  if args.name is not None:
    return release.entries_by_name(args.name)
  if args.proteome is not None:
    return release.store.entries_for_proteome(args.proteome)
  return release.store.list()


def _render_entries(
  entries: Sequence[UniProtEntry],
  args: argparse.Namespace,
  fields: Optional[Sequence[str]],
) -> str:
  if args.format == "json":
    return _json_text(entries, single=False)
  if args.format == "fasta":
    return _fasta_text(entries)
  if fields is None:
    raise ValueError("TSV fields were not validated")
  return _tsv_text(entries, fields)


def run(
  argv: Optional[Sequence[str]] = None,
  *,
  stdout: Optional[TextIO] = None,
) -> None:
  parser = build_parser()
  args = parser.parse_args(argv)
  destination = stdout if stdout is not None else sys.stdout
  fields: Optional[list[str]] = None
  if args.command != "install":
    if args.format == "tsv":
      fields = _fields(args.fields, parser)
    elif args.fields is not None:
      parser.error("--fields is only valid with --format tsv")

  try:
    if args.command == "entry":
      with UniProtClient() as client:
        if args.format == "fasta":
          text = client.get_entry_text(args.accession, format="fasta").text
          if text and not text.endswith("\n"):
            text += "\n"
        else:
          entry = client.get_entry(args.accession).entry
          if args.format == "json":
            text = _json_text([entry], single=True)
          else:
            text = _tsv_text([entry], fields or ())
      _write(text, args.output, destination)
      return

    with UniProtRelease(args.release, cache_dir=args.cache_dir) as release:
      if args.command == "install":
        count = (
          release.install_entries(args.accessions)
          if args.install_command == "entries"
          else release.install_proteome(args.upid)
        )
        destination.write("Installed {} entries into {}\n".format(count, release.database_path))
        return

      entries = _cached_entries(release, args)
      _write(_render_entries(entries, args, fields), args.output, destination)
  except (OSError, RuntimeError, TypeError, ValueError) as error:
    parser.error(str(error))
