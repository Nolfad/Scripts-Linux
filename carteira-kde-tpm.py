#!/usr/bin/python3 -I
"""Desbloqueia kdewallet no login automático do KDE Plasma em sistemas baseados em Debian.
Execute como seu usuário, em um terminal da sessão KDE. A instalação pede autorização administrativa.
"""
import argparse
import ctypes.util
import fcntl
import getpass
import hashlib
import os
from pathlib import Path
import pwd
import re
import resource
import shutil
import select
import signal
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


ENDPOINTS = (
    ("org.kde.secretservicecompat", "/ksecretd"),
    ("org.kde.kwalletd6", "/modules/kwalletd6"),
    ("org.kde.kwalletd5", "/modules/kwalletd5"),
)


def unlock(key, require_closed=False, create=False):
    import dbus
    if len(key) != 56:
        raise ValueError("Chave derivada inválida.")
    bus = dbus.SessionBus()
    names = set(bus.list_activatable_names()) | set(bus.list_names())
    for service, path in ENDPOINTS:
        if service in names:
            bus.start_service_by_name(service)
            obj = bus.get_object(service, path, introspect=False)
            api = dbus.Interface(obj, "org.kde.KWallet")
            break
    else:
        raise RuntimeError("O barramento da sessão não oferece KWallet compatível.")
    pidfd = None
    if require_closed:
        owner = bus.get_name_owner(service)
        pid = int(bus.call_blocking("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                    "org.freedesktop.DBus", "GetConnectionUnixProcessID", "s", (owner,)))
        # Só encerra o daemon criado para esta cópia isolada.
        daemon_env = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
        if os.fsencode("HOME=" + os.environ["HOME"]) not in daemon_env or Path.home() == Path(pwd.getpwuid(os.getuid()).pw_dir):
            raise RuntimeError("Daemon de teste fora do diretório isolado.")
        pidfd = os.pidfd_open(pid)
    try:
        open_on_bus(api, key, require_closed, create)
    finally:
        if pidfd is not None:
            try:
                signal.pidfd_send_signal(pidfd, signal.SIGTERM)
                poller = select.poll()
                poller.register(pidfd, select.POLLIN)
                if not poller.poll(2000):
                    signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                    poller.poll(2000)
            except ProcessLookupError:
                pass
            finally:
                os.close(pidfd)


def open_on_bus(api, key, require_closed, create):
    import dbus
    name = dbus.String(WALLET)
    if not create and name not in api.wallets():
        raise RuntimeError("Carteira kdewallet ausente.")
    if require_closed and api.isOpen(name, signature="s"):
        raise RuntimeError("A carteira de teste já estava aberta.")
    api.pamOpen(name, dbus.ByteArray(key), dbus.Int32(0), signature='sayi')
    for _ in range(40):
        if api.isOpen(name, signature="s"):
            if require_closed and api.close(name, True, signature="sb") != 0:
                raise RuntimeError("O KDE não concluiu a gravação da carteira de teste.")
            return
        time.sleep(0.1)
    raise RuntimeError("O KDE recusou a chave da carteira.")


def isolated_wallet(directory, key, create=False):
    """Abre apenas a carteira de teste em um D-Bus isolado."""
    base = directory.parent.parent
    env = {"HOME":str(base), "PATH":"/usr/bin:/bin", "QT_QPA_PLATFORM":"offscreen",
           "LC_ALL":"C.UTF-8", "XDG_CURRENT_DESKTOP":"KDE", "USER":pwd.getpwuid(os.getuid()).pw_name}
    for var, sub in (("XDG_DATA_HOME","data"), ("XDG_CONFIG_HOME","config"),
                     ("XDG_CACHE_HOME","cache"), ("XDG_RUNTIME_DIR","runtime")):
        (base/sub).mkdir(mode=0o700, exist_ok=True)
        env[var] = str(base/sub)
    (base/"config/kwalletrc").write_text("[Wallet]\nFirst Use=false\nLaunch Manager=false\n")
    run("dbus-run-session","--",PYTHON,"-I",str(SELF),"--create-copy" if create else "--test-copy",
        input=key,env=env,cwd=base,stdout=sp.DEVNULL,timeout=30)


WALLET_FILES = ("kdewallet.kwl", "kdewallet.salt")


def validate_copy(key):
    with tempfile.TemporaryDirectory(dir=f"/run/user/{os.getuid()}") as tmp:
        dest = Path(tmp)/"data/kwalletd"
        dest.mkdir(mode=0o700,parents=True)
        for filename in WALLET_FILES:
            shutil.copyfile(wallet_dir()/filename, dest/filename)
        isolated_wallet(dest, key)


def initialize_wallet(salt, key):
    """Só publica arquivos novos após fechar e reabrir a carteira de teste."""
    folder = wallet_dir()
    folder.mkdir(mode=0o700,parents=True,exist_ok=True)
    # Mesmo filesystem da carteira, para publicar sem sobrescrever nenhum arquivo.
    with tempfile.TemporaryDirectory(dir=folder) as tmp:
        dest = Path(tmp)/"data/kwalletd"
        dest.mkdir(mode=0o700,parents=True)
        (dest/WALLET_FILES[1]).write_bytes(salt)
        isolated_wallet(dest, key, create=True)
        isolated_wallet(dest, key)
        # Se outro processo criou a carteira entretanto, interromper sem substituí-la.
        published = []
        try:
            for filename in reversed(WALLET_FILES):
                os.link(dest/filename, folder/filename)
                published.append(filename)
        except BaseException:
            for filename in published:
                if (folder/filename).samefile(dest/filename):
                    (folder/filename).unlink()
            raise


def enroll():
    folder = wallet_dir()
    present = [os.path.lexists(folder/name) for name in WALLET_FILES]
    if any(present) and not all(present):
        raise RuntimeError("Carteira incompleta ou sem salt compatível. Verifique no KWalletManager.")
    new = not any(present)
    if new and any(folder.glob("*.kwl")):
        raise RuntimeError("Existe outra carteira. Selecione ou restaure kdewallet antes do cadastro.")
    salt = os.urandom(56) if new else (folder/WALLET_FILES[1]).read_bytes()
    if len(salt) != 56:
        raise RuntimeError("Formato de carteira incompatível com desbloqueio PAM do KDE.")
    if sys.stdout.isatty():
        raise RuntimeError("Cadastro interno requer um canal privado.")
    with open("/dev/tty","w") as term:
        for _ in range(3):
            prompt = "Escolha a senha da NOVA carteira KDE: " if new else "Senha ATUAL da carteira KDE: "
            password = getpass.getpass(prompt, stream=term)
            if not password:
                raise RuntimeError("Cadastro cancelado.")
            if new and getpass.getpass("Repita a nova senha: ", stream=term) != password:
                print("As senhas diferem.", file=term, flush=True)
                continue
            key = hashlib.pbkdf2_hmac("sha512",password.encode(),salt,50000,56)
            del password
            if new:
                initialize_wallet(salt, key)
            else:
                try:
                    validate_copy(key)
                except sp.CalledProcessError:
                    print("A carteira de teste não abriu. Confira a senha e o formato da carteira.",file=term,flush=True)
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


def dependencies(plasma):
    if not shutil.which("apt-get") or not Path("/run/systemd/system").is_dir():
        raise RuntimeError("O instalador requer APT e systemd como gerenciador do sistema.")
    version = int(sp.check_output(["systemctl","--version"],text=True).split()[1])
    if version < 252:
        raise RuntimeError("É necessário systemd 252 ou mais recente para esta integração com TPM.")
    groups = []
    if not shutil.which("dbus-run-session"):
        groups.append(("dbus-daemon","dbus"))
    if sp.run([PYTHON,"-I","-c","import dbus"],capture_output=True).returncode:
        groups.append(("python3-dbus",))
    service_dir = Path("/usr/share/dbus-1/services")
    native = "org.kde.kwalletd5" if plasma == "5" else "org.kde.kwalletd6"
    if not (service_dir/(native+".service")).exists() and not (service_dir/"org.kde.secretservicecompat.service").exists():
        groups.append(("libkf5wallet-bin",) if plasma=="5" else ("kwallet6","libkf6wallet-bin"))
    for library, packages in (
        ("tss2-esys", ("libtss2-esys-3.0.2-0t64","libtss2-esys-3.0.2-0","libtss2-esys0")),
        ("tss2-rc", ("libtss2-rc0t64","libtss2-rc0")),
        ("tss2-mu", ("libtss2-mu-4.0.1-0t64","libtss2-mu0")),
        ("tss2-tcti-device", ("libtss2-tcti-device0t64","libtss2-tcti-device0")),
    ):
        if not ctypes.util.find_library(library):
            groups.append(packages)
    if groups:
        run("apt-get","update")
        chosen = []
        for group in groups:
            for package in group:
                policy = sp.check_output(["apt-cache","policy",package],env={**os.environ,"LC_ALL":"C"},text=True)
                if re.search(r"Candidate: (?!\(none\))\S+",policy):
                    chosen.append(package)
                    break
            else:
                raise RuntimeError("Nenhum pacote disponível nos repositórios para: "+", ".join(group))
        run("apt-get","--no-remove","--no-install-recommends","-y","install",*chosen)
    if not shutil.which("systemd-creds"):
        raise RuntimeError("O systemd desta distribuição não fornece systemd-creds.")
    run("systemd-creds","has-tpm2")


def admin(force, plasma):
    uid = int(os.environ.get("SUDO_UID") or os.environ.get("PKEXEC_UID") or 0)
    user = pwd.getpwuid(uid)
    name = user.pw_name
    if os.geteuid() != 0 or uid == 0 or not re.fullmatch(r"[a-z_][a-z0-9_-]*",name):
        raise RuntimeError("Execute o script como usuário comum em um terminal do KDE.")
    base = f"kdewallet-{name}"
    helper = Path(f"/usr/local/libexec/abrir-kdewallet-{name}.py")
    credential = Path("/etc/credstore.encrypted")/base
    units = Path("/etc/systemd/system")
    # Evita dois cadastros simultâneos.
    lock = os.open(f"/run/{base}.lock",os.O_CREAT|os.O_RDWR,0o600)
    fcntl.flock(lock,fcntl.LOCK_EX)
    env = {"HOME":user.pw_dir,"PATH":"/usr/bin:/bin","USER":name,"LOGNAME":name}
    def as_user(mode, **kw):
        return run(PYTHON,"-I",str(helper),mode,user=uid,group=user.pw_gid,
                   extra_groups=os.getgrouplist(name,user.pw_gid),env=env,cwd=user.pw_dir,**kw)
    if not Path("/dev/tpmrm0").exists():
        raise RuntimeError("TPM 2.0 indisponível. Confira na BIOS se o TPM/fTPM está habilitado.")
    dependencies(plasma)
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
    if not shutil.which("plasmashell") or not ok("systemctl","--user","is-active","plasma-workspace.target"):
        raise RuntimeError("Rode em uma sessão ativa do KDE Plasma gerenciada pelo systemd.")
    version = sp.check_output(["plasmashell","--version"],text=True)
    major = re.search(r"plasmashell\s+(\d+)\.",version)
    if not major or major[1] not in ("5","6"):
        raise RuntimeError("Versão do KDE Plasma ainda não suportada: "+version.strip())
    if shutil.which("sudo"):
        elevate = ["sudo", "--"]
    elif shutil.which("pkexec"):
        elevate = ["pkexec"]
    else:
        raise RuntimeError("Instale sudo ou polkit (pkexec) para autorizar a configuração.")
    if wallet_dir() != Path.home()/".local/share/kwalletd":
        raise RuntimeError("Este instalador requer o diretório padrão da carteira.")
    args = ["--force-admin"] if force else ["--admin"]
    run(*elevate,PYTHON,"-I",str(SELF),*args,major[1])
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
    if arg==["--create-copy"]: return unlock(sys.stdin.buffer.read(57),require_closed=True,create=True)
    if arg==["--test-copy"]: return unlock(sys.stdin.buffer.read(57),require_closed=True)
    if arg==["--validate"]: return validate_copy(sys.stdin.buffer.read(57))
    if arg==["--enroll"]: return enroll()
    if arg==["--request"]: return request_unlock(pwd.getpwuid(os.getuid()).pw_name)
    if len(arg)==2 and arg[0] in ("--admin","--force-admin") and arg[1] in ("5","6"):
        return admin(arg[0]=="--force-admin",arg[1])
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
