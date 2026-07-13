# UniProtPy architecture notes

This file is the project source of truth for UniProt domain findings, design decisions, shipped milestones, and the next implementation step.

## Research snapshot

Checked against the live UniProt REST service on 2026-07-12 (release header `2026_02`, release date `10-June-2026`), the UniProt Consortium's 2025 API paper, the official manuals, and the open-source REST controllers.

Primary references:

- [UniProt website API paper](https://pmc.ncbi.nlm.nih.gov/articles/PMC12230682/)
- [UniProtKB API documentation](https://www.uniprot.org/api-documentation/uniprotkb)
- [API query guide](https://github.com/ebi-uniprot/uniprot-manual/blob/main/help/api_queries.md)
- [REST response-header guide](https://github.com/ebi-uniprot/uniprot-manual/blob/main/help/rest-api-headers.md)
- [Proteome reference-selection workflow](https://github.com/ebi-uniprot/uniprot-manual/blob/main/help/ref_proteomes_workflow.md)
- [Proteome quality definitions](https://github.com/ebi-uniprot/uniprot-manual/blob/main/help/assessing_proteomes.md)
- [ID-mapping programmatic guide](https://github.com/ebi-uniprot/uniprot-manual/blob/main/help/id_mapping_prog.md)
- [Official REST implementation](https://github.com/ebi-uniprot/uniprot-rest-api)

### UniProtKB

- Swiss-Prot entries are reviewed; TrEMBL entries are unreviewed. `entryType` is retained verbatim and exposed through a convenience `reviewed` property rather than reduced to a lossy database-prefix flag.
- A canonical entry is a rich, evolving JSON document. Representative top-level fields include `primaryAccession`, `secondaryAccessions`, `uniProtkbId`, `entryType`, `entryAudit`, `annotationScore`, `proteinDescription`, `genes`, `organism`, `proteinExistence`, `sequence`, `features`, `comments`, `uniProtKBCrossReferences`, `keywords`, `references`, and `extraAttributes`.
- Names and genes are nested and plural. A single `protein_name` or `gene` column is only a search/accessor projection; it is not the source representation.
- Features are heterogeneous protein annotations with 1-based inclusive positions, position modifiers, evidence, ligands, xrefs, and alternative-sequence payloads. They are not genomic intervals.
- Comments are a tagged union (`commentType`): text comments, subcellular locations, alternative products, disease records, interactions, cofactors, and other structures cannot be flattened to one text field.
- Cross-references have database-specific ordered properties and may carry isoform context/evidence. Evidence objects recur throughout the document.
- `entryAudit` versions are distinct from the UniProt data release. `sequence` includes value, length, molecular weight, and checksums.
- Canonical isoforms are described by `ALTERNATIVE PRODUCTS` comments and VSP alternative-sequence features. Direct `/uniprotkb/{accession}-{suffix}.json` responses are materialized but deliberately smaller isoform projections; they are not equivalent to the canonical annotation document.
- Per-entry endpoints support JSON, FASTA, flat text, XML, GFF and additional formats. JSON is the faithful storage source; FASTA is a sequence/output artifact. `fields` is a projection, not an exact JSON-key whitelist.

### Retrieval behavior

- `GET /uniprotkb/{accession}.{format}` retrieves a single entry. The official guidance uses individual entry calls only for small sets (fewer than 50); larger accession sets should use ID Mapping or a bulk query.
- `GET /uniprotkb/search` is cursor-paginated (maximum page size 500). Follow the absolute `Link` URL with `rel="next"` verbatim; cursors are opaque. Preserve `X-Total-Results`.
- `GET /uniprotkb/stream` returns the full query result (up to the documented service limit) but is not resumable. Paginated search is the durable ingestion default; stream is an explicit bulk-download choice.
- Preserve `X-UniProt-Release` and `X-UniProt-Release-Date` beside every cached dataset. The release identifies data, not API/schema compatibility; tolerant parsing remains required.
- Use a descriptive User-Agent, a pooled session, explicit connect/read timeouts, and bounded retries for idempotent GET requests on 429/500/502/503/504. Honor `Retry-After`; otherwise use capped exponential backoff with jitter. Never retry semantic 4xx errors. Do not issue per-entry loops where one search/stream/mapping request suffices.

### Proteomes

- Proteomes expose entry, paginated search, and stream resources. Current proteome types are `REFERENCE`, `NON_REFERENCE`, and `EXCLUDED`. The redundant category was retired in release 2026_02 and must not be encoded as a current selection signal.
- Rich proteome JSON contains UPID, organism/taxonomy, type, components, gene/protein counts, genome assembly/representation, annotation/completeness data, BUSCO, CPD, statistics, and sometimes `panproteomeTaxon`.
- `taxonomy_id:<id>` search is lineage-aware and may return descendant strains. Exact-taxon and lineage scopes must therefore be explicit.
- UniProt may intentionally designate multiple reference proteomes for a taxon. There is no authoritative universal single-best ordering. Selection returns `NotFound`, one unique candidate, or an ambiguity set. A caller may opt into an honestly named policy such as highest BUSCO; protein count, API order, and annotation score are not silently treated as “best.”
- Pan-proteomes are release-versioned species datasets distributed by FTP; `panproteomeTaxon` is a pointer, not a proteome subtype or current REST search collection.
- GeneCentric is a separate resource with one canonical and related proteins per gene grouping. Gene priority concerns canonical gene-symbol/sequence sets, not proteome ranking.

### Taxonomy and ID Mapping

- Taxonomy has entry/search/stream resources, inactive-taxon redirects, parent/lineage/name/rank/statistics data, and `tax_id`, `parent`, and `ancestor` query fields. Taxon IDs are canonicalized before proteome selection.
- ID Mapping capabilities are discovered from `/configure/idmapping/fields`; valid source/target pairs and `taxId` support are not hard-coded.
- Mapping flow: submit `POST /idmapping/run`, poll `/idmapping/status/{jobId}`, read the authoritative redirect from `/idmapping/details/{jobId}`, then consume paginated result links (or explicitly choose stream). Polling must handle the service's 303 redirect behavior.
- Results are either simple `{from,to}` pairs or enriched target objects. Preserve failed IDs, warnings, response metadata, and the seven-day job expiry.

## Architecture decision

Use five narrow layers:

1. **`UniProtClient` transport** — endpoint construction, pooled HTTP, timeouts/retries, response errors, release metadata, cursor pagination, and streaming downloads. It returns transport responses/pages; it never writes SQLite or chooses a proteome.
2. **UniProt-native domain objects** — `UniProtEntry`, `Proteome`, `Taxon`, mapping jobs/results, and small response metadata/value objects. `UniProtEntry` retains the complete raw JSON document while exposing memoized convenience accessors for accession, canonical accession, isoform status, reviewed state, sequence, names, genes, organism/taxon, PE, versions, features, comments, xrefs, keywords, and evidence-bearing structures. Unknown fields and tagged-union variants survive round-trip.
3. **`UniProtStore`** — a release-aware SQLite store. It persists the rich nested entry data as JSON, plus a deliberately small indexed projection for real lookups. It does not normalize every nested subtype into relational tables.
4. **`UniProtRelease` handle/cache** — the ergonomic entry point, identified by release string and cache directory. Cache root precedence is explicit argument, `UNIPROTPY_CACHE_DIR`, then the platform user cache directory. It downloads/installs lazily, records source query/URL and release headers, and exposes memoized store-backed accessors.
5. **Thin policies/interfaces** — a pure `ProteomeSelector`, CLI commands, and serializers call the layers above. No constructor performs network or filesystem I/O.

### SQLite model

`datasets`

- identity: dataset key, resource kind, requested release, observed release/date
- provenance: source URL/query, format, fields, include-isoforms flag, fetched timestamp
- status: complete/incomplete and optional next-page URL for resumable installs

`entries`

- primary key: `(dataset_id, accession)`; isoform accessions remain distinct rows
- indexed projections: canonical accession, UniProtKB ID, reviewed, primary protein name, primary gene name, taxon ID, protein-existence level, entry version, sequence version
- sequence and checksums
- JSON columns: the complete raw entry plus nested protein description, genes, organism, entry audit, features, comments, xrefs, keywords, references, and extra attributes as useful direct projections

`entry_gene_names`

- `(dataset_id, accession, name, kind, ordinal)` for exact case-insensitive gene lookup without discarding gene name/synonym/locus/ORF ordering

`proteomes` and `dataset_proteomes`

- rich raw proteome JSON plus indexed UPID, taxon, type, counts, BUSCO/CPD, assembly, and explicit dataset membership

The full raw JSON is authoritative. Projection columns accelerate stable query/accessor needs and can be rebuilt. This avoids both extremes: a lossy flat FASTA/GTF analogue and an unqueryable opaque blob.

### Public API sketch

```python
client = UniProtClient()
entry = client.get_entry("P04637")
fasta = client.get_entry_text("P04637", format="fasta")
pages = client.search_entries("gene:TP53 AND organism_id:9606")

release = UniProtRelease("2026_02", cache_dir="...")
release.install_entries(["P04637", "P38398"])
release.install_query("proteome:UP000005640")
entry = release.entry("P04637")
release.entries_by_gene("TP53")
release.entries_by_name("Cellular tumor antigen p53")

candidates = client.reference_proteomes(9606, scope="exact")
selection = ProteomeSelector().select(candidates)
release.install_proteome("UP000005640")

client.get_taxon(9606)
job = client.submit_id_mapping("Gene_Name", "UniProtKB", ["TP53"])
result = client.wait_for_mapping(job)
```

CLI shape:

```text
uniprotpy entry get P04637 --format json --output p53.json
uniprotpy install entries P04637 P38398 --release 2026_02
uniprotpy install proteome UP000005640 --release 2026_02
uniprotpy proteome select 9606 --scope exact
uniprotpy query --gene TP53 --format tsv
```

JSON output emits the faithful source object. FASTA uses the stored accession/description/sequence. TSV is an explicit stable projection with caller-selected fields; nested values are JSON-encoded rather than silently flattened.

## Why pyensembl's data model is not copied

Useful ergonomics: a stable release/handle value, deterministic cache directories with an environment override, explicit install/load, lazy database creation, memoized accessors, serialization, and a thin CLI.

Rejected concepts: GTF ingestion; gene/transcript/exon interval tables; contig/start/end/strand indexes; Ensembl species-server routing; integer release bounds; genome/reference-name lifecycle; and interval-coordinate accessors. UniProt's core is an accession-keyed, evidence-rich protein knowledge document plus distinct proteome, taxonomy, and mapping resources. Forcing it into genomic interval tables would discard the information this library exists to expose.

## Existing repository migration

- Replace duplicated direct `requests` calls with the central client.
- Expand the current flat `UniprotEntry` without preserving the lossy schema as a second public model. Migrate all callers; no compatibility shim.
- Repair FASTA parsing to accept paths, handles, or text explicitly; keep it for external FASTA input/output, not as the authoritative rich-entry parser.
- Replace `ProteomeSelector` constructor I/O and its mixed return shapes with pure candidate selection.
- Remove hard-coded `./data` and `/home/dan/Desktop` outputs. Implement the documented stores or reject unsupported CLI combinations rather than silently doing nothing.
- Keep exploratory protein/taxonomy tree utilities outside the public API until they use the client and have bounded behavior.

## Verification plan

High-signal fixtures and tests will cover:

- a captured real reviewed entry JSON with multiple genes/names, nested evidence, heterogeneous features/comments/xrefs, and sequence/audit versions;
- a direct isoform projection and canonical-accession behavior;
- faithful JSON -> domain -> SQLite -> domain round-trip, including unknown nested fields;
- indexed accession, gene-name/synonym, and protein-name lookups;
- retries/error translation and verbatim Link pagination with mocked HTTP;
- proteome unique/ambiguous/not-found selection and exact versus lineage scope;
- CLI output bytes/text and destinations.

Live API checks are smoke tests only; deterministic tests use captured fixtures and never require the current release number.

## Milestones

- Design checkpoint: complete. Research and UniProt-native architecture recorded here.
- Entry pipeline: complete in `b68d9c5` and `bc50a60`. Includes JSON/FASTA retrieval, lossless accessors, polite retries/pagination, release-aware SQLite, indexed accession/gene/name queries, and an environment-overridable release cache.
- Proteome pipeline: complete in `c4df80f`. Includes faithful proteome metadata, exact/lineage reference discovery, typed ambiguity-preserving selection, and cursor-paginated bulk install with membership/provenance.
- Verification: 21 focused offline tests pass. A live `P04637` JSON/FASTA fetch round-tripped through SQLite with a successful `TP53` lookup; live `UP000005640` metadata returned taxon 9606 and BUSCO 99.0 on release `2026_02`.
- Redesigned CLI: complete. Includes entry JSON/FASTA/selected-TSV fetch, entry/proteome installation, cached accession/gene/name/proteome/all queries, faithful raw JSON, deterministic cached FASTA, explicit selected TSV fields, and UTF-8 file/stdout destinations. Focused offline verification passes 13 tests.
- Taxonomy, ID Mapping, and repository cutover: complete in `2d986a0`. Includes faithful taxonomy domains and merged-ID handling, taxonomy pagination, capability-discovered mapping validation, one-shot form submission, redirect-safe polling, authoritative-details result pagination, raw diagnostics/metadata retention, explicit external FASTA artifacts, stateless proteome selection, and removal of legacy direct-request/flat-schema/path shims. Focused offline coverage passes across taxonomy, mapping, parser, store, proteome, transport, cache, and CLI behavior.

## Next step

No further implementation milestone is committed. Future taxonomy or ID Mapping CLI/storage work should be scoped explicitly and continue to preserve raw source documents, response metadata, opaque cursors, mapping diagnostics, and the clean client/domain/release boundaries shipped above.
