"""Normal complete-app exit cleanup; window hiding never reaches this path."""
import time
from setup_config import write_setting

def finish_vm_shutdown(store,vm,worker_thread):
    from vm_runtime import BusyVM
    started=time.time()
    try:
        if worker_thread.is_alive():raise ValueError('업무 정리가 끝나지 않아 VM 정상 종료를 보류했습니다.')
        deadline=time.monotonic()+10
        while True:
            try:result=vm.shutdown_gracefully(timeout=90);break
            except BusyVM:
                if time.monotonic()>=deadline:raise
                time.sleep(.25)
    except Exception as error:
        result={'state':'pending','error':str(error),'forced':False}
    result={**result,'started_at':started,'finished_at':time.time()}
    write_setting(store,'vm_shutdown_result',result)
    print('VM shutdown: '+str(result),flush=True)
    return result
