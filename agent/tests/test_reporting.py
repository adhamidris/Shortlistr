"""Reporting integration checks use temporary ledgers and mocked Telegram only."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import shutil
import subprocess
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import ledger
import run_report as rr


class ReportingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.run = self.root/'runs/test'
        self.lp = patch.object(ledger, 'LEDGER', self.root/'ledger.jsonl')
        self.lp.start()
        self.source = self.root/'source.json'
        self.source.write_text(json.dumps({'summary':{'status':'complete','already_collected':5}, 'jobs':[
            {'job_id':str(i),'platform':'linkedin','title':f'Role {i}','company':'A & B_[Company]',
             'url':f'https://linkedin.com/jobs/view/{i}/','location':'Cairo'} for i in range(1,5)]}), encoding='utf-8')
        rr.collect(self.run,[self.source])

    def test_legacy_skip_reopens_once_but_old_reports_and_applications_stay_protected(self):
        job=rr.manifest(self.run)['jobs']['linkedin:1']
        old=rr.event_for(job,self.root/'old','skipped',notes='Not AI-focused')
        old.pop('assessment_policy')
        ledger.append_events([old])
        self.assertEqual(rr.report(self.run)['jobs'][0]['reason'],'Reassess under current software-fit policy')
        self.assertEqual(rr.prior_outcome(job,[old],'legacy-run')['status'],'previously_resolved')
        rr.record(self.run,{'skipped':[{'job_id':'1','reason':'Unsupported core platform responsibilities'}]})
        events=ledger.read_events()
        self.assertEqual(rr.prior_outcome(job,events,'future',rr.ASSESSMENT_POLICY)['status'],'previously_resolved')
        applied=rr.event_for(job,self.root/'applied','applied',score=70)
        self.assertEqual(rr.prior_outcome(job,[old,applied],'future',rr.ASSESSMENT_POLICY)['reason'],'previous application')

    def test_cache_recheck_ids_exclude_new_decisions_and_prior_applications(self):
        from assessment_policy import recheck_ids
        jobs=rr.manifest(self.run)['jobs']
        events=[]
        for i in ['1','2','3','4']:
            e=rr.event_for(jobs['linkedin:'+i],self.root/'old','skipped')
            e.pop('assessment_policy');e.pop('event_id');events.append(e)
        events += [rr.event_for(jobs['linkedin:2'],self.root/'old','applied',score=80),
                   rr.event_for(jobs['linkedin:3'],self.root/'old','needs_input',notes='User action'),
                   rr.event_for(jobs['linkedin:4'],self.root/'old','skipped')]
        ledger.append_events(events)
        self.assertEqual(recheck_ids('linkedin'),{'1'})
        self.assertEqual(recheck_ids('other-platform'),set())

    def tearDown(self):
        self.lp.stop()
        self.tmp.cleanup()

    def test_cross_source_merge_and_prior_application(self):
        source = self.root/'cross.json'
        a = dict(platform='linkedin',job_id='99',title='AI Engineer',company='Example',
                 url='https://www.linkedin.com/jobs/view/99',
                 external_apply_url='https://job-boards.greenhouse.io/example/jobs/123')
        b = dict(platform='greenhouse',job_id='us:123',title='AI Engineer',company='Example',
                 url='https://example.com/careers?gh_jid=123')
        source.write_text(json.dumps({'jobs':[a,b]}), encoding='utf-8')
        rr.collect(self.run,[source])
        self.assertIn('linkedin:99',rr.manifest(self.run)['jobs'])
        self.assertNotIn('greenhouse:us:123',rr.manifest(self.run)['jobs'])
        ledger.append_events([rr.event_for(a,self.root/'runs/previous','applied',evidence='confirmation')])
        self.assertEqual(rr.prior_outcome(b,ledger.read_events(),rr.run_id(self.run))['reason'],'previous application')

    def test_finalize_records_completion_once_without_telegram(self):
        rr.record(self.run, {'skipped': ['1', '2', '3', '4']})
        with rr.run_lock(self.run):
            result = rr.finalize(self.run)
            rr.finalize(self.run)
        self.assertEqual(result['status'], 'complete')
        self.assertTrue(Path(result['summary_file']).exists())
        self.assertEqual(sum(e['event'] == 'run_complete' for e in ledger.read_events()), 1)
        self.assertFalse((self.run/'digest/delivery.json').exists())

    def test_finalize_does_not_complete_unfinished_or_blocked_runs(self):
        with rr.run_lock(self.run):
            self.assertEqual(rr.finalize(self.run)['status'], 'incomplete')
        rr.record(self.run, {'skipped': ['1', '2', '3', '4']})
        rr.blocker(self.run, 'account', 'Verify email', False)
        with rr.run_lock(self.run):
            self.assertEqual(rr.finalize(self.run)['status'], 'incomplete')
        self.assertFalse(any(e['event'] == 'run_complete' for e in ledger.read_events()))

    def test_decisions_join_metadata_and_unrecorded_jobs_remain_unresolved(self):
        result = rr.record(self.run, {'skipped':['1'], 'applied':[{'job_id':'2','score':91,'evidence':'runs/test/proof.png'}],
                                     'needs_input':[{'job_id':'3','reason':'Transcript required'}]})
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(result['counts']['unresolved'],1)
        event = next(e for e in ledger.read_events() if e['event']=='applied')
        self.assertEqual(event['company'],'A & B_[Company]')
        self.assertEqual(event['url'],'https://linkedin.com/jobs/view/2/')
        self.assertEqual(rr.report(self.run)['jobs'][1]['evidence'],'runs/test/proof.png')
        text = (self.run/'summary.md').read_text(encoding='utf-8')
        self.assertIn('Agent work remaining (1)', text)
        self.assertIn('Role 1', text)
        self.assertIn('A &amp; B', text)
        self.assertFalse((self.run/'digest').exists())

    def history_run(self, name, age, job_id, status='needs_input'):
        path = self.run.parent/name
        job = {'platform':'linkedin','job_id':str(job_id),'title':f'Historical {job_id}',
               'company':'Old company','url':f'https://linkedin.com/jobs/view/{job_id}/'}
        rr.atomic_json(path/'report-input.json', {'run':rr.run_id(path),
            'created_at':(datetime.now().astimezone()-timedelta(days=age)).isoformat(),
            'jobs':{rr.key(job):job},'sources':{}})
        ledger.append_events([rr.event_for(job,path,status,notes='User verification required')])
        return path, job

    def test_two_previous_runs_carry_only_open_user_items_without_changing_totals(self):
        oldest,_ = self.history_run('oldest',3,101)
        second,_ = self.history_run('second',2,102)
        newest,_ = self.history_run('newest',1,103)
        rep = rr.report(self.run)
        self.assertEqual(rep['previous_attention']['runs'],[rr.run_id(newest),rr.run_id(second)])
        self.assertEqual({j['job_id'] for j in rep['previous_attention']['jobs']},{'102','103'})
        self.assertEqual(rep['total'],4)
        self.assertEqual(rep['counts']['needs_input'],0)
        before = ledger.LEDGER.read_bytes()
        rr.render(self.run);rr.render(self.run)
        self.assertEqual(ledger.LEDGER.read_bytes(),before)
        self.assertEqual(rr.report(self.run)['previous_attention'],rep['previous_attention'])
        # A future run cannot change the current run's lookback window.
        future,_ = self.history_run('future',-1,104)
        self.assertEqual(rr.report(self.run)['previous_attention']['runs'],rep['previous_attention']['runs'])

    def test_resolved_or_skipped_older_items_do_not_carry(self):
        old,job = self.history_run('old',1,101)
        ledger.append_events([rr.event_for(job,old,'skipped',notes='User declined')])
        self.assertEqual(rr.report(self.run)['previous_attention']['jobs'],[])
        ledger.append_events([rr.event_for(job,old,'needs_input',notes='Old blocker'),rr.event_for(job,old,'applied',score=90)])
        self.assertEqual(rr.report(self.run)['previous_attention']['jobs'],[])

    def test_blockers_carry_from_previous_runs_and_resolve_at_source(self):
        old,_ = self.history_run('old',1,101,'skipped')
        rr.blocker(old,'account','Account verification required')
        self.assertEqual(rr.report(self.run)['previous_attention']['blockers'][0]['id'],'account')
        rr.blocker(old,'account',resolve=True)
        self.assertEqual(rr.report(self.run)['previous_attention']['blockers'],[])

    def test_old_rediscovered_need_input_is_not_repeated_outside_window(self):
        oldest,_ = self.history_run('oldest',3,101)
        self.history_run('middle',2,102,'skipped')
        self.history_run('newest',1,103,'skipped')
        job = rr.manifest(self.run)['jobs']['linkedin:1']
        ledger.append_events([rr.event_for(job,oldest,'needs_input',notes='Expired reminder')])
        rep=rr.report(self.run)
        self.assertEqual(rep['jobs'][0]['status'],'needs_input')
        self.assertFalse(rep['jobs'][0]['attention_visible'])
        self.assertNotIn('Expired reminder','\n'.join(rr.summary_lines(rep, self.run)))
        self.assertIn('Older blocked jobs','\n'.join(rr.summary_lines(rep,self.run)))

    def test_rich_summary_has_actual_search_filters_links_and_skip_reasons(self):
        payload=json.loads(self.source.read_text(encoding='utf-8'))
        payload['summary'].update(keyword='AI Native',location='Egypt',posted_within='week',status='partial',reason='Login required')
        self.source.write_text(json.dumps(payload), encoding='utf-8');rr.collect(self.run,[self.source])
        rr.record(self.run,{'skipped':[{'job_id':'1','reason':'Management scope | not hands-on'}],
                           'applied':[{'job_id':'2','score':88,'reason':'Relevant AI delivery','evidence':'runs/test/proof.json'}]})
        text=(self.run/'summary.md').read_text(encoding='utf-8')
        for phrase in ['## At a glance','## Search coverage','AI Native','Egypt','Past week','partial','Login required',
                       '## Applied (1)','88/100','## Skipped (1)','https://linkedin.com/jobs/view/1/',
                       r'Management scope \| not hands-on','## Agent work remaining','## Report scope']:
            self.assertIn(phrase,text)
        self.assertIn('A &amp; B',text)

    def test_cli_workflow_in_an_isolated_repository(self):
        scripts = self.root/'isolated/agent/scripts'
        scripts.mkdir(parents=True)
        for name in ['ledger.py','run_report.py','job_identity.py','assessment_policy.py','runtime.py','file_lock.py']:
            shutil.copy(Path(rr.__file__).parent/name,scripts/name)
        run = self.root/'isolated/runs/cli'
        def call(script,*args,input=None):
            result = subprocess.run([sys.executable,str(scripts/script),*args],input=input,
                                    capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            return json.loads(result.stdout)
        call('run_report.py','collect','--run',str(run),'--jobs',str(self.source))
        result=call('run_report.py','record','--run',str(run),'--decisions','-',
                    input=json.dumps({'skipped':['1','2','3','4']}))
        self.assertEqual(result['counts']['skipped'],4)
        result=call('run_report.py','finalize','--run',str(run))
        self.assertEqual(result['status'],'complete')
        self.assertTrue((run/'summary.md').exists())
        self.assertFalse((run/'digest/delivery.json').exists())
        result=call('run_report.py','blocker','--run',str(run),'--id','email','--reason','Verify account email.')
        self.assertEqual(result['blockers'],1)
        self.assertEqual(result['status'],'incomplete')
        result=call('run_report.py','blocker','--run',str(run),'--id','email','--resolve')
        self.assertEqual(result['status'],'complete')

    def test_collect_and_decision_retries_are_idempotent(self):
        rr.collect(self.run,[self.source])
        self.assertEqual(len(ledger.read_events()),4)
        payload = {'skipped':['1','2']}
        rr.record(self.run,payload)
        rr.record(self.run,payload)
        self.assertEqual(len(ledger.read_events()),6)
        rr.record(self.run,{'applying':[{'job_id':'3','score':90}]})
        self.assertEqual(rr.report(self.run)['unresolved'],2)
        rr.record(self.run,{'applied':[{'job_id':'3','score':90}]})
        rr.record(self.run,{'applied':[{'job_id':'3','score':90}]})
        self.assertEqual(sum(e['event']=='applied' for e in ledger.read_events()),1)
        with self.assertRaises(ValueError):
            rr.record(self.run,{'skipped':['3']})

    def test_invalid_batch_does_not_partially_write(self):
        before = ledger.LEDGER.read_bytes()
        for payload in [{'skipped':['1','999']}, {'needs_input':['1']},
                        [{'job_id':'1','status':'applied','score':999}],
                        {'skipped':['1','1']}]:
            with self.assertRaises(ValueError):
                rr.record(self.run,payload)
            self.assertEqual(ledger.LEDGER.read_bytes(),before)

    def test_prior_application_and_skip_are_not_new_skips(self):
        jobs=rr.manifest(self.run)['jobs']
        ledger.append_events([rr.event_for(jobs['linkedin:1'], self.root/'old', 'applied',score=90),
                              rr.event_for(jobs['linkedin:2'], self.root/'old', 'skipped')])
        report=rr.report(self.run)
        self.assertEqual(report['counts']['previously_resolved'],2)
        self.assertEqual(report['counts']['skipped'],0)
        self.assertEqual(report['unresolved'],2)

    def test_old_blocker_and_draft_survive_later_seen_and_scored_events(self):
        jobs = rr.manifest(self.run)['jobs']
        old = self.run.parent/'old'
        rr.atomic_json(old/'report-input.json', {'run':rr.run_id(old), 'created_at':(datetime.now().astimezone()-timedelta(days=1)).isoformat(), 'jobs':{}, 'sources':{}})
        ledger.append_events([
            rr.event_for(jobs['linkedin:1'],old,'needs_input',notes='Saved draft; user must confirm one field.'),
            rr.event_for(jobs['linkedin:1'],old,'scored',score=90),
            rr.event_for(jobs['linkedin:2'],old,'applying',notes='Verify submission before retrying.'),
            rr.event_for(jobs['linkedin:2'],old,'seen')])
        rep = rr.report(self.run)
        self.assertEqual(rep['jobs'][0]['status'],'needs_input')
        self.assertEqual(rep['jobs'][0]['reason'],'Saved draft; user must confirm one field.')
        self.assertEqual(rep['jobs'][1]['status'],'applying')
        self.assertEqual(rep['unresolved'],3)
        text='\n'.join(rr.summary_lines(rep, self.run))
        self.assertIn('Saved draft; user must confirm one field.',text)
        self.assertNotIn(str(old),text)
        rr.record(self.run,{'skipped':['1'], 'applied':[{'job_id':'2','score':90}]})
        self.assertEqual(rr.report(self.run)['jobs'][0]['status'],'skipped')
        self.assertEqual(rr.report(self.run)['jobs'][1]['status'],'applied')

    def test_later_resolution_and_reposted_job_matching(self):
        job=rr.manifest(self.run)['jobs']['linkedin:1']
        old={**job,'job_id':'999'}
        ledger.append_events([rr.event_for(old,self.root/'old','needs_input',notes='CAPTCHA'),
                              rr.event_for(old,self.root/'old','skipped')])
        self.assertEqual(rr.report(self.run)['jobs'][0]['status'],'previously_resolved')

    def test_run_blocker_can_precede_collection_and_resolve_without_fake_job(self):
        run=self.root/'blocked'
        rr.blocker(run,'account-email','Verify the ATS account email.')
        rep=rr.report(run)
        self.assertEqual(rep['total'],0)
        self.assertEqual(rep['status'],'incomplete')
        self.assertIn('Verify the ATS account email.', '\n'.join(rr.summary_lines(rep, self.run)))
        rr.blocker(run,'account-email','Verify the ATS account email.')
        self.assertEqual(len(rr.report(run)['blockers']),1)
        rr.blocker(run,'account-email',resolve=True)
        self.assertEqual(rr.report(run)['blockers'],[])
        with self.assertRaises(ValueError): rr.blocker(run,'unknown',resolve=True)

    def test_legacy_non_job_blocker_is_visible_and_resolvable(self):
        ledger.append_events([{'event':'needs_input','platform':'workday','run':rr.run_id(self.run),
                               'notes':'Account email verification required.'}])
        rep=rr.report(self.run)
        self.assertEqual(len(rep['blockers']),1)
        rr.blocker(self.run,rep['blockers'][0]['id'],resolve=True)
        self.assertEqual(rr.report(self.run)['blockers'],[])

    def test_timestamps_are_utc_and_summary_uses_configured_zone(self):
        self.assertEqual(datetime.fromisoformat(rr.now()).utcoffset(),timedelta(0))
        rep=rr.report(self.run)
        rep['created_at']='2026-01-01T22:30:00+00:00'
        self.assertIn('01 Jan 2026, 22:30','\n'.join(rr.summary_lines(rep, self.run)))
        rep['created_at']='2026-01-02T00:30:00+02:00'
        self.assertIn('01 Jan 2026, 22:30','\n'.join(rr.summary_lines(rep, self.run)))

    def test_partial_source_and_zero_results_are_reported(self):
        source=self.root/'partial.json'
        source.write_text(json.dumps({'summary':{'status':'partial','reason':'Page limit reached'},'jobs':[]}), encoding='utf-8')
        rr.collect(self.run,[source])
        rr.record(self.run,{'skipped':['1','2','3','4']})
        report=rr.report(self.run)
        self.assertEqual(report['status'],'incomplete')
        self.assertIn('Page limit reached', '\n'.join(rr.summary_lines(report, self.run)))
        empty=self.root/'empty'
        source.write_text(json.dumps({'summary':{'status':'complete'},'jobs':[]}), encoding='utf-8')
        rr.collect(empty,[source])
        self.assertEqual(rr.report(empty)['status'],'complete')
        self.assertNotIn('## Skipped', '\n'.join(rr.summary_lines(rr.report(empty), empty)))

    def test_full_skipped_list_and_long_untrusted_text_are_not_truncated(self):
        report=rr.report(self.run)
        report['jobs']=[{'status':'skipped','title':f'Role {i:04d} '+('&'*300),'company':'X'} for i in range(150)]
        report['counts']['skipped']=150
        report['jobs'].append({'status':'needs_input','title':'<b>untrusted</b>','company':'A',
                              'reason':'&'*5000+' file: /home/test/private/cv.pdf','url':'javascript:alert(1)'})
        text = '\n'.join(rr.summary_lines(report, self.run))
        for i in range(150):
            self.assertIn(f'Role {i:04d}', text)
        self.assertIn('&amp;' * 5000, text)
        self.assertIn('&lt;b&gt;untrusted&lt;/b&gt;', text)
        self.assertNotIn('](javascript:', text)

    def test_preview_does_not_mark_completion(self):
        rr.record(self.run, {'skipped': ['1', '2', '3', '4']})
        before = ledger.LEDGER.read_bytes()
        rr.render(self.run)
        self.assertEqual(ledger.LEDGER.read_bytes(), before)
        self.assertFalse((self.run/'digest').exists())


if __name__ == '__main__':
    unittest.main()
