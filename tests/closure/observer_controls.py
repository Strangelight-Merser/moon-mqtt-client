"""Exercise actual observer failure paths, not just edited JSON fixtures."""
import json
import os
import platform
from pathlib import Path
import subprocess
from dial_evidence import HERE, observed_env, require


def check(library, out):
    directory=out/'observer-controls'; directory.mkdir()
    source=directory/'probe.c'
    source.write_text('#include <sys/socket.h>\n#include <arpa/inet.h>\n#include <unistd.h>\nint main(void){int s=socket(AF_INET,SOCK_DGRAM,0);struct sockaddr_in a={0};a.sin_family=AF_INET;a.sin_port=htons(9);inet_pton(AF_INET,"127.0.0.1",&a.sin_addr);int r=connect(s,(void*)&a,sizeof a);close(s);return r!=0;}\n')
    binary=directory/'observer_probe'; subprocess.run(['cc',str(source),'-o',str(binary)],check=True)
    output=directory/'overflow.jsonl'
    env={**os.environ,**observed_env(library,binary,output),'MQTT_DIAL_CAPACITY':'1'}
    result=subprocess.run([binary],env=env,capture_output=True)
    require(result.returncode==123,'observer did not fail closed on real buffer overflow')
    footer=json.loads(output.read_text().splitlines()[-1])
    require(footer['total']==2 and footer['lost']==1,'overflow accounting incorrect')
    output=directory/'existing.jsonl'; output.write_text('do not overwrite\n')
    env={**os.environ,**observed_env(library,binary,output)}
    result=subprocess.run([binary],env=env,capture_output=True)
    require(result.returncode==122 and output.read_text()=='do not overwrite\n','observer output failure was accepted or replaced evidence')
    report={'real_overflow_exit':123,'real_output_failure_exit':122,'preserved_existing_output':True}
    timing=directory/'observer_timing'
    options=['-pthread'] + (['-ldl'] if platform.system()=='Linux' else [])
    subprocess.run(['cc','-O2','-Wall','-Wextra','-Werror',str(HERE/'observer_timing_control.c'),
                    *options,'-o',str(timing)],check=True)
    result=subprocess.run([timing],capture_output=True,text=True,timeout=10)
    require(result.returncode==0, 'observer misses accept before connect returns: '+result.stdout+result.stderr)
    report['accept_before_connect_return']=json.loads(result.stdout)
    (directory/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report
