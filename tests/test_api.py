"""End-to-end API tests, including real FASTQ QC execution."""
import os
import tempfile
import time
from pathlib import Path

os.environ['BIOCOMPUTE_STATE_DIR'] = tempfile.mkdtemp(prefix='bio-test-state-')
os.environ['BIOCOMPUTE_ADMIN_TOKEN'] = 'test-token-not-for-production'

from fastapi.testclient import TestClient
from biocompute.app import app
from biocompute.config import DATA_DIR


def call(client, method, path, **kwargs):
    headers={'Authorization': 'Bearer test-token-not-for-production'}
    return client.request(method, path, headers=headers, **kwargs)


def test_authentication_and_inventory():
    with TestClient(app) as c:
        assert c.get('/api/pipelines').status_code==401
        assert c.post('/api/auth/login', json={'token':'bad'}).status_code==401
        assert c.post('/api/auth/login', json={'token':'test-token-not-for-production'}).status_code==200
        assert call(c,'GET','/api/health').status_code==200
        nodes=call(c,'GET','/api/nodes').json()
        assert any(n['id']=='local-controller' and n['can_execute_now'] for n in nodes)
        assert call(c,'POST','/api/nodes/discover').status_code==200
        assert call(c,'GET','/api/pipelines').status_code==200


def test_file_browser_stays_in_approved_roots():
    with TestClient(app) as c:
        assert call(c,'GET','/api/files?path=/etc').status_code == 403
        assert call(c,'GET', '/api/files', params={'path':str(DATA_DIR)}).status_code == 200
        entries=call(c,'GET','/api/files',params={'path':str(DATA_DIR)}).json()['entries']
        assert any(e['name'].endswith('.fastq') for e in entries)
        # A symlink from inside an approved root must not escape the approved root.
        link=DATA_DIR/'escape-symlink-test'
        try:
            link.symlink_to('/etc')
            assert call(c,'GET','/api/files', params={'path':str(link)}).status_code == 403
        finally:
            link.unlink(missing_ok=True)


def samples():
    return [{'sample':f'{group}_rep{rep}', 'condition':group,'replicate':rep,
             'file1':str(DATA_DIR/f'{group}_rep{rep}_R1.fastq'),'file2':''}
            for group in ('Control','Treatment') for rep in (1,2)]


def test_experiment_samples_validation_and_storage():
    with TestClient(app) as c:
        payload={'name':'K562 test','assay':'Epigenomics','samples':samples()}
        assert call(c,'POST','/api/experiments/validate',json=payload).json()['valid']
        saved=call(c,'POST','/api/experiments',json=payload).json()
        assert saved['sample_count']==4
        assert any(e['id']==saved['id'] for e in call(c,'GET','/api/experiments').json())
        invalid=payload|{'samples':[samples()[0],samples()[0]]}
        assert not call(c,'POST','/api/experiments/validate',json=invalid).json()['valid']


def test_real_qc_run_generates_artifacts():
    with TestClient(app) as c:
        response=call(c,'POST','/api/runs',json={
            'pipeline_id':'builtin-fastq-qc','name':'FASTQ integration test','samples':samples(),
            'params':{'max_reads':80,'min_length':20},'node_ids':['local-controller']})
        assert response.status_code == 200, response.text
        rid=response.json()['id']
        status='queued'
        for _ in range(150):
            result=call(c,'GET',f'/api/runs/{rid}').json()
            status=result['status']
            if status in ('completed','failed','interrupted'):
                break
            time.sleep(.03)
        assert status == 'completed', result
        logs=call(c,'GET',f'/api/runs/{rid}/logs').json()
        assert 'Completed. 4 file metrics' in logs['content']
        artifacts=call(c,'GET',f'/api/runs/{rid}/artifacts').json()
        names={r['name'] for r in artifacts}
        assert {'report.html','qc_metrics.csv','summary.json'} <= names
        response=call(c,'GET',f'/api/runs/{rid}/artifacts/qc_metrics.csv')
        assert response.status_code == 200
        assert 'mean_quality' in response.text
        assert c.get(f'/api/runs/{rid}/artifacts/qc_metrics.csv').status_code == 401
        assert call(c,'GET',f'/api/runs/{rid}/artifacts/does-not-exist').status_code == 404


def test_run_rejects_bad_params_and_remote_nodes():
    with TestClient(app) as c:
        body={'pipeline_id':'builtin-fastq-qc','name':'test','samples':samples()}
        assert call(c,'POST','/api/runs',json=body|{'params':{'max_reads':-1}}).status_code == 422
        assert call(c,'POST','/api/runs',json=body|{'node_ids':['remote-pc']}).status_code == 409
        assert call(c,'POST','/api/runs',json=body|{'samples':[{'sample':'a','file1':'/etc/passwd'}]}).status_code == 422


def test_candidate_is_not_automatically_recruited():
    with TestClient(app) as c:
        r=call(c,'POST','/api/nodes/manual',json={'hostname':'unapproved-pc.local','address':'192.168.1.12'})
        assert r.status_code == 200
        data=r.json()
        assert not data['authorized'] and not data['enabled'] and not data['can_execute_now']
        assert call(c,'PATCH',f"/api/nodes/{data['id']}",json={'enabled':True}).status_code == 409


def test_schema_and_studio():
    with TestClient(app) as c:
        p=call(c,'GET','/api/pipelines/nextflow-fastq-qc').json()
        assert any(f['name']=='min_length' and f['group']=='Analysis parameters' for f in p['fields'])
        r=call(c,'POST','/api/studio/graphs',json={'name':'Trial',
                'nodes':[{'id':'a','type':'input','x':10,'y':10}], 'edges':[]})
        assert r.status_code == 200
        assert any(g['name']=='Trial' for g in call(c,'GET','/api/studio/graphs').json())


def test_agent_heartbeat_only_enrolls_inventory():
    with TestClient(app) as c:
        data={'machine_id':'test-agent-12345678','hostname':'home-imac','os':'Darwin','arch':'x86_64',
              'cpu_total':8,'ram_total':16*1024**3}
        assert c.post('/api/agents/heartbeat',json=data).status_code == 401
        # Set a deterministic worker-token for this test; no execution is enabled.
        os.environ['BIOCOMPUTE_AGENT_TOKEN']='test-agent-inventory-token'
        response=c.post('/api/agents/heartbeat',json=data,headers={'X-Agent-Token':'test-agent-inventory-token'})
        assert response.status_code == 200
        assert not response.json()['execution_enabled']
        remote=next(n for n in call(c,'GET','/api/nodes').json() if n['hostname']=='home-imac')
        assert remote['cpu_total']==8
        assert not remote['authorized'] and not remote['enabled'] and not remote['can_execute_now']


def test_recent_nfcore_style_dollar_defs_and_samplesheet_metadata():
    from biocompute.pipelines import flatten_properties, sample_columns
    from biocompute.runner import RunService
    schema={'$defs': {'group':{'title':'Genome options','properties':{'genome':{'type':'string','default':'GRCh38'}}}},
            'x-biocompute-input-schema': {'items':{'properties':{'sample':{'type':'string'},'fastq_1':{'type':'string'},'strandedness':{'type':'string'}},
                                                    'required':['sample','fastq_1','strandedness']}}}
    assert flatten_properties(schema)[0]['group']=='Genome options'
    assert len(sample_columns(schema))==3
    path=Path(tempfile.mkdtemp())/'samplesheet.csv'
    RunService._write_samplesheet(path, [samples()[0]|{'metadata':{'strandedness':'reverse'}}],sample_columns(schema))
    assert 'strandedness' in path.read_text()
    assert 'reverse' in path.read_text()


def test_frontend_served_and_node_limits_checked():
    with TestClient(app) as c:
        assert '<title>BioCompute' in c.get('/').text
        assert c.get('/assets/app.js').status_code == 200
        local=next(n for n in call(c,'GET','/api/nodes').json() if n['id']=='local-controller')
        assert call(c,'PATCH','/api/nodes/local-controller',json={'cpu_limit':local['cpu_total']+1}).status_code == 422
        assert call(c,'PATCH','/api/nodes/local-controller',json={'cpu_limit':max(local['cpu_total']-1,1)}).status_code == 200


def test_execution_api_lists_real_published_outputs_and_downloads_nested_files():
    with TestClient(app) as c:
        response = call(c, 'POST', '/api/runs', json={
            'pipeline_id': 'builtin-fastq-qc', 'name': 'Inspect output provenance',
            'samples': samples(), 'params': {'max_reads': 30, 'min_length': 20}})
        assert response.status_code == 200, response.text
        rid = response.json()['id']
        for _ in range(120):
            detail = call(c, 'GET', f'/api/runs/{rid}').json()
            if detail['status'] in ('completed', 'failed'):
                break
            time.sleep(.04)
        assert detail['status'] == 'completed'
        r = call(c, 'GET', f'/api/runs/{rid}/execution')
        assert r.status_code == 200
        info = r.json()
        assert info['engine'] == 'builtin'
        assert info['tasks'][0]['name'] == 'FASTQ Quality Explorer'
        assert {'report.html', 'summary.json', 'qc_metrics.csv'} <= {f['path'] for f in info['published']}
        assert info['published_count'] >= 3
        from biocompute.config import WORK_DIR
        nested = WORK_DIR / rid / 'results' / 'subfolder'
        nested.mkdir()
        (nested / 'read_counts.tsv').write_text('sample\treads\nS1\t3\n')
        info = call(c,'GET',f'/api/runs/{rid}/execution').json()
        assert 'subfolder/read_counts.tsv' in {f['path'] for f in info['published']}
        assert call(c,'GET',f'/api/runs/{rid}/artifacts/subfolder/read_counts.tsv').status_code == 200
        assert call(c,'GET',f'/api/runs/{rid}/artifacts/%2E%2E%2Fparams.json').status_code in (400,404)
        (nested / 'outside').symlink_to('/etc/passwd')
        assert 'subfolder/outside' not in {f['path'] for f in call(c,'GET',f'/api/runs/{rid}/execution').json()['published']}
        assert call(c,'GET',f'/api/runs/{rid}/artifacts/subfolder/outside').status_code == 404
        assert c.get(f'/api/runs/{rid}/execution').status_code == 401


def test_nextflow_trace_task_file_snapshot_and_safe_published_symlink(tmp_path):
    from biocompute.execution import execution_snapshot, safe_output_path
    root = tmp_path / 'a-run'
    results = root / 'results'
    results.mkdir(parents=True)
    task = root / 'work' / 'a1' / 'b2c3d4abcdef'
    task.mkdir(parents=True)
    (task / 'aligned.bam').write_bytes(b'BAM-EXAMPLE')
    (task / '.command.sh').write_text('echo hello')
    (results / 'mapped.bam').symlink_to(task / 'aligned.bam')
    trace = results / 'trace.txt'
    trace.write_text('task_id\thash\tname\tstatus\trealtime\n1\ta1/b2c3d4\tBOWTIE2 (sample1)\tCOMPLETED\t12s\n')
    snap = execution_snapshot(root,'nextflow','completed')
    assert snap['trace_available']
    assert snap['tasks'][0]['name'] == 'BOWTIE2 (sample1)'
    assert 'aligned.bam' in [f['path'] for f in snap['tasks'][0]['workspace_files']]
    assert '.command.sh' not in [f['path'] for f in snap['tasks'][0]['workspace_files']]
    assert 'mapped.bam' in [f['path'] for f in snap['published']]
    assert safe_output_path(root,'mapped.bam') == (task/'aligned.bam')
    (results / 'outside.txt').symlink_to('/etc/passwd')
    assert 'outside.txt' not in [f['path'] for f in execution_snapshot(root,'nextflow','completed')['published']]
    import pytest
    with pytest.raises(ValueError):
        safe_output_path(root,'outside.txt')
    with pytest.raises(ValueError):
        safe_output_path(root,'../params.json')


def test_registry_html_parse_and_catalog_pagination(monkeypatch):
    import io
    from biocompute import registry
    class Result(io.BytesIO):
        def __enter__(self):return self
        def __exit__(self,*args):self.close()
    def page(rows):
        return '<table><tbody>'+''.join(
            f'<tr><td><a href="/pipelines/nf-core/{n}@1.0.0">nf-core/{n}</a> 1.0.0</td><td>chip epigenomics</td><td>{n} analysis</td></tr>'
            for n in rows)+'</tbody></table>'
    def fetch(req,timeout=12):
        url=req.full_url
        n=1 if 'page=1' in url else 2
        return Result(page([f'workflow{i}' for i in range(20)] if n==1 else ['chipseq']).encode())
    monkeypatch.setattr(registry, 'urlopen', fetch)
    listing=registry.list_registry_pipelines(force=True)
    assert listing['total']==21
    assert any(p['id']=='nf-core/chipseq' for p in listing['pipelines'])
    filtered=registry.list_registry_pipelines(search='chipseq')
    assert filtered['filtered']==1
    with TestClient(app) as c:
        result=call(c,'GET','/api/registry/pipelines',params={'search':'chipseq'})
        assert result.status_code==200
        assert len(result.json()['pipelines'])==1
        bad=call(c,'POST','/api/registry/import',json={'pipeline_id':'nf-core/chipseq'})
        assert bad.status_code==422  # Import requires explicit review/consent.
        bad=call(c,'POST','/api/registry/import',json={'pipeline_id':'bad slug', 'confirm_untrusted_code':True})
        assert bad.status_code==422
