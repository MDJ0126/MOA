"""Create an offline Tiny Core Xorg desktop ISO from verified distribution packages."""
import concurrent.futures
import hashlib
import gzip
import json
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request
from install_vm import ROOT,CACHE,fetch,require_stopped_vm
sys.path.insert(0,str(ROOT/'runtime/pythonlibs'))
import pycdlib

BASE='https://distro.ibiblio.org/tinycorelinux/17.x/x86/tcz/'
def main():
    require_stopped_vm()
    source=CACHE/'desktop-source';source.mkdir(exist_ok=True)
    subprocess.run([str(CACHE/'7zip/7z.exe'),'x','-y',str(CACHE/'TinyCore-17.1.iso'),'-o'+str(source)],check=True,stdout=subprocess.DEVNULL)
    optional=source/'cde/optional';optional.mkdir(parents=True,exist_ok=True)
    visited=set();pending=['Xorg-7.7.tcz','graphics-6.18.35-tinycore.tcz'];manifest=[]
    checksums={name:digest for digest,name in (line.split() for line in gzip.decompress(fetch(BASE+'md5.db.gz',CACHE/'packages-md5.db.gz').read_bytes()).decode().splitlines())}
    while pending:
        name=pending.pop()
        if name in visited:continue
        visited.add(name)
        try:
            if (optional/(name+'.dep')).exists():dependencies=(optional/(name+'.dep')).read_text().split()
            else:
                with urllib.request.urlopen(BASE+name+'.dep',timeout=20) as response:dependencies=response.read().decode().split()
        except urllib.error.HTTPError as error:
            if error.code!=404:raise
            dependencies=[]
        dependencies=[dependency.replace('KERNEL','6.18.35-tinycore') for dependency in dependencies]
        pending.extend(dependencies)
        (optional/(name+'.dep')).write_text('\n'.join(dependencies)+'\n',newline='\n')
    def download(name):
        path=fetch(BASE+name,optional/name)
        checksum=checksums[name]
        if hashlib.md5(path.read_bytes()).hexdigest()!=checksum:raise ValueError('Package checksum mismatch: '+name)
        return {'name':name,'published_md5':checksum,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:manifest=list(pool.map(download,sorted(visited)))
    onboot=source/'cde/onboot.lst';lines=onboot.read_text().splitlines()
    lines=[line for line in lines if line not in ('Xvesa.tcz','Xfbdev.tcz','fbdev-Xorg-conf.tcz','Xorg-7.7.tcz')];lines.insert(0,'Xorg-7.7.tcz');lines.insert(0,'graphics-6.18.35-tinycore.tcz')
    onboot.write_text('\n'.join(lines)+'\n',newline='\n')
    cfg=source/'boot/isolinux/isolinux.cfg'
    cfg.write_text(cfg.read_text().replace('TIMEOUT 600','TIMEOUT 20'),newline='\n')
    # Add rock-ridge names so the guest sees case-sensitive original package filenames.
    iso=pycdlib.PyCdlib();iso.new(interchange_level=3,rock_ridge='1.09',vol_ident='MOA_DESKTOP')
    mapped={source:'/'}
    for index,path in enumerate(sorted((p for p in source.rglob('*') if p.is_dir() and not p.name.startswith('[')),key=lambda p:len(p.parts))):
        if path.parent not in mapped:continue
        target=mapped[path.parent].rstrip('/')+'/'+path.name.upper()
        iso.add_directory(iso_path=target,rr_name=path.name);mapped[path]=target
    boot_path=None
    for index,path in enumerate(sorted(source.rglob('*'))):
        if not path.is_file() or path.parent not in mapped or path.name=='boot.cat':continue
        filename=path.name.upper() if 'boot' in path.relative_to(source).parts else f'F{index:06d}.BIN'
        target=mapped[path.parent].rstrip('/')+'/'+filename+';1'
        iso.add_file(str(path),iso_path=target,rr_name=path.name)
        if path.name=='isolinux.bin':boot_path=target
    if not boot_path:raise ValueError('ISOLINUX boot image missing')
    iso.add_eltorito(boot_path,bootcatfile='/BOOT.CAT;1',rr_bootcatname='boot.cat',boot_load_size=4,boot_info_table=True)
    output=ROOT/'runtime/vm-downloads/moa-desktop.iso';iso.write(str(output));iso.close()
    (CACHE/'desktop-manifest.json').write_text(json.dumps({'base':'TinyCore-17.1.iso','packages':manifest,'iso_sha256':hashlib.sha256(output.read_bytes()).hexdigest()},indent=2),encoding='utf-8')
    print(f'Built {output.name}: {len(manifest)} verified packages, {output.stat().st_size//1024**2} MiB',flush=True)
if __name__=='__main__':main()
