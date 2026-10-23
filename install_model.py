"""Install pinned official artifacts locally; verify publisher SHA-256 before use."""
import hashlib
import json
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
ARTIFACTS = [
    ('runtime/llama-b11669.zip', 'https://github.com/ggml-org/llama.cpp/releases/download/b11669/llama-b11669-bin-win-cpu-x64.zip', '37b3efcf962847ca869745fd4ccf7f5e7e8dacfeff9fa3523b4ca8cdc66d632b'),
    ('models/qwen2.5-coder-7b-instruct-q4_k_m.gguf', 'https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/main/qwen2.5-coder-7b-instruct-q4_k_m.gguf', '509287f78cb4d4cf6b3843734733b914b2c158e43e22a7f4bf5e963800894d3c'),
]

def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def main():
    installed=[]
    for name,url,sha in ARTIFACTS:
        path=ROOT/name
        path.parent.mkdir(parents=True,exist_ok=True)
        if not path.exists() or digest(path)!=sha:
            temp=path.with_suffix(path.suffix+'.part')
            print('Downloading '+name,flush=True)
            with urllib.request.urlopen(url,timeout=60) as response, temp.open('wb') as f:
                total=0; announced=0
                while chunk:=response.read(1024*1024):
                    f.write(chunk);total+=len(chunk)
                    if total-announced>=100*1024*1024:
                        print(f'{total//1024//1024} MiB downloaded',flush=True);announced=total
            if digest(temp)!=sha:
                raise RuntimeError('SHA-256 mismatch: '+name)
            temp.replace(path)
        print('Verified '+name,flush=True)
        installed.append({'path':name,'source':url,'sha256':sha,'bytes':path.stat().st_size})
    with zipfile.ZipFile(ROOT/ARTIFACTS[0][0]) as archive:
        dest=ROOT/'runtime'/'llama'
        for entry in archive.infolist():
            if not (dest/entry.filename).resolve().is_relative_to(dest.resolve()):
                raise ValueError('Invalid archive path')
        for entry in archive.infolist():
            target=dest/entry.filename
            if entry.is_dir():target.mkdir(parents=True,exist_ok=True);continue
            content=archive.read(entry)
            # Do not overwrite matching DLLs that a running model has loaded.
            if target.exists() and digest(target)==hashlib.sha256(content).hexdigest():continue
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
    (ROOT/'models'/'installed.json').write_text(json.dumps(installed,indent=2),encoding='utf-8')
    print('Local model installation complete.',flush=True)

if __name__=='__main__':main()
