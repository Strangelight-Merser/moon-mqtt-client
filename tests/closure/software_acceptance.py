"""One original Release run per frozen revision with retained environment evidence."""
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from acceptance import source_identity


def snapshot(out, name):
    commands={'disk':['df','-h',str(out)], 'mount':['mount'],
              'processes':['ps','-axo','pid,ppid,%cpu,%mem,comm'],
              'compiler':[os.environ.get('MOON',str(ROOT/'scripts/moon.sh')),'version','--all']}
    result={'time_ns':time.time_ns(),'platform':platform.platform(),'cwd':str(ROOT),
            'evidence':str(out),'load':os.getloadavg(),'commands':{}}
    for label, command in commands.items():
        process=subprocess.run(command,cwd=ROOT,text=True,capture_output=True)
        result['commands'][label]={'command':command,'returncode':process.returncode,
                                   'stdout':process.stdout,'stderr':process.stderr}
    (out/(name+'.json')).write_text(json.dumps(result,indent=2)+'\n')


def main():
    source=source_identity()
    if source['dirty']: raise SystemExit('Freeze the source before formal Release')
    out=ROOT/'_build/software-closure'/source['commit']/platform.system()
    out.mkdir(parents=True,exist_ok=False) # a duplicate attempt fails, never overwrites
    (out/'source.json').write_text(json.dumps(source,indent=2)+'\n')
    snapshot(out,'before')
    try:
        result=subprocess.run([str(ROOT/'scripts/check.sh'),'release','--evidence',str(out/'acceptance')],cwd=ROOT)
    finally:
        snapshot(out,'after')
    raise SystemExit(result.returncode)

if __name__=='__main__': main()
