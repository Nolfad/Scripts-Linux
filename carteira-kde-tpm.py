#!/usr/bin/python3 -I
"""Desbloqueia kdewallet no login automático do Plasma 6 (TUXEDO OS/Ubuntu).
Execute como seu usuário, em um terminal da sessão KDE. A instalação usa sudo.
"""
import argparse
import fcntl
import getpass
import hashlib
import os
from pathlib import Path
import pwd
import re
import resource
import shutil
import socket
import subprocess as sp
import sys
import tempfile
import time

SELF = Path(__file__).resolve()
PYTHON = "/usr/bin/python3"
WALLET = "kdewallet"
USER_UNIT = "kdewallet-desbloquear.service"


def run(*cmd, **kw):
    return sp.run(cmd, check=True, **kw)


def atomic(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        try:
            f.write(data)
            f.flush()
            os.fchmod(f.fileno(), mode)
            os.fsync(f.fileno())
            f.close()
            os.replace(f.name, path)
        finally:
            Path(f.name).unlink(missing_ok=True)


def wallet_dir():
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home()/".local/share")/"kwalletd"


def unlock(key, require_closed=False):
    import dbus
    if len(key) != 56:
        raise ValueError("Chave derivada inválida.")
    bus = dbus.SessionBus()
    bus.start_service_by_name("org.kde.secretservicecompat")
    obj = bus.get_object("org.kde.secretservicecompat", "/ksecretd", introspect=False)
    api = dbus.Interface(obj, "org.kde.KWallet")
    name = dbus.String(WALLET)
    if name not in api.wallets():
        raise RuntimeError("Carteira kdewallet ausente.")
    if require_closed and api.isOpen(name, signature="s"):
        raise RuntimeError("A carteira de teste já estava aberta.")
    api.pamOpen(name, dbus.ByteArray(key), dbus.Int32(0))
    for _ in range(40):
        if api.isOpen(name, signature="s"):
            return
        time.sleep(0.1)
    raise RuntimeError("O KDE recusou a chave da carteira.")


def validate_copy(key):
    # Nova sessão D-Bus e cópia privada em RAM: a carteira do desktop segue aberta.
    uid = os.getuid()
    runtime = Path(f"/run/user/{uid}")
    with tempfile.TemporaryDirectory(dir=runtime) as tmp:
        base = Path(tmp)
        env = {"HOME":tmp, "PATH":"/usr/bin:/bin", "QT_QPA_PLATFORM":"offscreen",
               "XDG_CURRENT_DESKTOP":"KDE", "USER":pwd.getpwuid(uid).pw_name}
        for var, sub in (("XDG_DATA_HOME","data"), ("XDG_CONFIG_HOME","config"),
                         ("XDG_CACHE_HOME","cache"), ("XDG_RUNTIME_DIR","runtime")):
            (base/sub).mkdir(mode=0o700)
            env[var] = str(base/sub)
        dest = base/"data/kwalletd"
        dest.mkdir(mode=0o700)
        for name in ("kdewallet.kwl", "kdewallet.salt"):
            shutil.copyfile(wallet_dir()/name, dest/name)
        (base/"config/kwalletrc").write_text("[Wallet]\nFirst Use=false\nLaunch Manager=false\n")
        run("dbus-run-session","--",PYTHON,"-I",str(SELF),"--test-copy",
            input=key,env=env,cwd=tmp,stdout=sp.DEVNULL,timeout=25)


def enroll():
    salt = (wallet_dir()/"kdewallet.salt").read_bytes()
    if len(salt) != 56:
        raise RuntimeError("Formato de carteira incompatível com o desbloqueio PAM do KDE.")
    # A senha não passa por argumentos, variáveis de ambiente ou arquivos.
    if sys.stdout.isatty():
        raise RuntimeError("Cadastro interno requer um canal privado.")
    with open("/dev/tty","w") as term:
        for _ in range(3):
            password = getpass.getpass("Senha ATUAL da carteira KDE: ",stream=term)
            if not password:
                raise RuntimeError("Cadastro cancelado.")
            key = hashlib.pbkdf2_hmac("sha512",password.encode(),salt,50000,56)
            del password
            try:
                validate_copy(key)
            except sp.CalledProcessError:
                print("A carteira de teste não abriu. Confira a senha.",file=term,flush=True)
                continue
            sys.stdout.buffer.write(key)
            return
    raise RuntimeError("Cadastro cancelado após três tentativas.")


def request_unlock(name):
    with socket.socket(socket.AF_UNIX) as conn:
        conn.settimeout(25)
        conn.connect(f"/run/kdewallet-{name}.sock")
        if conn.makefile("rb").readline() != b"OK\n":
            raise RuntimeError("O serviço não conseguiu abrir a carteira.")


def admin(force):
    uid = int(os.environ["SUDO_UID"])
    user = pwd.getpwuid(uid)
    name = user.pw_name
    if os.geteuid() != 0 or uid == 0 or not re.fullmatch(r"[a-z_][a-z0-9_-]*",name):
        raise RuntimeError("Execute o script como usuário comum em um terminal do KDE.")
    base = f"kdewallet-{name}"
    helper = Path(f"/usr/local/libexec/abrir-kdewallet-{name}.py")
    credential = Path("/etc/credstore.encrypted")/base
    units = Path("/etc/systemd/system")
    # Evita dois cadastros simultâneos.
    lock = os.open(f"/run/lock/{base}.lock",os.O_CREAT|os.O_RDWR,0o600)
    fcntl.flock(lock,fcntl.LOCK_EX)
    env = {"HOME":user.pw_dir,"PATH":"/usr/bin:/bin","USER":name,"LOGNAME":name}
    def as_user(mode, **kw):
        return run(PYTHON,"-I",str(helper),mode,user=uid,group=user.pw_gid,
                   extra_groups=os.getgrouplist(name,user.pw_gid),env=env,cwd=user.pw_dir,**kw)
    if not Path("/dev/tpmrm0").exists():
        raise RuntimeError("TPM 2.0 indisponível. Confira na BIOS se o TPM/fTPM está habilitado.")
    if not shutil.which("dbus-run-session"):
        raise RuntimeError("Instale dbus-daemon antes de continuar.")
    if not Path("/usr/bin/ksecretd").is_file():
        raise RuntimeError("Esta versão requer Plasma 6 com ksecretd.")
    if shutil.which("apt-get") and sp.run(["dpkg-query","-W","-f=${db:Status-Status}","libtss2-rc0t64"],
                    capture_output=True).stdout != b"installed":
        run("apt-get","-y","--no-install-recommends","install","libtss2-rc0t64")
    run("systemd-creds","has-tpm2")
    atomic(helper,SELF.read_bytes(),0o755)
    # O modo padrão do helper também atende à unidade instalada anteriormente.
    usable = False
    if credential.exists() and not force:
        existing = sp.run(["systemd-creds","decrypt",f"--name={base}",str(credential),"-"],stdout=sp.PIPE)
        if existing.returncode == 0:
            try:
                as_user("--validate",input=existing.stdout)
                usable = True
            except sp.CalledProcessError:
                print("A chave anterior não abre mais esta carteira.")
    if not usable:
        key = as_user("--enroll",stdout=sp.PIPE).stdout
        if len(key)!=56:
            raise RuntimeError("Falha na derivação da chave.")
        blob = run("systemd-creds","encrypt",f"--name={base}","--with-key=host+tpm2",
                   "--tpm2-device=/dev/tpmrm0","--tpm2-pcrs=","-","-",input=key,stdout=sp.PIPE).stdout
        if run("systemd-creds","decrypt",f"--name={base}","-","-",input=blob,stdout=sp.PIPE).stdout != key:
            raise RuntimeError("O TPM não recuperou a chave cadastrada.")
        credential.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        atomic(credential,blob,0o600)
    atomic(units/(base+".socket"),f"""[Unit]
Description=Desbloqueio local da carteira KDE
[Socket]
ListenStream=/run/{base}.sock
SocketUser={name}
SocketMode=0600
Accept=yes
[Install]
WantedBy=sockets.target
""".encode(),0o644)
    atomic(units/(base+"@.service"),f"""[Unit]
Description=Abrir carteira KDE com chave protegida pelo TPM
[Service]
User={name}
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus
Environment=XDG_RUNTIME_DIR=/run/user/{uid}
LoadCredentialEncrypted={base}:{credential}
ExecStart={PYTHON} -I {helper}
StandardInput=socket
StandardOutput=socket
StandardError=journal
NoNewPrivileges=yes
LimitCORE=0
RuntimeMaxSec=25
""".encode(),0o644)
    run("systemctl","daemon-reload")
    run("systemctl","enable","--now",base+".socket")


def desktop(force, repair):
    uid = os.getuid()
    if uid == 0 or not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        raise RuntimeError("Rode em um terminal da sessão KDE com seu usuário, sem sudo.")
    name = pwd.getpwuid(uid).pw_name
    base = f"kdewallet-{name}"
    def ok(*cmd):
        return sp.run(cmd,stdout=sp.DEVNULL,stderr=sp.DEVNULL).returncode == 0
    if not force and not repair and ok("systemctl","is-enabled",base+".socket") and ok("systemctl","--user","is-enabled",USER_UNIT):
        try:
            request_unlock(name)
        except (OSError,RuntimeError):
            print("O cadastro anterior não funcionou. Iniciando a recuperação.",flush=True)
        else:
            print("O desbloqueio automático já está configurado e funcionando.")
            return
    if not all((wallet_dir()/f).is_file() for f in ("kdewallet.kwl","kdewallet.salt")):
        raise RuntimeError("Carteira kdewallet compatível não encontrada.")
    if wallet_dir() != Path.home()/".local/share/kwalletd":
        raise RuntimeError("Este instalador requer o diretório padrão da carteira.")
    args = ["--force-admin"] if force else ["--admin"]
    run("sudo","--",PYTHON,"-I",str(SELF),*args)
    helper=f"/usr/local/libexec/abrir-kdewallet-{name}.py"
    unit_dir = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home()/".config")/"systemd/user"
    atomic(unit_dir/USER_UNIT,f"""[Unit]
Description=Desbloquear a carteira KDE no login automático
After=plasma-kwin_wayland.service plasma-kwallet-pam.service
Before=plasma-workspace.target plasma-restoresession.service xdg-desktop-autostart.target
PartOf=graphical-session.target
[Service]
Type=oneshot
ExecStart={PYTHON} -I {helper} --request
TimeoutStartSec=28
[Install]
WantedBy=plasma-workspace.target
""".encode(),0o644)
    run("systemctl","--user","daemon-reload")
    run("systemctl","--user","enable",USER_UNIT)
    run("systemctl","--user","restart",USER_UNIT)
    print("Pronto. O KDE abrirá a carteira no login automático.")


def main():
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    os.umask(0o077)
    arg = sys.argv[1:]
    if arg==["--test-copy"]: return unlock(sys.stdin.buffer.read(57),require_closed=True)
    if arg==["--validate"]: return validate_copy(sys.stdin.buffer.read(57))
    if arg==["--enroll"]: return enroll()
    if arg==["--request"]: return request_unlock(pwd.getpwuid(os.getuid()).pw_name)
    if arg in (["--admin"],["--force-admin"]): return admin(arg==["--force-admin"])
    if not arg and "CREDENTIALS_DIRECTORY" in os.environ and socket_is_stdin():
        name=pwd.getpwuid(os.getuid()).pw_name
        unlock((Path(os.environ["CREDENTIALS_DIRECTORY"])/f"kdewallet-{name}").read_bytes())
        print("OK",flush=True)
        return
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recadastrar",action="store_true",help="pedir a senha atual e substituir o cadastro")
    parser.add_argument("--reparar",action="store_true",help="reinstalar a integração, reaproveitando a chave se válida")
    args=parser.parse_args()
    desktop(args.recadastrar,args.reparar)


def socket_is_stdin():
    import stat
    return stat.S_ISSOCK(os.fstat(0).st_mode)


if __name__=="__main__":
    try:
        main()
    except (KeyboardInterrupt,EOFError):
        sys.exit("Cancelado.")
    except Exception as exc:
        sys.exit(f"Erro: {exc}")
