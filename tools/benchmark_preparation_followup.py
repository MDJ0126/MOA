"""Repeat actual full workflow after increasing optional assessment idle grace."""
import os,time,shutil,json
os.environ['MOA_ASSESSMENT_IDLE']='300'
import benchmark_leader_preparation as run
archive=run.OUT/'intermediate-idle60'
archive.mkdir(exist_ok=True)
for p in run.OUT.glob('after*'):
 if p.is_file():shutil.copy2(p,archive/p.name)
try:
 run.switches(True)
 restart=run.restart('after')
 started=run.prepare('after')
 run.bench.run('after')
 run.check_policy('after',restart,time.perf_counter()-started)
finally:run.switches(True)
