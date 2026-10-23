"""Readable report snapshots and staged artifact publication."""
import hashlib
import html
import json
from pathlib import Path
import re
import shutil
import tempfile
import time
import zipfile
from conversation import for_agent

LABELS={'planner':'계획','developer':'개발','reviewer':'검증','reporter':'보고'}
STATES={'completed':'완료','failed':'확인 필요','interrupted':'중단됨','cancelled':'취소됨','queued':'대기 중','running':'진행 중'}


def text(value):
    value=html.escape(' '.join(str(value).split()),quote=False)
    return re.sub(r'([\\`*_{}\[\]()#+.!|>~-])',r'\\\1',value)


def markdown(report):
    verification=report.get('verification') or {};plan=report['team']
    names={a['id']:a['name'] for a in plan['agents']};names.update(user='사용자',system='작업 관리자')
    count=verification.get('tests_run');count=count if type(count) is int and count>=0 else None
    if report.get('verification_matches_files') is False:verdict='검증 후 파일 변경 · 현재 파일 재검증 필요'
    elif report['mode']=='demo':verdict=('데모 흐름 확인' if report['status']=='completed' else '데모 흐름 미완료')+' · 실제 AI 추론과 실행 테스트 없음'
    elif verification.get('passed') is False:verdict='검증 실패'
    elif verification.get('passed') is True and count and count>0:verdict=f'실행 테스트 {count}개 통과'+(' · 검증 파일 일치 미확인' if report.get('verification_matches_files') is None else '')
    elif verification.get('passed') is True:verdict='자동 파일 검사만 통과 · 실행 테스트 없음'
    else:verdict='검증 미실시 또는 미확인'
    lines=['# 작업 결과',f'**상태:** {STATES.get(report["status"],text(report["status"]))} · **작업:** {text(report["task_id"])}',
           '## 요청',text(report['title']),'## 검증',verdict]
    if verification.get('scope'):lines.append('검사 범위: '+text(verification['scope']))
    contract=report.get('execution_environment',{}).get('execution_contract')
    if contract:
        lines.extend(['## 실행 대상',text(contract['target_os'])+' · '+text(contract['architecture'])+' · 현재 PC', 'Python 검사 범위입니다. 데모는 실행 테스트가 없으며 엔진·화면 동작은 이 결과로 검증하지 않습니다.'])
    if count is not None:lines.append(f'실행 테스트 수: {count}개')
    lines.extend(['## 담당 흐름',' → '.join(text(names.get(plan['assignment'][role],plan['assignment'][role]))+' · '+LABELS.get(role,role) for role in plan['pipeline']),
                  f'수정 회차: {report["revision"]}','## 담당자별 결과'])
    for row in report['history']:
        if row['kind']!='handoff':continue
        try:
            value=json.loads(row['content']);summary=value.get('summary',row['content']) if isinstance(value,dict) else row['content']
            if isinstance(value,dict) and type(value.get('passed')) is bool:summary=('담당자 판단: 통과 · ' if value['passed'] else '담당자 판단: 수정 요청 · ')+str(summary)
        except (ValueError,TypeError):summary=row['content']
        summary=str(summary)
        if len(summary)>1000:summary=summary[:1000]+' … (전체 내용은 report.json)'
        label=names.get(row['agent'],row['agent'])
        if row.get('capability'):label+=' · '+LABELS.get(row['capability'],row['capability'])
        if row.get('recipient'):label+=' → '+names.get(row['recipient'],row['recipient'])
        lines.append('**'+text(label)+'**: '+text(summary))
    issues=[report.get('error',''),*verification.get('errors',[])]
    issues += ['필수 파일 없음: '+str(name) for name in verification.get('missing_files',[])]
    if any(issues):lines.extend(['## 확인할 내용',*['- '+text(item) for item in issues if item]])
    output=verification.get('output')
    if verification.get('passed') is False and output:
        output=str(output)[-2000:];fence='`'*max(3,max((len(m[0])+1 for m in re.finditer(r'`+',output)),default=3))
        lines.extend(['### 검증 로그 끝부분',fence+'text\n'+output+'\n'+fence])
    lines.extend(['## 결과 파일','\n'.join(['| 파일 | SHA-256 |','| --- | --- |']+['| '+text(item['path'])+' | '+item['sha256']+' |' for item in report['files']])])
    lines.extend(['## 실행 범위와 제한',*['- '+text(item) for item in report['limitations']],
                  '보고서 ID: '+text(report['report_id']),
                  '검증 상세·전체 대화: report.json · 결과물 묶음: artifacts.zip'])
    return '\n\n'.join(lines)+'\n'


def workspace_files(workspace):
    original=Path(workspace)
    if original.is_symlink() or getattr(original,'is_junction',lambda:False)():raise ValueError('작업 폴더 링크는 결과물로 묶을 수 없습니다.')
    workspace=Path(workspace).resolve();result=[]
    for path in sorted(workspace.rglob('*')):
        if '__pycache__' in path.parts:continue
        if path.is_symlink() or getattr(path,'is_junction',lambda:False)() or not path.resolve().is_relative_to(workspace):
            raise ValueError('결과물에는 작업 폴더 밖 경로 또는 링크를 포함할 수 없습니다.')
        if path.is_file():
            if path.stat(follow_symlinks=False).st_nlink!=1:raise ValueError('결과물에 하드링크를 포함할 수 없습니다.')
            result.append(path)
    return result


def hash_file(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def _replace(source,destination):
    for attempt in range(5):
        try:source.replace(destination);return
        except OSError:
            if attempt==4:raise
            time.sleep(.05)


def publish(folder,workspace,report):
    folder=Path(folder).resolve();workspace=Path(workspace).absolute()
    expected={item['path']:item['sha256'] for item in report['files']}
    with tempfile.TemporaryDirectory(prefix='.report-',dir=folder) as temporary:
        staged=Path(temporary)
        sources=workspace_files(workspace)
        if {str(p.relative_to(workspace)) for p in sources}!=set(expected):raise ValueError('보고 준비 중 결과 파일 목록이 변경되었습니다.')
        for source in sources:
            relative=source.relative_to(workspace);target=staged/'workspace'/relative
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
            if hash_file(target)!=expected[str(relative)]:raise ValueError('보고 준비 중 결과 파일 내용이 변경되었습니다.')
        for name in report['vm_evidence']:
            source=folder/name;target=staged/name
            if source.is_symlink() or not source.resolve().is_relative_to(folder.resolve()):raise ValueError('VM 근거 파일 경로가 올바르지 않습니다.')
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
            if hash_file(target)!=report['vm_evidence_sha256'][name]:raise ValueError('VM 근거 파일이 변경되었습니다.')
        (staged/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        (staged/'report.md').write_text(markdown(report),encoding='utf-8',newline='\n')
        agents=set((*LABELS,*(a['id'] for a in report['team']['agents']),'system','user'))
        for agent in agents:
            path=staged/'agents'/agent/'messages.json';path.parent.mkdir(parents=True)
            path.write_text(json.dumps([m for m in report['history'] if for_agent(m,agent,report['team'])],ensure_ascii=False,indent=2),encoding='utf-8')
        with zipfile.ZipFile(staged/'artifacts.zip','w',zipfile.ZIP_DEFLATED) as bundle:
            for source in sorted(staged.rglob('*')):
                if source.is_file() and source!=staged/'artifacts.zip':bundle.write(source,source.relative_to(staged).as_posix())
        # Build and hash-check the complete snapshot before exposing any new reports.
        for source in sorted(staged.rglob('*')):
            if not source.is_file() or source==staged/'artifacts.zip' or source.relative_to(staged).parts[0] in ('workspace','vm'):continue
            destination=folder/source.relative_to(staged);destination.parent.mkdir(parents=True,exist_ok=True);_replace(source,destination)
        _replace(staged/'artifacts.zip',folder/'artifacts.zip')
