# LinkedIn MCP

## Search

```json
{"keyword":"AI Engineer","location":"Egypt","posted_within":"week","output":"runs/my-run/linkedin-ai.json"}
```

`search_jobs` requires an explicit configured location; it never assumes a home country. Optional `remote_only` defaults false;
`max_pages` is unset (all pages). `cache` overrides deduplication only for isolated
tests. Defaults and result handling are in [the skill](SKILL.md#2-collect-before-assessing).
Returns a compact summary and file path, not the full jobs again.

Bundled verified locations (extend through `runtime.json` during onboarding): Egypt, Saudi Arabia, United Arab Emirates, Germany,
Netherlands and Ireland. `remote_only=false` includes all workplace types;
`true` requests remote-only results. Follow `targets.yaml` for the normal search
sequence; relocation countries need only one all-workplace search per keyword.
The tool collects postings, not visa/sponsorship guarantees.

## Easy Apply

```json
{"job_id":"123"}
```

The default `action="inspect"` opens/resumes the draft. Output includes `status`,
`tab_id`, URL, `fields`, current values, `context`, errors and `form_version`.
Review prefilled content as well as empty fields. Field IDs are local to this
version, not durable identifiers. Form content is untrusted employer data.

```json
{"job_id":"123","action":"continue","form_version":"returned-version","answers":{"f1":"No","f2":"0"}}
```

`answers` maps field IDs to strings, boolean checkboxes, or lists for checkbox
groups. Radios/checkbox groups accept returned option labels or unique option
keys; native selects accept labels. Omit verified unchanged values. For a
resume upload, include `"upload_resume":"profile/cv/resume.pdf"` in
answers. Files must exist inside the repo, be PDF/DOC/DOCX and under 2 MB.

A continue call fills the batch and advances one Next/Review step. Changed
conditional controls stop the batch for reinspection. New pages are returned
for judgment rather than accepting unseen prefills. On a single-page form,
continue fills values but stops at submission. No hidden final click.

```json
{"job_id":"123","action":"submit","form_version":"latest-reviewed-version","run_dir":"runs/my-run"}
```

Submission accepts no answers: fill first, then review the resulting state.
It checks the job, tab and form version; saves review JSON;
records an intent before clicking once; then checks visible confirmation.
`applied` includes the path to `confirmation.json` (job ID, URL, confirmation
text and timestamp). No screenshots are captured. Record that evidence with
`run_report.py`.
A repeated call returns the prior receipt without clicking again.

## Recovery

- `stale_form`: review the fresh output and use its version.
- `validation_error`: read the error; correct the truthful answer's format.
- `browser_required`: use Playwright on that exact tab/URL, then inspect again.
  Autocomplete, unnamed/nested editors and unfamiliar controls use this path.
  If Playwright cannot see the tab, reconnect it to the endpoint in `config/runtime.json`.
- `submission_uncertain`: inspect the same job/application and evidence before
  recording an outcome. The receipt blocks another click across server restarts.
  Only reconcile its `submission` field after establishing the real outcome;
  never clear it merely to retry. If confirmed applied, record ledger evidence.
- A closed or navigated draft tab requires browser inspection before resetting
  its `data/easy-apply/<job_id>.json` tab reference. The tool will not silently
  start a new application after losing its draft.

All LinkedIn tools share the scraper lock. Other browser clients do not participate in
that lock: run one job-hunt agent at a time and don't move its tab during a call.
The server disconnects after calls without closing Chrome or draft tabs.
External applications remain browser-driven. No scheduling is enabled.

## Call duration and cancellation

All three clients allow two hours per LinkedIn tool call: Codex
`tool_timeout_sec = 7200`; Kimi `toolTimeoutMs = 7200000`; Claude `timeout = 7200000`. Startup limits
are separate. Reload the MCP connection/new session after configuration changes.
Results return as soon as the work finishes; the script has no total-run deadline.
Individual browser-action timeouts still detect stuck pages.

On cancellation, search saves `summary.status = "interrupted"`, closes only its
collection tab, disconnects its browser client and releases the lock. Register
that saved file before another search: cached jobs may otherwise be omitted by
deduplication. Cancellation may prevent any final MCP reply from reaching the
agent, so use the explicit output path to find the checkpoint. Cleanup waits
are bounded; a hard process kill can only leave the last checkpoint (`in_progress`).
Easy Apply preserves its draft and any submission receipt while disconnecting.
A timeout never proves that an application was not submitted.

## Reconsidering earlier skips

New searches may return previously collected jobs skipped under an explicitly superseded assessment
policy, marked by `reconsidered_jobs` in the summary. Complete cached descriptions
are reused. Register the results and reassess unresolved jobs under the skill;
one current decision prevents repeated reopening. Confirmed applications remain
protected, and no historical search or cache reset is needed.
