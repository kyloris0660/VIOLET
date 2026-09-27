import copy,json,sys
from dataclasses import replace
import pytest


@pytest.mark.parametrize('status,valid',[('failed',False),('failed',True),('reserved',True),('success',False)])
def test_failed_or_revoked_pair_predecessor_cannot_authorize_correction(tmp_path,monkeypatch,status,valid):
    from test_production_pixiv_adjudication import config,MeteredProvider,_eligible_llm_edges,service
    from app.services.production_pixiv_pair_correction import plan_corrected_pairs
    signals,edges=_eligible_llm_edges(1);provider=MeteredProvider()
    monkeypatch.setattr(service,'primary_openai_provider_from_settings',lambda:(provider,{}))
    cfg=replace(config(tmp_path),prompt_version=service.PRODUCTION_PAIR_PROMPT_VERSION)
    rows,_=service.run_bounded_llm_adjudication(edges,signals=signals,config=cfg)
    ledger=json.loads((tmp_path/'budget.json').read_text())
    changed=[replace(s,work_context_key='corrected-context') for s in signals]
    assert plan_corrected_pairs(edges,changed,rows,cfg,ledger)[1]['dispatchable_call_ceiling']==1
    ledger['calls'][0].update(status=status,business_valid=valid)
    with pytest.raises(ValueError,match='correction_prior_attempt_unverified'):
        plan_corrected_pairs(edges,changed,rows,cfg,ledger)
    assert provider.calls==1


@pytest.mark.parametrize('label',['focused','postgresql'])
@pytest.mark.parametrize('defect',['trivial','omit','duplicate','filter','xml','python','cwd','none'])
def test_release_requires_complete_registered_test_command(tmp_path,label,defect):
    from scripts.check_production_pixiv_a2 import ROOT,verify_required_test_command
    targets=json.loads((ROOT/'docs/state/production-pixiv-a2-required-tests.json').read_text())[label]
    xml=tmp_path/'result.xml';xml.write_text('<testsuites/>')
    command={'argv':[sys.executable,'-m','pytest',*targets,'-q','--junitxml='+str(xml)],'cwd':str(ROOT)}
    if defect=='trivial':command['argv'][3:-2]=['tests/test_trivial.py']
    elif defect=='omit':command['argv'].pop(3)
    elif defect=='duplicate':command['argv'][3]=command['argv'][4]
    elif defect=='filter':command['argv'][3:3]=['-k','trivial']
    elif defect=='xml':command['argv'][-1]='--junitxml='+str(tmp_path/'other.xml')
    elif defect=='python':command['argv'][0]=str(tmp_path/'other-python')
    elif defect=='cwd':command['cwd']=str(tmp_path)
    if defect=='none':verify_required_test_command(tmp_path,{'xml':xml.name},command,label)
    else:
        with pytest.raises(ValueError,match='validation_'):
            verify_required_test_command(tmp_path,{'xml':xml.name},command,label)
