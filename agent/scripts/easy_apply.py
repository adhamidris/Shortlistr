"""Small LinkedIn UI adapter. Leaves drafts open; never guesses answers."""
from runtime import browser_endpoint
import asyncio
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlparse

from linkedin import ROOT, atomic_write, check_access, goto, utc_now

DOM = Path(__file__).with_name('easy_apply_dom.js').read_text(encoding='utf-8')
ENDPOINT = browser_endpoint()
CONFIRM_TIMEOUT = 15000
CONFIRMATION = re.compile(r'^(?:Your application (?:was |has been )?(?:sent|submitted)|Application (?:sent|submitted))', re.I)


def shape(state):
    return [(f['id'], f['kind'], f['label'],
             [(o.get('key'), o.get('label')) for o in f.get('options', [])])
            for f in state['fields']]


def version(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()[:24]


async def target_id(page):
    session = await page.context.new_cdp_session(page)
    try:
        return (await session.send('Target.getTargetInfo'))['targetInfo']['targetId']
    finally:
        await session.detach()


def matches_job(url, job_id):
    p = urlparse(url)
    return p.hostname == 'www.linkedin.com' and p.path.rstrip('/') == f'/jobs/view/{job_id}'


class EasyApply:
    def __init__(self, state_dir=None):
        self.state_dir = Path(state_dir or ROOT / 'data/easy-apply')

    def save(self, job_id, state):
        atomic_write(self.state_dir / f'{job_id}.json', json.dumps(state))

    async def snapshot(self, page, job_id):
        await check_access(page)
        if not matches_job(page.url, job_id):
            raise ValueError('Tab navigated away from the requested job; inspect it with Playwright.')
        dialogs = page.get_by_role('dialog').filter(visible=True)
        if await dialogs.count() != 1:
            raise ValueError('Expected one application dialog; inspect the same tab with Playwright.')
        dialog = dialogs.first
        state = await dialog.evaluate(DOM)
        if not (state['heading'] or '').startswith('Apply to '):
            raise ValueError('This is not an Easy Apply form; inspect the same tab with Playwright.')
        state.update(job_id=job_id, tab_id=await target_id(page), url=page.url)
        return dialog, state

    def result(self, state, status=None, **extra):
        submit = [b for b in state['buttons'] if b['name'] == 'Submit application' and not b['disabled']]
        result = {k:state[k] for k in ('job_id','tab_id','url','heading','fields','errors')}
        result.update(status=status or ('validation_error' if state['errors'] else 'ready_to_submit' if submit else 'needs_answers'),
                      form_version=version(state), **extra)
        # Preserve review prose and read-only experience; no unrelated page text.
        result['context'] = state['text']
        result['actions'] = [b['name'] for b in state['buttons'] if not b['disabled']]
        if state['unsupported']:
            result['status'] = 'browser_required'
        return result

    async def fill(self, dialog, state, answers):
        fields = {f['id']:f for f in state['fields']}
        unknown = set(answers) - fields.keys() - {'upload_resume'}
        if unknown:
            raise ValueError(f'Unknown field IDs: {sorted(unknown)}. Inspect the form again.')
        for key, value in answers.items():
            fresh = await dialog.evaluate(DOM)
            if shape(fresh) != shape(state):
                raise ValueError('Conditional fields changed during filling; inspect before answering further.')
            if key == 'upload_resume':
                path = Path(value).expanduser().resolve()
                if not path.is_relative_to(ROOT) or not path.is_file():
                    raise ValueError('Upload must be an existing file within this repo.')
                if path.suffix.lower() not in {'.pdf','.doc','.docx'} or path.stat().st_size >= 2_000_000:
                    raise ValueError('Resume must be PDF/DOC/DOCX under 2 MB.')
                async with dialog.page.expect_file_chooser() as chooser:
                    await dialog.get_by_role('button', name='Upload resume', exact=True).click()
                await (await chooser.value).set_files(str(path))
                await dialog.page.get_by_text('Resume uploaded successfully', exact=True).wait_for(timeout=15000)
                continue
            field = fields[key]
            control = dialog.locator(f'[data-job-field="{key}"]')
            kind = field['kind']
            if field.get('disabled'):
                raise ValueError(f'{key} is disabled.')
            if kind in ('radio','checkboxes'):
                values = value if isinstance(value,list) else [value]
                selected = []
                for val in values:
                    options = [o for o in field['options'] if o['key'] == val or o['label'] == val]
                    if len(options) != 1:
                        raise ValueError(f'{key}: ambiguous/unknown choice; use returned option key.')
                    selected.append(options[0]['key'])
                if kind == 'radio' and len(selected) != 1:
                    raise ValueError('Choose exactly one radio option.')
                for option in field['options']:
                    loc = control.locator(f'[data-job-option="{option["key"]}"]')
                    checked = option['key'] in selected
                    if kind == 'radio' and not checked:
                        continue
                    if await loc.is_checked() != checked:
                        row = control.locator(f'[data-job-choice="{option["key"]}"]')
                        if await row.count() == 1:
                            await row.click()
                        else:
                            await loc.set_checked(checked)
                    updated = await dialog.evaluate(DOM)
                    if shape(updated) != shape(state):
                        raise ValueError('Conditional fields changed; inspect before continuing.')
                    if await loc.is_checked() != checked:
                        raise ValueError(f'{key}: selection did not persist.')
            elif kind == 'checkbox':
                if not isinstance(value,bool):
                    raise ValueError('Checkbox answers must be boolean.')
                await control.set_checked(value)
                await dialog.evaluate(DOM)
                if await control.is_checked() != value:
                    raise ValueError(f'{key}: checkbox change did not persist.')
            elif kind == 'select':
                await control.select_option(label=str(value))
                updated = await dialog.evaluate(DOM)
                if shape(updated) != shape(state):
                    raise ValueError('Conditional fields changed; inspect before continuing.')
                if await control.locator('option:checked').inner_text() != str(value):
                    raise ValueError(f'{key}: selection did not persist.')
            elif kind in ('text','email','tel','number','url','search'):
                await control.fill(str(value))
                await control.blur()
                updated = await dialog.evaluate(DOM)
                if shape(updated) != shape(state):
                    raise ValueError('Conditional fields changed; inspect before continuing.')
                if await control.input_value() != str(value):
                    raise ValueError(f'{key}: value did not persist.')
            else:
                raise ValueError(f'{key}: {kind} needs Playwright in this same tab; then inspect again.')

    async def execute(self, browser, job_id, action='inspect', answers=None, form_version=None, run_dir=None):
        if not re.fullmatch(r'\d+', job_id):
            raise ValueError('job_id must contain digits only.')
        if action not in ('inspect','continue','submit'):
            raise ValueError('Unknown action.')
        answers = answers or {}
        if action == 'inspect' and answers:
            raise ValueError('Use continue to fill answers.')
        path = self.state_dir / f'{job_id}.json'
        saved = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        if saved.get('submission'):
            return dict(saved, job_id=job_id, status=saved['submission'],
                        reason='Submission was already attempted. Inspect evidence/browser; never retry automatically.')
        pages = [p for c in browser.contexts for p in c.pages if matches_job(p.url,job_id)]
        if saved.get('tab_id'):
            pages = [p for p in pages if await target_id(p) == saved['tab_id']]
            if not pages:
                return {'status':'browser_required','job_id':job_id,'tab_id':saved['tab_id'],
                        'reason':'The saved tab is closed or navigated away. Reconcile the draft before removing its state file.'}
        if len(pages) > 1:
            return {'status':'browser_required','reason':'Multiple tabs for this job. Keep one draft tab before proceeding.'}
        if not pages:
            if action != 'inspect':
                raise ValueError('Inspect the job before changing a form.')
            page = await browser.contexts[0].new_page()
            saved['owned'] = True
            await goto(page, f'https://www.linkedin.com/jobs/view/{job_id}/')
            await asyncio.sleep(3)
        else:
            page = pages[0]
        page.set_default_timeout(8000)
        saved['tab_id'] = await target_id(page)
        self.save(job_id,saved)
        try:
            await check_access(page)
            if not await page.get_by_role('dialog').filter(visible=True).count():
                if action != 'inspect':
                    raise ValueError('Draft is no longer open. Inspect before continuing.')
                button = page.get_by_role('button',name='Easy Apply to this job',exact=True)
                if await button.count() != 1:
                    return {'status':'browser_required','job_id':job_id,'tab_id':saved['tab_id'],
                            'url':page.url,'reason':'No unique Easy Apply button; inspect applied/closed/external-apply state.'}
                await button.click()
                await page.get_by_role('dialog').get_by_role('button',name=re.compile(r'^(Next|Review|Submit application)$')).first.wait_for(timeout=15000)
            dialog, state = await self.snapshot(page,job_id)
            if action == 'inspect':
                return self.result(state)
            if not form_version or form_version != version(state):
                return self.result(state,'stale_form',reason='Form changed. Review this state and use its new form_version.')
            if action == 'submit':
                if answers:
                    raise ValueError('Fill answers with continue, inspect the resulting form, then submit separately.')
                return await self.submit(page,dialog,state,saved,run_dir)
            await self.fill(dialog,state,answers)
            dialog, current = await self.snapshot(page,job_id)
            # Conditional questions must be read before another action.
            if 'upload_resume' in answers or shape(state) != shape(current) or current['unsupported']:
                return self.result(current, reason='Form structure changed; review new controls.')
            if any(b['name'] == 'Submit application' for b in current['buttons']):
                return self.result(current)  # Continue never submits, including single-page forms.
            next_button = dialog.get_by_role('button',name=re.compile(r'^(Next|Review)$'))
            if await next_button.count() != 1:
                return self.result(current,'browser_required',reason='No unique Next/Review button.')
            await next_button.click()
            await asyncio.sleep(1)
            _, after = await self.snapshot(page,job_id)
            return self.result(after)
        except Exception as exc:
            return {'status':'browser_required','job_id':job_id,'tab_id':saved['tab_id'],
                    'url':page.url,'reason':str(exc).split('\n')[0],
                    'recovery':'Inspect this same tab with Playwright, fix only the blocker, then call inspect.'}

    async def submit(self, page, dialog, state, saved, run_dir):
        job_id = state['job_id']
        if state['errors'] or state['unsupported']:
            return self.result(state,'browser_required',reason='Resolve form errors/unsupported controls before submission.')
        confirmation = page.get_by_text(CONFIRMATION)
        if await confirmation.count() and await confirmation.first.is_visible():
            return self.result(state,'browser_required',reason='An existing confirmation is visible. Reconcile it before another submission.')
        missing = [f['label'] for f in state['fields'] if f.get('required') and
                   (not any(o['checked'] for o in f['options']) if f['kind'] in ('radio','checkboxes')
                    else not f.get('value'))]
        if missing:
            return self.result(state,'validation_error',reason='Required fields are empty: ' + ', '.join(missing))
        button = dialog.get_by_role('button',name='Submit application',exact=True)
        if await button.count() != 1 or not await button.is_enabled():
            return self.result(state,'browser_required',reason='No enabled final submit button.')
        if not run_dir:
            raise ValueError('run_dir is required for submission evidence.')
        folder = Path(run_dir).resolve()
        if not folder.is_relative_to((ROOT / 'runs').resolve()):
            raise ValueError('run_dir must be within this repo runs/.')
        folder = folder / 'applications' / job_id
        folder.mkdir(parents=True,exist_ok=True)
        before = folder / 'review.json'
        atomic_write(before, json.dumps(state,ensure_ascii=False))
        _, fresh = await self.snapshot(page,job_id)
        if version(fresh) != version(state):
            return self.result(fresh,'stale_form')
        saved.update(submission='submission_uncertain',before_submit=str(before))
        self.save(job_id,saved)  # Durable before click; cancellation/crash cannot silently re-submit.
        try:
            await button.click(timeout=8000)
            await confirmation.first.wait_for(timeout=CONFIRM_TIMEOUT)
            proof = folder / 'confirmation.json'
            atomic_write(proof, json.dumps({
                'job_id': job_id, 'url': page.url, 'confirmed_at': utc_now(),
                'confirmation_text': await confirmation.first.inner_text(),
            }, ensure_ascii=False))
            saved.update(submission='applied',evidence=str(proof))
            self.save(job_id,saved)
            if saved.get('owned') and matches_job(page.url, job_id):
                await page.close()
        except Exception:
            pass
        return dict(saved,job_id=job_id,status=saved['submission'])
