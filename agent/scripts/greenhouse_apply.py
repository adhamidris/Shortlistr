"""Greenhouse hosted forms: API schema + live browser state, single-shot submission."""
import asyncio
import json
from pathlib import Path
import re

from discovery import DATA, Fetcher, api_root
from easy_apply import target_id, version
from job_identity import greenhouse_ref
from linkedin import ROOT, atomic_write

DOM = Path(__file__).with_name('greenhouse_form.js').read_text(encoding='utf-8')
STATE = ROOT / 'data/greenhouse-apply'
CONFIRM = re.compile(r'(?:Thank you for applying|Your application has been (?:received|submitted)|Application submitted)',re.I)


async def snapshot(page, schema):
    await page.locator('#first_name').wait_for(state='visible',timeout=15000)
    forms = page.locator('form').filter(has=page.locator('#first_name'))
    if await forms.count() != 1:
        raise ValueError('Expected one application form; inspect this tab with Playwright.')
    form = forms.first
    state = await form.evaluate(DOM)
    spec = {f['name']: (q,f) for q in schema.get('questions',[])+schema.get('location_questions',[])
            for f in q['fields']}
    for field in state['fields']:
        if field['id'] in spec:
            q,f = spec[field['id']]
            field['required'] = q.get('required',False)
            field['label'] = q['label']
            field['alternatives'] = [x['name'] for x in q['fields']]
            field['multiple'] = f['type'] == 'multi_value_multi_select'
            if f.get('values'):
                field['options'] = [v['label'] for v in f['values']]
    present = {f['id'] for f in state['fields']}
    unavailable = [q['label'] for q in schema.get('questions', [])
                   if q.get('required') and not any(f['name'] in present for f in q['fields'])]
    state['unavailable_required'] = unavailable
    state['unsupported'] = state['unsupported'] or bool(unavailable)
    return form,state


def shape(state):
    return [(f['id'],f['kind'],f['label'],f.get('options'),f.get('required')) for f in state['fields']]


def missing(state):
    values={f['id']:f['value'] for f in state['fields']}
    return [f['id'] for f in state['fields'] if f['required'] and
            not any(values.get(k) for k in f.get('alternatives',[f['id']]))]


async def apply(ctx, client, url, action, answers=None, revision=None, directory=STATE, cache=DATA):
    region,board,job = greenhouse_ref(url)
    if not job:
        raise ValueError('Application requires a Greenhouse job URL, not a board URL.')
    host = 'job-boards.eu.greenhouse.io' if region == 'eu' else 'job-boards.greenhouse.io'
    url = f'https://{host}/{board}/jobs/{job}'
    path=Path(directory)/f'{region}-{job}.json'
    record=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    def save(**changes):
        record.update(changes);atomic_write(path,json.dumps(record,ensure_ascii=False))
    if record.get('status')=='submitted':
        return record
    page=None
    for tab in ctx.pages:
        if record.get('tab_id') and await target_id(tab)==record['tab_id']:
            page=tab;break
    if action!='inspect' and (not page or not revision):
        return {'status':'inspect_required', 'url':url}
    if not page and record.get('status')=='submission_pending':
        return {**record,'status':'submission_uncertain','reason':'Saved tab unavailable. Verify outcome manually; never resubmit blindly.'}
    if not page:
        # Adopt an existing exact job tab; never close user-owned tabs.
        for tab in ctx.pages:
            if tab.url.split('?')[0].rstrip('/')==url.split('?')[0].rstrip('/'):
                page=tab;break
        owned=page is None
        if owned: page=await ctx.new_page()
        save(tab_id=await target_id(page),owned=owned,url=url,job_id=f'{region}:{job}')
        if owned: await page.goto(url,wait_until='domcontentloaded',timeout=45000)
    info={'tab_id':record['tab_id'],'url':page.url,'job_id':f'{region}:{job}'}
    async def confirmed():
        # Confirmation must be scoped to the application's own Greenhouse origin.
        if not page.url.startswith(url.split('/jobs/')[0]+'/'):
            return False
        if await page.locator('#first_name').is_visible():
            return False
        notices=page.get_by_role('heading',name=CONFIRM)
        if await notices.count() and await notices.first.is_visible():
            evidence={'url':page.url,'text':await notices.first.inner_text()}
            save(status='submitted',evidence=evidence)
            if record.get('owned'): await page.close()
            return True
        return False
    if record.get('status')=='submission_pending':
        if await confirmed(): return record
        return {**info,'status':'submission_uncertain','reason':'Submission attempted. Inspect confirmation or email; no automatic retry.'}
    try:
        if greenhouse_ref(page.url) != (region,board,job):
            raise ValueError('Saved tab navigated away from this job.')
        schema=json.loads(await Fetcher(cache,client).get(api_root(region)+board+f'/jobs/{job}?questions=true',900))
        form,state=await snapshot(page,schema)
    except Exception as exc:
        return {**info,'status':'browser_required','reason':str(exc).split('\n')[0]}
    def result(s):
        return {**info,**s,'form_version':version(s),'missing':missing(s),
                'status':'browser_required' if s['unsupported'] else 'validation_error' if s['errors'] else
                         'needs_answers' if missing(s) or s.get('uploading') else 'ready_to_submit'}
    current=result(state)
    if action=='inspect' or state['unsupported']:return current
    if revision!=current['form_version']:return {**current,'status':'form_changed'}
    answers=answers or {}
    if action=='submit' and answers:raise ValueError('Fill and inspect first; submit accepts no answers.')
    fields={f['id']:f for f in state['fields']}
    if set(answers)-fields.keys():raise ValueError('Unknown field IDs; inspect first.')
    # Validate the entire batch before changing any control.
    for key,value in answers.items():
        f=fields[key]
        if f['disabled']:raise ValueError(f'{key} is disabled.')
        if f['kind']=='checkbox':
            if not isinstance(value,bool):raise ValueError(f'{key} requires boolean.')
        elif f['kind']=='file':
            if not isinstance(value,str):raise ValueError('Upload requires a repo-local file path.')
            file=Path(value).expanduser().resolve()
            if not file.is_relative_to(ROOT) or file.suffix.lower() not in {'.pdf','.doc','.docx','.txt'} or not file.is_file() or file.stat().st_size>10*1024*1024:
                raise ValueError('Upload must be an existing repo-local PDF/DOC/DOCX/TXT under 10 MB.')
        elif f['kind']=='choice' and f.get('multiple'):
            if not isinstance(value,list) or not all(isinstance(v,str) for v in value):raise ValueError('Multi-select requires choice labels.')
        elif not isinstance(value,str):raise ValueError(f'{key} requires text.')
        if f['options'] and any(v not in f['options'] for v in (value if isinstance(value,list) else [value])):
            raise ValueError(f'Use exact returned choice labels for {key}.')
    if action=='fill':
        try:
            for key,value in answers.items():
                _,fresh=await snapshot(page,schema)
                if shape(fresh)!=shape(state):return {**result(fresh),'status':'form_changed'}
                f=fields[key];control=form.locator('[id='+json.dumps(key)+']')
                if f['kind']=='file':
                    if not await control.count():
                        return {**result(fresh),'status':'browser_required','reason':'Replace the existing attachment in this tab, then inspect.'}
                    wrapped = await control.evaluate('(e)=>!!e.closest(".file-upload")')
                    await control.set_input_files(str(Path(value).expanduser().resolve()),timeout=15000)
                    if wrapped:
                        await page.wait_for_function('''name => [...document.querySelectorAll('.file-upload__filename')].some(e=>e.textContent.trim()===name && e.getClientRects().length) && ![...document.querySelectorAll('.file-upload__progressbar')].some(e=>e.getClientRects().length)''',arg=Path(value).name,timeout=30000)
                elif f['kind']=='checkbox':await control.set_checked(value,timeout=10000)
                elif f['kind']=='select':await control.select_option(label=value,timeout=10000)
                elif f['kind']=='choice':
                    if f.get('multiple') and f['value'] and f['value']!=value:
                        return {**result(fresh),'status':'browser_required','reason':'Changing existing multi-select values requires inspection in this tab.'}
                    for label in (value if isinstance(value,list) else [value]):
                        if label in (f['value'] if isinstance(f['value'],list) else []):continue
                        await control.click(timeout=10000)
                        await control.fill(label,timeout=10000)
                        await page.get_by_role('option',name=label,exact=True).click(timeout=10000)
                else:await control.fill(value,timeout=10000)
                if f['kind'] != 'file':
                    await control.blur()
            _,fresh=await snapshot(page,schema)
            # Upload widgets may replace the file input after processing; force a new
            # inspection rather than treating a queued upload as accepted.
            by_id={f['id']:f for f in fresh['fields']}
            for key,value in answers.items():
                actual=by_id.get(key,{}).get('value')
                expected=[Path(value).name] if fields[key]['kind']=='file' else value
                if fields[key]['kind']=='choice' and isinstance(expected,str):expected=[expected]
                if actual!=expected:
                    return {**result(fresh),'status':'browser_required','reason':f'Verify persisted value/upload for {key} in this tab.'}
            return result(fresh)
        except Exception as exc:
            _,fresh=await snapshot(page,schema)
            return {**result(fresh),'status':'browser_required','reason':str(exc).split('\n')[0]}
    if action!='submit':raise ValueError('Unknown action.')
    if current['status']!='ready_to_submit':return current
    button=form.get_by_role('button',name='Submit application',exact=True).filter(visible=True)
    if await button.count()!=1 or not await button.is_enabled():
        return {**current,'status':'browser_required','reason':'Submit is missing, ambiguous or disabled.'}
    save(status='submission_pending')  # Durable BEFORE click; cancellation cannot cause a duplicate.
    try:
        await button.click(timeout=15000)
        for _ in range(20):
            if await confirmed():return record
            await asyncio.sleep(1)
    except Exception:
        pass
    return {**info,'status':'submission_uncertain','reason':'Check this tab for confirmation, validation, or a challenge; never blindly resubmit.'}
