# Browser Tips

Browser mechanics for external forms and MCP recovery. Read the working method
first, then only the relevant site notes. [The skill](SKILL.md) owns application
order, answers, authentication, blocker handling and reporting; these notes do
not override those rules. Site observations are clues to verify against the
current page, not universal recipes.

## Working method

### Establish the current form

Confirm the active tab, job, form step and visible dialog before interacting.
Inspect the relevant accessibility tree or DOM to identify fields, required
inputs, uploads and controls that reveal or reset other fields. If snapshots
and DOM queries disagree, check the tab, frame and shadow-root context before
concluding that a control is missing. Use a screenshot when layout, visibility
or an ambiguous state needs visual inspection; it is not a routine prerequisite.

### Fill in useful batches

Review existing values rather than re-entering them. Handle dependencies such
as country/state selection or resume parsing first, wait for the resulting form
state, then batch straightforward fields. Stop the batch at a navigation,
re-render or uncertain custom control; inspect the changed section before
continuing. This avoids both one-call-per-field work and long speculative scripts.

Use labels, roles and observed attributes, scoped to the intended field or
visible dialog. Resolve duplicate matches by their meaning and visibility,
not arbitrary `.first()`/`.nth()` selection. For a custom dropdown, inspect its
associated open popup, using `aria-controls` when available; unrelated hidden
menus are not its options. Refresh snapshot references after navigation or
structural changes, and re-query controls whose names change after selection.

### Verify the result

Check the intended state after a batch or uncertain action: saved field values,
selected options/chips, accepted attachment, validation feedback or the next
step. A successful tool call, synthetic click, changed DOM property or HTTP 2xx
alone does not prove the form accepted the action. Recheck affected fields after
re-renders, especially passwords, dependent selects and uploaded attachments.
Before submission, follow the skill's review and confirmation requirements.

### Recover from evidence

Read the failure before choosing another approach. Distinguish a missing or
ambiguous target from a hidden, disabled, covered, unstable or off-screen one.
Inspect the relevant control and, when needed, its visible label, overlay or
scroll container. Scroll the actual container and confirm the target is in view;
repeated force clicks or viewport resizing do not explain an off-screen target.

Prefer normal Playwright interactions with the visible control or label. Use
keyboard input, coordinates, `force` or DOM mutation only when the observed
failure justifies that method, then verify the form accepted the change. Do not
cycle through selectors or event types without new evidence. Choose the next
step from what changed, rather than applying a fixed fallback ladder.

Wait for meaningful state changes (options loaded, control enabled, upload
accepted, next step visible), retaining any required platform pacing. A fixed
sleep alone is not evidence of readiness. After a timeout, inspect the current
state before repeating an action: a partial batch or submission may already
have taken effect. Do not overlap a replacement operation with one still running.
Reloading or replacing a tab can lose a draft; establish the saved/submission
state before doing so. For persistent site failures, authentication, challenges
or uncertain submissions, follow the skill's recovery and `needs_input` rules
and continue other work as directed there.

### Keep observations focused

Return the fields, options, validation messages and state changes needed for
the next decision. Avoid repeated full-page text/HTML dumps, exhaustive console
logs or deep snapshots when a section inspection is enough. Expand inspection
when the local evidence is insufficient. Use console/network information to
investigate a specific failure, not as routine output; a console error alone
is not proof of a bot block.

Use the browser tool's documented execution environment. Page code runs in the
browser; do not assume `require`, dynamic imports or filesystem access exist
inside browser snippets. Use the supported upload tools for local files.

## Uploads and custom inputs

- **Uploads:** identify the correct file input and use `setInputFiles` when
  supported. If using the browser's chooser route, open the chooser and complete
  it with `browser_file_upload`; that tool requires an active chooser. Do not
  compete with the MCP's chooser handling using a second handler. Verify the
  site's attachment state: `input.files` can show selection before acceptance,
  or be cleared after upload. A filename/status and validation provide context.
  If the chooser remains pending, resolve its modal state before more page actions.
- **Text and numeric fields:** start with normal fill operations; use keyboard
  input if the widget requires it. Respect the field's format and validation;
  change formatting without changing the truthful answer. A DOM setter alone
  may not update framework state.
- **Checkboxes/radios:** target the question's visible control or associated
  label. Hidden input order can include menu toggles or unrelated controls.
  Verify selection and form validation; native validity checks do not cover all
  custom or server-side rules. Leave hidden decoy fields untouched.
- **Autocomplete/multiselect:** typed text is not necessarily a selected value.
  Wait for the associated suggestions, select the intended option and verify the
  selected value or chips. Do not substitute a text/value assignment for selection.
- **Repeated sections:** scope Add/Delete controls to the intended education,
  employment or other section. Recheck the section after changes; fixed ancestor
  counts and positional selectors can target a neighbouring entry.
- **Dates/OTP boxes:** inspect the widget's format and focus behaviour. Segmented
  controls may advance focus as characters are typed; verify all segments rather
  than mixing blind clears and clicks. Follow the skill's email-verification rules.

## Shared browser and tabs

Use the shared browser as specified in the skill; never launch another instance
with its profile. Resume an MCP fallback in the returned tab and refresh the
MCP's form state afterwards. Let in-flight tools finish before browser actions.

Reuse the task's existing tab. After recording an outcome, close finished tabs
unless they hold an unfinished draft, uncertain submission or necessary login
recovery. At task boundaries, close redundant search/login tabs using current
URLs and state, not stale indices or broad URL-only deletion. Never close a tab
owned by an in-flight call. If LinkedIn authentication fails, retain one recovery
tab; further keyword searches cannot restore authentication.

## Site observations

### LinkedIn

Use its MCP for supported search and application actions. Search filters,
pacing, deduplication and saved result handling belong to the tools and skill.
Browser recovery should inspect the returned tab rather than recreate the flow.

- LinkedIn detail URLs use `/jobs/view/<id>/`. During manual inspection, expand
  incomplete descriptions. Generated CSS classes are fragile; observed roles,
  labels and `componentkey` attributes have been more useful for recovery.
- Easy Apply validation may appear below the modal fold. Check the affected
  question and current values before advancing. For `stale_form`, follow the
  skill's review rule rather than blindly resubmitting with a new version.

### Workday

- Cookie overlays can intercept actions. Resume parsing can populate fields;
  review the parsed profile before filling the remainder.
- Some prompt widgets require Enter to search after typing. Inspect the active
  list and verify selection on the trigger or selected pills. Stale listboxes
  can remain in the DOM. Clearing a search with blind Backspace bursts has deleted
  selected pills; check focus and the input value before editing.
- A question's button name may change from “Select One” to its answer. Scope to
  the question instead of advancing through a shifting list of button indices.
- Date spinbuttons have required keyboard entry in some tenants. Inspect the
  format and verify every segment, including after Save and Continue validation.
- Workday chooser uploads have raced with custom `filechooser` handlers. Use the
  single upload route described above. Numeric-leading IDs need a properly
  escaped selector or an attribute selector such as `[id="9459"]`.

### Avature / Siemens Energy

- Country changes have re-rendered and cleared other fields while revealing State.
  Set dependencies first. Validation has also cleared password fields; inspect
  the resulting form before retrying.
- Select2 options may need a search before the desired institution/location
  appears. Inspect the active dropdown rather than guessing option text or IDs.
- Returning to a registration URL can lead to an existing profile. Check account
  state and resume through the job page; account recovery follows the skill's
  authorization rules, not an automatic password reset.

### Teamtailor / React / MUI

- Dialogs may contain their own scroll containers; unrelated hidden menus can
  precede application options in the DOM. Scope dropdowns to the current question.
- MUI multiselects display selected chips; typing into the textbox is not enough.
- Teamtailor range controls have had companion hidden values. Verify that the
  normal interaction updates the submitted form state before considering a
  widget-specific fallback.
- A stuck “Loading application form” or stale validation message needs current
  evidence. Inspect the relevant request/error/state; do not assume bot protection
  or repeatedly submit merely to test whether an error is stale.

### Ashby / resume-parsing portals / JazzHR

- Ashby Yes/No questions can be button groups with `aria-pressed`, alongside real
  radio fields. Autofill upload and required resume attachment may be separate.
- Some resume-parsing portals reveal contact fields only after parsing completes.
  Verify the parsed values and accepted attachment before proceeding.
- JazzHR uploads have sometimes selected a file without registering it in the
  widget. If the attachment is not accepted, inspect the chooser/widget state
  and use the supported alternative route; do not retry unsupported imports.

### Google Forms / Globant / Qureos

- Google Forms may use RTL layouts and custom option popups. Inspect actual
  visible controls rather than assuming a click offset or selection attribute
  is reliable. Reloading an unsaved form can discard answers.
- Globant resume parsing has produced incorrect language entries. Review parsed
  data; account credentials and signup rules remain owned by the skill/profile,
  never browser notes or run logs.
- `easyapply.jobs` links can redirect to an external ATS such as Qureos. Confirm
  the destination and job; use the skill's CV and email-verification rules.
