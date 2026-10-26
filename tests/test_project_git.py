import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from app import Store
from setup_config import PRESETS,save_team,write_setting
import project_git as pg

class ProjectGitTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=Store(self.root/'data');self.team=save_team(self.store,PRESETS[1])

    def repository(self,empty=False):
        remote=self.root/'remote.git';pg.git(['init','--bare','--initial-branch=main',str(remote)])
        if not empty:
            seed=self.root/'seed';pg.git(['init','--initial-branch=main',str(seed)]);(seed/'README.md').write_text('original project')
            pg.git(['add','.'],seed);pg.git(['commit','-m','initial'],seed,('Original','original@example.test'));pg.git(['remote','add','origin',str(remote)],seed);pg.git(['push','origin','main'],seed)
        write_setting(self.store,'project_git',{'url':str(remote),'branch':'main','connected':False,'authors':{a['id']:a['id']+'@example.test' for a in self.team['agents']}})
        with patch.object(pg,'validate_url',side_effect=lambda value:value):pg.connect(self.store)
        return remote

    def task(self,text):
        tid=self.store.add(text,'demo');folder=self.store.root/'tasks'/tid/'deliverables';folder.mkdir(parents=True);body=text.encode();(folder/'answer.txt').write_bytes(body)
        value={'report_id':'a'*32,'files':{'deliverables/answer.txt':{'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()}}}
        with self.store.connect() as c:c.execute("UPDATE tasks SET status='completed',artifact_publication=? WHERE id=?",(json.dumps(value),tid))
        return tid

    def test_url_credentials_and_options_are_rejected(self):
        for value in ('--upload-pack=evil','file:///private','https://token@github.com/a/b.git','https://github.com/a/b?token=secret','ext::evil'):
            with self.assertRaises(ValueError):pg.validate_url(value)
        self.assertEqual(pg.validate_url('https://github.com/a/b.git'),'https://github.com/a/b.git')

    def test_actual_commits_and_pushes_use_each_teammate_identity(self):
        remote=self.repository()
        for agent in self.team['agents'][:2]:
            tid=self.task(agent['id']);receipt=pg.publish(self.store,tid,agent['id'])
            actual=pg.git(['--git-dir='+str(remote),'show','-s','--format=%an|%ae|%cn|%ce','refs/heads/'+receipt['branch']])
            self.assertEqual(actual,'|'.join((agent['name'],agent['id']+'@example.test')*2))
            self.assertEqual(pg.git(['--git-dir='+str(remote),'show',receipt['branch']+':README.md']),'original project')
            self.assertEqual(pg.git(['--git-dir='+str(remote),'show',receipt['branch']+':'+receipt['path']+'/answer.txt']),agent['id'])
        self.assertEqual(pg.git(['--git-dir='+str(remote),'show','main:README.md']),'original project')

    def test_empty_remote_and_retry_create_one_commit(self):
        remote=self.repository(empty=True);tid=self.task('empty repository');aid=self.team['agents'][0]['id']
        first=pg.publish(self.store,tid,aid);second=pg.publish(self.store,tid,aid)
        self.assertEqual(first['commit'],second['commit']);self.assertEqual(pg.git(['--git-dir='+str(remote),'rev-list','--count',first['branch']]),'1')

    def test_automatic_name_email_migration_rename_and_new_teammate(self):
        agents=self.team['agents'];first=agents[0];second=agents[1]
        write_setting(self.store,'project_git',{'authors':{first['id']:first['id']+'@moa.local',second['id']:'custom@example.test'}})
        value=pg.settings(self.store)
        self.assertEqual(value['authors'][first['id']],pg.default_author_email(first))
        self.assertEqual(value['authors'][second['id']],'custom@example.test')
        pg.save(self.store,{**value,'url':'','branch':''})
        first['name']='김하준'
        agents.append({**first,'id':'newperson','name':'새팀원'})
        write_setting(self.store,'workspace',self.team)
        value=pg.settings(self.store)
        self.assertEqual(value['authors'][first['id']],'kimhajun@moa.local')
        self.assertEqual(value['authors']['newperson'],'saetimwon@moa.local')
        self.assertEqual(value['authors'][second['id']],'custom@example.test')

    def test_actual_unicode_name_defaults_in_author_and_committer(self):
        remote=self.repository()
        value=pg.settings(self.store);value['authors']={};write_setting(self.store,'project_git',value)
        for agent in self.team['agents'][:2]:
            tid=self.task('자동 이름 테스트');receipt=pg.publish(self.store,tid,agent['id'])
            actual=pg.git(['--git-dir='+str(remote),'show','-s','--format=%an|%ae|%cn|%ce','refs/heads/'+receipt['branch']])
            self.assertEqual(actual,'|'.join((agent['name'],pg.default_author_email(agent))*2))

    def test_romanization_of_current_and_future_names(self):
        for name,email in [('김하준','kimhajun'),('이지우','leejiu'),('최서연','choiseoyeon'),('박도윤','parkdoyun'),('한유진','hanyujin'),('Alex Smith','alexsmith')]:
            self.assertEqual(pg.default_author_email({'id':'newperson','name':name}),email+'@moa.local')
        first=self.team['agents'][0]
        write_setting(self.store,'project_git',{'authors':{first['id']:pg.legacy_author_email(first)}})
        self.assertEqual(pg.settings(self.store)['authors'][first['id']],pg.default_author_email(first))
