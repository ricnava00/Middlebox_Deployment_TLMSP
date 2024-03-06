import os
import signal
import subprocess
import json
import sys
import time
import paramiko
from traceback import format_exception

VAGRANT = False
SRV_IP = "192.168.58.1"
MB_IP = "192.168.56.2"
TEST_TIME = 120
SLEEP_TIME = 120

if VAGRANT:
    srv_user = mb_user = srv_password = mb_password = "vagrant"
else:
    srv_user = ""
    srv_password = ""
    mb_user = ""
    mb_password = ""

srv_tlmsp_install_path = mb_tlmsp_install_path = client_tlmsp_install_path = "/home/vagrant/tlmsp/install"
httpd_conf_file = srv_tlmsp_install_path + "/etc/apache24/httpd_tlmsp.conf"
if VAGRANT:
    srv_tlmsp_base_path = mb_tlmsp_base_path = client_tlmsp_base_path = "/home/vagrant/external/ETSI"
    mb_dc_base_path = client_dc_base_path = "/home/vagrant/external/DC"
else:
    srv_tlmsp_base_path = mb_tlmsp_base_path = client_tlmsp_base_path = "/home/ric00/Repo/ETSI"
    mb_dc_base_path = client_dc_base_path = "/home/ric00/Repo/DC"
client_tlmsp_conf_path = client_tlmsp_base_path + "/Configurations"
mb_tlmsp_conf_path = mb_tlmsp_base_path + "/Configurations"
srv_tlmsp_conf_path = srv_tlmsp_base_path + "/Configurations"
mb_tlmsp_middlebox_path = mb_tlmsp_base_path + "/Middlebox"
client_dc_middlebox_path = client_dc_base_path + "/Middlebox"
mb_dc_middlebox_path = mb_dc_base_path + "/Middlebox"


def background(host, command):
    print(f"Running {command} on {host.get_transport().getpeername()}")
    _, stdout, stderr = host.exec_command(command)
    pid = int(stdout.readline())
    time.sleep(1)
    if stdout.channel.exit_status_ready():
        print(stdout.read().decode('iso-8859-1'))
        print(stderr.read().decode('iso-8859-1'))
        for host, pid in running:
            host.exec_command(f"kill {pid}")
        return False
    running.append((host, pid))
    return True


def run_tlmsp(conf_file, output_file):
    _, stdout, _ = srv.exec_command(f"sed -i 's#TLMSPConfigFile.*#TLMSPConfigFile \"{srv_tlmsp_conf_path}/{conf_file}\"#' {httpd_conf_file}")
    assert stdout.channel.recv_exit_status() == 0
    if not background(srv, f"echo $$; . {srv_tlmsp_install_path}/share/tlmsp-tools/tlmsp-env.sh; exec httpd -X"):
        return
    if not background(mb, f"echo $$; . {mb_tlmsp_install_path}/share/tlmsp-tools/tlmsp-env.sh; cd {mb_tlmsp_middlebox_path}; rm session.dat; tlmsp-mb -c {mb_tlmsp_conf_path}/{conf_file} -a"):
        return
    passthru(f". {client_tlmsp_install_path}/share/tlmsp-tools/tlmsp-env.sh; python measure.py -s https://{SRV_IP}:4444 -a {JWT} --tlmsp {client_tlmsp_conf_path}/{conf_file} -o {output_file} -t {TEST_TIME}")
    kill_background()


def kill_background():
    for host, pid in running:
        print(f"Killing {pid} on {host.get_transport().getpeername()}")
        host.exec_command(f"kill {pid}")
    running.clear()


def run_curl(output_file):
    passthru(f"python measure.py -s http://{SRV_IP}:8080 -a {JWT} -o {output_file} -t {TEST_TIME}")


def run_goclient(output_file):
    passthru(f"python measure.py -s http://{SRV_IP}:8080 -a {JWT} --go {client_dc_middlebox_path}/client -o {output_file} -t {TEST_TIME}")


def run_dc(empty, output_file):
    if not background(mb, f"echo $$; cd {mb_dc_middlebox_path}; exec ./middlebox{'_empty' if empty else ''} 2>/dev/null >/dev/null"): # problems with buffering, fix if needed
        return
    passthru(f"python measure.py -s https://{MB_IP}:8443 -a {JWT} --go {client_dc_middlebox_path}/client -o {output_file} -t {TEST_TIME}")
    kill_background()


def passthru(command):
    p = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True)
    while p.poll() is None:
        out = p.stdout.read(1)
        if out:
            print(out.decode('iso-8859-1'), end='')
    print()


def t():
    return time.strftime("%Y-%m-%d_%H-%M-%S")


def stop_process(*args):
    global force_exit
    if not force_exit:
        force_exit = True
        kill_background()
    exit(1)


def excepthook(type_, value, traceback):
    print("".join(format_exception(type_, value, traceback)))
    stop_process()


def cleardb():
    print("Clearing DB")
    _, stdout, stderr = srv.exec_command('sudo kubectl run -i --rm --namespace=openfaas-fn --restart=Never --image=mysql:5.6 mysql-client -- mysql -h mysql -ppass -e "DROP DATABASE IF EXISTS helloRetail; CREATE DATABASE helloRetail"')
    if stdout.channel.recv_exit_status() != 0:
        print("Failed to create database")
        print(stderr.read().decode('iso-8859-1'))
        exit(1)


force_exit = False
signal.signal(signal.SIGINT, stop_process)
sys.excepthook = excepthook

os.environ["PYTHONUNBUFFERED"] = "1"
running = []
os.chdir("../OAuthTokenGetter")
out = subprocess.run(["./refresh_token.sh"], stdout=subprocess.PIPE).stdout.decode('utf-8')
JWT = json.loads(out)["id_token"]
os.chdir("../PerformanceMeasuring")
srv = paramiko.SSHClient()
srv.set_missing_host_key_policy(paramiko.AutoAddPolicy())
srv.connect(SRV_IP, username=srv_user, password=srv_password)
mb = paramiko.SSHClient()
mb.set_missing_host_key_policy(paramiko.AutoAddPolicy())
mb.connect(MB_IP, username=mb_user, password=mb_password)
_, stdout, _ = srv.exec_command("killall -s 9 httpd")
stdout.channel.recv_exit_status()
_, stdout, _ = mb.exec_command("killall -s 9 tlmsp-mb")
stdout.channel.recv_exit_status()
_, stdout, _ = mb.exec_command("killall -s 9 middlebox")
stdout.channel.recv_exit_status()
_, stdout, _ = mb.exec_command("killall -s 9 middlebox_empty")
stdout.channel.recv_exit_status()
if not os.path.exists("auto"):
    os.mkdir("auto")
cleardb()
while True:
    print("\033[1;36mDirect curl\033[0m");
    run_curl("auto/" + t() + "_direct_curl.res")
    time.sleep(SLEEP_TIME)
    cleardb()
    print("\033[1;36mDirect go\033[0m");
    run_goclient("auto/" + t() + "_direct_goclient.res")
    time.sleep(SLEEP_TIME)
    cleardb()
    print("\033[1;36mTLMSP forward\033[0m");
    run_tlmsp("read_write.ucl", "auto/" + t() + "_tlmsp_empty.res")
    time.sleep(SLEEP_TIME)
    cleardb()
    print("\033[1;36mTLMSP full\033[0m");
    run_tlmsp("randomization.ucl", "auto/" + t() + "_tlmsp.res")
    time.sleep(SLEEP_TIME)
    cleardb()
    print("\033[1;36mDC forward\033[0m");
    run_dc(True, "auto/" + t() + "_go_empty.res")
    time.sleep(SLEEP_TIME)
    cleardb()
    print("\033[1;36mDC full\033[0m");
    run_dc(False, "auto/" + t() + "_go.res")
    time.sleep(SLEEP_TIME)
    cleardb()
