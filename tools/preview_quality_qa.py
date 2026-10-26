"""Paused UI preview of actual QA records; never runs tasks or sends messages."""
import json,sqlite3,sys,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from app import Store,serve
from setup_config import write_setting
from vm_runtime import DEFAULT
source=ROOT/'reports/leader-review-qa-20261025/data'
target=ROOT/'reports/leader-review-ui/data';target.mkdir(parents=True,exist_ok=True)
with sqlite3.connect(source/'state.sqlite3') as original,sqlite3.connect(target/'state.sqlite3') as output:original.backup(output)
store=Store(target)
write_setting(store,'paused',True);write_setting(store,'vm',DEFAULT)
with store.connect() as c:
 tid=c.execute('SELECT id FROM tasks ORDER BY created DESC LIMIT 1').fetchone()[0]
 for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
  name=row[0]
  if not name.replace('_','').isalnum():continue
  columns=[r[1] for r in c.execute('PRAGMA table_info('+name+')')]
  if 'task_id' in columns:c.execute('DELETE FROM '+name+' WHERE task_id<>?',(tid,))
 c.execute('DELETE FROM tasks WHERE id<>?',(tid,))
folder=source/'tasks'/tid
if folder.exists():shutil.copytree(folder,target/'tasks'/tid,dirs_exist_ok=True)
config=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
print('ACTUAL_QA_PREVIEW_TASK='+tid,flush=True)
serve(target,8878,config['model'],config['model_url'])
