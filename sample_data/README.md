# NEXA sample data

> **All documents here are synthetic.** Harbourview Property Group, Kestrel Pay and Lumen Labs are fictional companies. Every name, address, phone number (`555` numbers) and email address (`.example` domains) is made up. Say this clearly whenever you show them in a demo.

These documents power the three demo tenants (Property, Fintech, Company) and the evaluation dataset in `backend/eval/datasets/`. They cover every format the ingestion pipeline supports (PDF, DOCX, MD, TXT). Each one is built to test a specific part of the pipeline.

## Documents

| Domain | File | Format | Pages | Designed to test |
|---|---|---|---|---|
| property | `lease_unit_4b.pdf` | PDF | 5 | Page-accurate citations. The 60-day notice period (the plan's example question) is on page 3, Clause 7.2. |
| property | `lease_unit_7a.pdf` | PDF | 5 | Same layout as 4B with different terms (30 days notice, larger break fee, no pets). Used for comparison and multi-document questions, and for the Week 6 agent demo. |
| property | `building_c_rules.md` | Markdown | – | Markdown heading parsing. Pet policy ("What's the pet policy in Building C?"), quiet hours, parking, balconies. |
| property | `maintenance_policy.docx` | DOCX | – | Word `Heading 1/2` styles and **one table** (repair category → response time), so it also tests table extraction. |
| property | `inspection_reports_2026.pdf` | PDF | 50 | The long document for the Week 1 test "upload a 50-page PDF and check the page numbers". One inspection per page, each with its own identifiers (`INS-2026-xxxx`, `WO-xxxx`). Generated from a fixed random seed. |
| property | `tenant_welcome_guide.txt` | TXT | – | Plain text with no markup. Its section titles are numbered UPPERCASE lines such as `1. COLLECTING YOUR KEYS`. |
| fintech | `fee_schedule.pdf` | PDF | 3 | Fees, percentages and minimums/maximums that must be quoted exactly. |
| fintech | `kyc_aml_policy.docx` | DOCX | – | Nested headings (`Heading 3` sub-sections). This is the "Compliance" document for the Week 4 permissions demo. |
| fintech | `payment_error_codes.md` | Markdown | – | Exact identifiers such as `E-204`. Keyword and hybrid search should beat pure semantic search here. |
| fintech | `customer_faq.md` | Markdown | – | Question-style headings; paraphrased customer questions. |
| fintech | `internal_chargeback_procedure.txt` | TXT | – | An internal-only procedure, useful for the "Internal Procedures" collection in the permissions demo. |
| company | `employee_handbook.pdf` | PDF | 7 | HR policy lookups. The annual leave table answers "How many days of annual leave do I get after 3 years?" (21 days, page 2). |
| company | `it_security_policy.docx` | DOCX | – | Security rules, and cross-references with the onboarding SOP. |
| company | `api_documentation.md` | Markdown | – | Technical docs with code blocks and tables. It answers "Why am I getting a 401 on the /users endpoint?". |
| company | `release_notes.md` | Markdown | – | Dated changes that connect to the API docs (token lifetime changed in v2.4.0), for multi-document questions. |
| company | `onboarding_sop.txt` | TXT | – | Plain text SOP that refers to the handbook and the IT security policy. |

### What the PDFs look like (the parser depends on this)

- **Pagination is exact.** Each `<!-- pagebreak -->` in the source starts a new page. Pages are never reflowed, so an eval question can reliably say "page 3".
- **Headings are bigger than body text:** H1 18pt bold, H2 14pt bold, body 10.5pt, header/footer 8pt. The PDF parser finds headings by font size compared with the body text.
- **Every page has a running header and a footer.** They exist so the cleaner's repeated header/footer removal has real data to work on:
  - property PDFs: `Harbourview Property Group - Confidential`
  - fintech PDF: `Kestrel Pay - Customer Information`
  - company PDF: `Lumen Labs - Internal - Employee Handbook v4.2`
  - footer on every PDF: `Page N of M`
- The text is ASCII only, because the built-in PDF fonts can't draw characters like em dashes. The disclaimer inside PDFs therefore reads `Synthetic demo document - fictional company, not real data.`

## Folder layout

```
sample_data/
├── _source/<domain>/   hand-written sources: edit these
├── property/           generated outputs: do not edit by hand
├── fintech/
└── company/
```

## Regenerating

Edit a source in `_source/`, then run the generator. It needs Python 3.12, PyMuPDF and python-docx, so the easiest way is Docker (run from the repo root):

```bash
docker run --rm -v "$PWD":/work -w /work python:3.12-slim \
  sh -c "pip install -q pymupdf python-docx && python scripts/generate_sample_data.py"
```

The script prints each file's SHA-256 prefix, page count and the font sizes it found. The output is **deterministic**: running it twice gives byte-identical files. This matters because NEXA deduplicates uploads by file hash, so re-seeding the demo tenants won't create duplicates.

If you change a document, re-check the eval questions that cite it. The `expected_sources` in `backend/eval/datasets/*.jsonl` point at specific pages and sections.
