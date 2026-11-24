# Discovery and Greenhouse tools

Remotive, We Work Remotely and Greenhouse have separate MCP servers, sharing
the underlying collection, cache and runtime code. It uses public APIs/RSS for collection and
the existing shared Chrome for Greenhouse applications. Strategy and answers
remain the agent's decisions.

## Collect jobs

- `remotive.search_jobs(output, keyword="", since=null)`
- `weworkremotely.search_jobs(output, keyword="", since=null, feeds=null)`
- `greenhouse.search_jobs(board_url, output, keyword="", since=null)`

- `output`: fresh JSON path under the current run. Returns a compact summary
  and file path; read descriptions from that file, not a duplicate tool payload.
- `keyword`: title/description phrase; empty means all available listings.
- `filter_counts`: feed entries examined, outside the date window, and rejected by
  the keyword filter. Cached jobs can legitimately yield zero matches; this is
  not itself a parser failure. Overlapping feeds may examine the same job twice.
- `since`: ISO date/time; omitted means past seven days. Use an overlapping
  window for feeds, not the last seen timestamp: listings can arrive late.
- `board_url`: required for Greenhouse, e.g. `https://job-boards.greenhouse.io/COMPANY`.
  Choose employers dynamically when relevant; there is no universal company search
  and no fixed employer list. A custom careers URL must first be mapped to its
  actual public Greenhouse board. Do not guess board tokens from company names.
- `feeds`: WWR only; defaults to `all`, `programming`, `other`, `sales-marketing`.
  These snapshots overlap and differ in date coverage. The tool merges IDs.

Resolve `search_queries_from: linkedin` in targets.yaml to its query choices;
the tools do not read YAML themselves. Collect enabled sources before assessing
the merged primary batch. Run queries sequentially and register each output with `run_report.py collect`,
including partial/empty results. Assess the merged report after collection.
Cached matches are returned again intentionally: collection is not a decision.
Registration merges query overlaps and verified cross-source job identities;
prior outcomes come from the ledger. Read full descriptions for unresolved
registered jobs; do not repeatedly assess jobs already resolved. Use registered report IDs when recording
outcomes, because the first registered ID survives a cross-source merge.
Different titles at the same company are not automatically duplicates.

Remotive downloads are reused for six hours (public listings are delayed 24h).
WWR and Greenhouse responses are reused for 15 minutes. Every requested query
still runs against that snapshot. Respect reported fetch times and coverage;
never describe a feed as an exhaustive historical search. Unknown posting dates
remain available for assessment. Retain Remotive/WWR source attribution and links.

The file includes full descriptions and application instructions. A source URL
is not necessarily a verified application destination. Prefer a verified employer
posting when an aggregator is blocked; do not equate a similar title with proof.
Interpret geographic restrictions from the description, not a broad remote tag.
Talent-network registration, take-home assessments and email instructions are
not confirmed job submissions. Email tools remain read-only; surface any genuine
missing capability through the existing needs-input flow.

## Apply on Greenhouse

`greenhouse.apply_job(url, action="inspect", answers=null, form_version=null)`

1. `inspect` opens/resumes the hosted Greenhouse job tab and returns fields,
   choices, required flags, existing values, form context, tab ID and version.
2. `fill` takes that version and one batch of answers keyed by field ID.
   Text and choice labels are strings; multi-selects use lists; checkboxes use
   booleans; file fields take an existing repo-local CV path. Reinspect after
   conditional changes. Review prefilled content and special employer instructions.
3. `submit` takes the fresh version, with no answers. It submits once and saves
   a durable receipt. Record applied only for `submitted` with confirmation evidence.

`browser_required`: continue using Playwright on the returned tab, then inspect.
`form_changed`: read the new fields/version before continuing.
`submission_uncertain`: inspect the tab/email for the outcome; never resubmit
blindly. The tool retains its pending receipt and will not click again. If a
human/browser recovery confirms submission, record that evidence in the ledger.
Respect any requirement for the applicant's own words; do not silently accept
an attestation inconsistent with how answers were produced.

Other hiring platforms continue through existing Playwright instructions.

## Runtime and recovery

All three MCP clients allow 7,200 seconds; the operation stops at 7,100 seconds.
Network requests and browser actions have shorter operation-specific waits.
Calls execute in-process and share the browser-tool lock; cancellation does not
leave a detached scraper. Partial collection is saved before cancellation returns.
HTTP 429 persists server backoff; 5xx gets one delayed retry. No submission retries.
Do not use other browser tools concurrently or remove a lock to force a retry.

Application state lives in `data/greenhouse-apply/`; feed responses in
`data/discovery/`. Successful tool-owned application tabs close; unfinished or
uncertain drafts remain for recovery. User-owned tabs and Chrome itself stay open.
Reconnect the MCP in Kimi/Codex after configuration changes, between runs.
