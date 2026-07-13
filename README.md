# UniProtPy

A Python client and release-aware local SQLite cache for the current UniProt REST API.

UniProtPy preserves the complete nested UniProtKB JSON document while adding convenient accessors and indexed local queries. It models proteins and proteomes directly; it does not force UniProt data into a genomic interval schema.

## Installation

```bash
pip install uniprotpy
```

## Fetch an entry

```python
from uniprotpy import UniProtClient

client = UniProtClient()
response = client.get_entry("P04637")
entry = response.entry

print(entry.primary_protein_name)
print(entry.primary_gene_name)
print(entry.sequence)

fasta = client.get_entry_text("P04637", format="fasta").text
```

`entry.to_dict()` returns the faithful source JSON, including features, comments, cross-references, keywords, references, evidence, and fields unknown to this library version.

## Command line

```text
uniprotpy entry get P04637 --format json --output p53.json
uniprotpy entry get P04637 --format tsv --fields accession,gene_name,organism
uniprotpy install entries P04637 P38398 --release 2026_02
uniprotpy install proteome UP000005640 --release 2026_02
uniprotpy query --release 2026_02 --gene TP53 --format fasta
uniprotpy query --release 2026_02 --all --format tsv --fields accession,protein_name
```

Entry fetches support faithful source JSON, API-provided FASTA, and field-selected TSV. Cached queries support accession, exact gene or primary protein name, installed proteome, or all entries. TSV fields may name stable accessors (`accession`, `canonical_accession`, `uniprotkb_id`, `reviewed`, `protein_name`, `gene_name`, `taxon_id`, `sequence`), raw top-level keys, or dotted raw paths; nested values are JSON-encoded. Output defaults to stdout; pass `--output` to write UTF-8 text to a file.

## Install and query a release cache

```python
from uniprotpy import UniProtRelease

release = UniProtRelease("2026_02")
release.install_entries(["P04637", "P38398"])

p53 = release.entry("P04637")
tp53_entries = release.entries_by_gene("TP53")
named_entries = release.entries_by_name("Cellular tumor antigen p53")
release.close()
```

Set `UNIPROTPY_CACHE_DIR` to override the platform cache root, or pass `cache_dir=` explicitly. Data are stored under a release-specific directory. The observed `X-UniProt-Release` response header is checked during installation.

## Proteomes

```python
from uniprotpy import ProteomeSelector, UniProtClient, UniProtRelease

client = UniProtClient()
proteome = client.get_proteome("UP000005640").proteome

candidates = client.reference_proteomes(9606, scope="exact")
selection = ProteomeSelector().select(candidates)

release = UniProtRelease("2026_02", client=client)
release.install_proteome(proteome.upid)
```

Taxonomy-based proteome searches are lineage-aware. Exact scope filters descendant taxa. UniProt can intentionally designate multiple reference proteomes, so selection returns typed unique, ambiguous, or not-found results rather than silently treating API order or protein count as “best.”

Proteome installation uses cursor-paginated UniProtKB bulk search and persists rich entry JSON, proteome membership, release metadata, and provenance. It does not issue one direct request per protein.

## Taxonomy and ID Mapping

```python
from uniprotpy import UniProtClient

client = UniProtClient()

taxon = client.get_taxon(9606).taxon
print(taxon.scientific_name, taxon.lineage)

# Mapping capabilities and valid pairs are discovered from UniProt, not hard-coded.
configuration = client.get_id_mapping_configuration()
job = client.submit_id_mapping(
    "Gene_Name",
    "UniProtKB",
    ["TP53", "BRCA1"],
    taxon_id=9606,
    configuration=configuration,
)
result = client.wait_for_mapping(job)
print(result.results, result.failed_ids, result.warnings)
```

Taxonomy entry retrieval preserves merged-taxonomy `303` bodies and redirect metadata; reference-proteome discovery resolves merged IDs before querying. Taxonomy search and ID Mapping results follow opaque absolute cursor links verbatim. ID Mapping submission is never retried, status polling does not auto-follow the service's `303`, and result retrieval uses the authoritative details redirect while preserving failed IDs, warnings, enriched targets, raw payloads, and response metadata. `wait_for_mapping` is synchronous polling over UniProt's server-asynchronous job API.

## External FASTA artifacts

```python
from uniprotpy import parse_fasta_handle, parse_fasta_path, parse_fasta_text

path_records = parse_fasta_path("proteins.fasta")
text_records = parse_fasta_text(">P12345 example\nMPEPTIDE\n")
with open("proteins.fasta", encoding="utf-8") as handle:
    handle_records = parse_fasta_handle(handle)
```

The three explicit source functions never guess whether a string is a path or literal FASTA. They return `FastaRecord` artifacts without inventing missing UniProt annotations. Rich `UniProtEntry` JSON remains the authoritative entry model.

## Current scope

Shipped: rich entry JSON and FASTA retrieval, resilient cursor pagination, lossless entry/proteome/taxonomy domains, release-scoped SQLite caching, indexed accession/gene/name queries, proteome metadata/discovery/selection, merged-taxon canonicalization, bulk proteome installation, asynchronous-service ID Mapping discovery/submit/poll/paginated results, explicit external FASTA parsing, and the entry/install/cached-query CLI with faithful JSON/FASTA/selected-TSV output.

The exploratory direct-request helpers, lossy flat FASTA entry schema, stateful selector wrapper, compatibility database/selector spellings, unbounded taxonomy tree utility, silent output modes, and hard-coded output destinations have been removed.