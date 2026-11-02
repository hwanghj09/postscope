import asyncio
import importlib.util
import json
from pathlib import Path
from datetime import datetime,timezone
from postscope.services.normalizer import normalize
from postscope.public_snapshot import public_dataset,materialize,publish_files


def test_daily_public_snapshot_does_not_expose_original_ids_or_bodies(tmp_path):
    stamp=datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    row,_=normalize({'id':'900719925474099312345','from_id':'user998877665544332211','date':stamp,'text':'PRIVATE_BODY','grouped_id':'PRIVATE_ALBUM','mime_type':'application/pdf'})
    dataset=public_dataset([row],secret='PRIVATE_CREDENTIAL',title='공개 별칭',start=stamp,end=stamp,collected_at=stamp)
    text=json.dumps(dataset)
    for value in ['900719925474099312345','998877665544332211','PRIVATE_BODY','PRIVATE_ALBUM','PRIVATE_CREDENTIAL']:
        assert value not in text
    assert dataset['messages'][0]['sender_kind']=='user'
    assert public_dataset([row],secret='PRIVATE_CREDENTIAL',title='공개 별칭',start=stamp,end=stamp,collected_at=stamp)==dataset
    store,workspace,chat=materialize(dataset,tmp_path/'store')
    date=datetime.fromisoformat(stamp).date().isoformat()
    output=tmp_path/'public'
    publish_files(store,workspace,chat,output,date,date)
    assert (output/'snapshot.json').exists() and (output/'snapshot.sqlite3').exists()
    assert not any(value in (output/'snapshot.json').read_text() for value in ['PRIVATE_BODY','PRIVATE_CREDENTIAL'])


def test_failed_collection_keeps_last_successful_file(tmp_path,monkeypatch):
    script=Path(__file__).resolve().parents[1]/'scripts'/'collect_daily.py'
    spec=importlib.util.spec_from_file_location('collect_daily',script);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    (tmp_path/'public').mkdir()
    (tmp_path/'public'/'source.json').write_text(json.dumps({'chat':'@configured-test-channel','mode':'telegram'}))
    output=tmp_path/'public'/'dataset.json';output.write_text('previous-success')
    monkeypatch.setattr(module,'ROOT',tmp_path);monkeypatch.setattr(module,'OUTPUT',output)
    for key in ['TELEGRAM_API_ID','TELEGRAM_API_HASH','TELEGRAM_SESSION','TELEGRAM_CHAT']:monkeypatch.delenv(key,raising=False)
    import pytest
    with pytest.raises(ValueError):asyncio.run(module.collect())
    assert output.read_text()=='previous-success'
