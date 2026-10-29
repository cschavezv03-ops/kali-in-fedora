#!/usr/bin/env bash
# Kali CLI on Fedora 44. Run as your regular Fedora user, never with sudo.
set -Eeuo pipefail
umask 077

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
LAB="$ROOT/lab"
STATE="$ROOT/.state"
LOCK="$ROOT/image.lock"
NAME=kali
TAG=docker.io/kalilinux/kali-rolling:latest
LABEL=io.github.kali-in-fedora.managed
RESERVE=$((20 * 1024 * 1024 * 1024))
MINIMUM=(ca-certificates curl iproute2 bind9-dnsutils)
CAPS=(CHOWN DAC_OVERRIDE FOWNER FSETID SETGID SETUID SETFCAP SYS_CHROOT)

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
note() { printf '\n%s\n' "$*"; }
run() { printf '+'; printf ' %q' "$@"; printf '\n'; "$@"; }
pm() { podman --remote=false "$@"; }
trap 'printf "ERROR: operación interrumpida en línea %s; no se elimina ningún contenedor.\n" "$LINENO" >&2' ERR

help_text() {
    cat <<'EOF'
Uso: ./kali.sh COMANDO
  inspect              Inspección sin instalar ni crear contenedores
  setup                Crear/reutilizar kali e instalar solo el mínimo
  validate             Comprobar seguridad, HTTPS y persistencia; reinicia kali
  start | stop         Iniciar o detener kali
  shell                Entrar a Bash; salir no elimina ni detiene kali
  status | logs        Estado reducido o últimos 100 registros del proceso principal
  inventory            Guardar versiones instaladas en .state/packages.tsv
  plan [PERFIL]        Actualizar índices y estimar SIN instalar (default: cli)
  remove               Eliminar solo kali detenido, con confirmación interactiva
  help                 Esta ayuda

Perfiles de plan: cli, headless, everything, everything-full.
everything-full incluye recomendaciones APT. Ninguno se instala automáticamente.
Solo setup instala paquetes, únicamente los mínimos documentados.
No se instala Podman ni se modifica la configuración de Fedora automáticamente.
EOF
}

prerequisites() {
    (( EUID != 0 )) || die 'Ejecuta como usuario normal, sin sudo.'
    local bin
    for bin in python3 podman realpath stat getenforce; do
        command -v "$bin" >/dev/null || die "Falta $bin. Consulta los requisitos del README."
    done
    [[ -r /etc/os-release ]] || die 'No se encuentra /etc/os-release.'
    [[ $(python3 -c 'import platform; d=platform.freedesktop_os_release(); print(d.get("ID"),d.get("VERSION_ID"))') == 'fedora 44' ]] || die 'Este script requiere Fedora 44.'
    [[ "$ROOT" != *:* && "$ROOT" != *$'\n'* && "$ROOT" != *$'\r'* ]] || die 'La ruta del proyecto contiene caracteres incompatibles con los volúmenes.'
    local var
    for var in CONTAINER_HOST CONTAINER_CONNECTION CONTAINERS_CONF CONTAINERS_STORAGE_CONF CONTAINERS_REGISTRIES_CONF PODMAN_USERNS; do
        [[ -z ${!var:-} ]] || die "La variable $var puede cambiar el entorno; revísala antes de continuar."
    done
    pm info --format json | python3 -c '
import json,sys
s=json.load(sys.stdin)["host"]["security"]
if not all(s.get(k) is True for k in ("rootless","seccompEnabled","selinuxEnabled")):
 sys.exit("Se requiere Podman local rootless, seccomp y soporte SELinux habilitados.")
'
    [[ $(getenforce) == Enforcing ]] || die 'Se requiere SELinux Enforcing; el script no cambia su configuración.'
}

private_dir() {
    local path=$1
    [[ ! -L "$path" ]] || die 'Se rechaza un directorio de trabajo que sea enlace simbólico.'
    if [[ -e "$path" ]]; then
        [[ -d "$path" && $(stat -c %u "$path") == "$EUID" && $(stat -c %a "$path") == 700 ]] ||
            die 'El directorio existente debe pertenecer al usuario y tener modo 0700; no se cambian sus permisos automáticamente.'
    else
        mkdir -m 700 -- "$path"
    fi
}

container_exists() {
    local rc=0
    pm container exists "$NAME" || rc=$?
    case $rc in 0) return 0;; 1) return 1;; *) die 'No se pudo consultar si existe kali.';; esac
}

space() {
    pm info --format '{{.Store.GraphRoot}}' | python3 -c '
import pathlib, shutil, sys
p=pathlib.Path(sys.stdin.read().strip())
while not p.exists(): p=p.parent
u=shutil.disk_usage(p)
print(u.free)
'
}

check_space() {
    local needed=${1:-0} free
    free=$(space)
    python3 -c 'import sys; f,n,r=map(int,sys.argv[1:]); print(f"Libre en almacenamiento Podman: {f/2**30:.2f} GiB; reserva Fedora: {r/2**30:.0f} GiB; presupuesto adicional: {n/2**30:.2f} GiB"); sys.exit(0 if f-n>=r else 1)' "$free" "$needed" "$RESERVE" ||
        die 'No queda la reserva de 20 GiB después del presupuesto estimado.'
}

inspect_host() {
    note 'Etapa 1: inspección (no instala paquetes ni crea contenedores)'
    python3 - <<'PY'
import os, pathlib, platform, pwd, shutil, subprocess
d=dict(l.split('=',1) for l in pathlib.Path('/etc/os-release').read_text().splitlines() if '=' in l)
print('Sistema:', d.get('PRETTY_NAME','').strip('"')); print('Kernel:',platform.release()); print('Arquitectura:',platform.machine())
print('Usuario sin privilegios:', os.geteuid()!=0)
u=shutil.disk_usage('.');print(f'Disco del proyecto: {u.free/2**30:.2f} GiB libres')
user=pwd.getpwuid(os.getuid()).pw_name
for name in ('subuid','subgid'):
 try:
  found=any(l.split(':')[0] in (user,str(os.getuid())) and int(l.strip().split(':')[2])>=65536 for l in pathlib.Path('/etc/'+name).read_text().splitlines())
  print(name+': rango de al menos 65536 IDs:',found)
 except (OSError,ValueError,IndexError): print(name+': no verificable')
for args in (['podman','--version'],['getenforce'],['systemctl','is-active','firewalld']):
 if shutil.which(args[0]):
  r=subprocess.run(args,capture_output=True,text=True); print(' '.join(args)+':',r.stdout.strip() or 'no verificable', '(exit='+str(r.returncode)+')')
for key in ('CONTAINER_HOST','CONTAINER_CONNECTION','CONTAINERS_CONF','CONTAINERS_STORAGE_CONF','CONTAINERS_REGISTRIES_CONF','PODMAN_USERNS'):
 if os.environ.get(key): print('Override a revisar (valor omitido):',key)
for base in (pathlib.Path('/etc/containers'),pathlib.Path.home()/'.config/containers'):
 for name in ('containers.conf','storage.conf','registries.conf','mounts.conf'):
  print(('Sistema' if str(base).startswith('/etc') else 'Usuario')+'/'+name+':', (base/name).exists())
PY
    if command -v podman >/dev/null; then
        pm info --format json | python3 -c '
import json,sys
d=json.load(sys.stdin); h=d["host"]; s=d["store"]
print("Podman:", {"rootless": h["security"]["rootless"], "SELinux": h["security"]["selinuxEnabled"], "seccomp":h["security"]["seccompEnabled"], "red":h["networkBackend"], "red_rootless":h.get("rootlessNetworkCmd"), "storage":s["graphDriverName"]})
'
        if container_exists; then
            pm inspect "$NAME" | python3 -c '
import json,sys
d=json.load(sys.stdin)[0];h=d["HostConfig"]
print("kali existe; no se sobrescribe:", {"estado":d["State"]["Status"],"imagen":d.get("ImageName"),"privileged":h.get("Privileged"),"red":h.get("NetworkMode"),"montajes":len(d.get("Mounts",[])),"publicaciones":len(h.get("PortBindings") or {})})
'
        else
            printf 'No existe un contenedor llamado kali.\n'
        fi
    else
        printf 'Podman no instalado. Propuesta manual: sudo dnf --repo=fedora --repo=updates install podman passt shadow-utils fuse-overlayfs\n'
    fi
}

audit() {
    pm inspect "$NAME" | python3 -c '
import json,os,sys
d=json.load(sys.stdin)[0];h=d["HostConfig"];c=d["Config"]; lab=sys.argv[1]
mounts=d.get("Mounts",[]); allowed={"CAP_CHOWN","CAP_DAC_OVERRIDE","CAP_FOWNER","CAP_FSETID","CAP_SETGID","CAP_SETUID","CAP_SETFCAP","CAP_SYS_CHROOT"}
caps=set(d.get("EffectiveCaps",[]))
checks={
 "propiedad_proyecto":c.get("Labels",{}).get("io.github.kali-in-fedora.managed")=="1",
 "imagen_oficial":d.get("ImageName", "").startswith("docker.io/kalilinux/kali-rolling@sha256:"),
 "sin_privileged":h.get("Privileged") is False,
 "persistente":h.get("AutoRemove") is False,
 "red_pasta":h.get("NetworkMode","").split(":")[0]=="pasta",
 "sin_puertos":not h.get("PortBindings"),
 "sin_dispositivos_extra":not h.get("Devices") and not h.get("DeviceRequests"),
 "PID_privado":h.get("PidMode") not in ("host",),
 "IPC_privado":h.get("IpcMode") not in ("host",),
 "no_new_privileges":"no-new-privileges" in h.get("SecurityOpt",[]),
 "capacidades_limitadas":bool(caps) and caps<=allowed,
 "solo_lab":len(mounts)==1 and mounts[0].get("Type")=="bind" and mounts[0].get("Source")==lab and mounts[0].get("Destination")=="/lab" and mounts[0].get("RW") is True,
 "sin_desactivar_SELinux":not any(x.startswith("label=disable") or x.startswith("label:disable") for x in h.get("SecurityOpt",[])),
 "sin_desactivar_seccomp":not any("seccomp=unconfined" in x or "seccomp:unconfined" in x for x in h.get("SecurityOpt",[])),
 "lab_privado":not os.path.islink(lab) and os.stat(lab).st_uid==os.getuid() and os.stat(lab).st_mode & 0o777==0o700,
}
for k,v in checks.items(): print(("OK " if v else "FALLO ")+k)
if not all(checks.values()): sys.exit("Contenedor incompatible: no se reutiliza ni modifica.")
' "$LAB"
}

require_managed() {
    container_exists || die 'No existe kali. Ejecuta primero ./kali.sh setup.'
    audit
}

start_container() {
    if [[ $(pm inspect --format '{{.State.Status}}' "$NAME") != running ]]; then
        run pm start "$NAME"
    fi
}

prepare_image() {
    if [[ -e "$LOCK" || -L "$LOCK" ]]; then
        [[ -f "$LOCK" && ! -L "$LOCK" ]] || die 'image.lock debe ser un archivo regular.'
        IMAGE=$(cat -- "$LOCK")
        [[ "$IMAGE" =~ ^docker\.io/kalilinux/kali-rolling@sha256:[a-f0-9]{64}$ ]] || die 'image.lock no contiene un digest oficial válido.'
        run pm pull --policy=missing "$IMAGE"
    else
        run pm pull "$TAG"
        IMAGE=$(pm image inspect "$TAG" --format '{{index .RepoDigests 0}}')
        [[ "$IMAGE" =~ ^docker\.io/kalilinux/kali-rolling@sha256:[a-f0-9]{64}$ ]] || die 'No se obtuvo un digest de la imagen oficial.'
        (set -C; printf '%s\n' "$IMAGE" > "$LOCK")
    fi
    printf 'Base fijada: %s\n' "$IMAGE"
}

apt_update() {
    run pm exec --env LC_ALL=C --env DEBIAN_FRONTEND=noninteractive "$NAME" \
        apt-get -o APT::Update::Error-Mode=any update
}

apt_plan() {
    local profile=$1 rc=0
    shift
    local raw="$STATE/plan-$profile.txt"
    local recommend_flag=--no-install-recommends recommends=false
    if [[ "$profile" == everything-full ]]; then
        recommend_flag=--install-recommends
        recommends=true
    fi
    pm exec --env LC_ALL=C --env DEBIAN_FRONTEND=noninteractive "$NAME" \
        apt-get --assume-no --no-remove "$recommend_flag" install "$@" > "$raw" 2>&1 || rc=$?
    [[ $rc == 0 || $rc == 1 ]] || { cat -- "$raw"; die 'APT no pudo resolver este perfil; no se instala nada.'; }
    python3 -c '
import json,pathlib,re,sys
text=pathlib.Path(sys.argv[1]).read_text()
def amount(s):
 s=s.replace(",","");m=re.fullmatch(r"([0-9.]+)\s*([kMGT]?B)",s.strip())
 if not m: raise ValueError("Tamaño APT no reconocido: "+s)
 return int(float(m[1])*{"B":1,"kB":10**3,"MB":10**6,"GB":10**9,"TB":10**12}[m[2]])
download=re.search(r"Need to get (.+?) of archives",text)
installed=re.search(r"After this operation, ([0-9.,]+\s*[kMGT]?B) of additional disk space will be used",text)
unchanged=re.search(r"^0 upgraded, 0 newly installed, 0 to remove",text,re.M) is not None
if not installed and not unchanged:
 print(text);sys.exit("No se pudo obtener una estimación fiable de APT; se detiene la operación.")
d=amount(download[1].split("/")[-1]) if download else 0
n=amount(installed[1]) if installed else 0
budget=n+d+2*2**30
result={"download_bytes":d,"additional_installed_bytes":n,"budget_bytes":budget,"reserve_bytes":20*2**30,"recommends":sys.argv[3]=="true"}
pathlib.Path(sys.argv[2]).write_text(json.dumps(result,indent=2)+"\n")
print(f"APT: descarga {d/2**30:.2f} GiB; instalado adicional {n/2**30:.2f} GiB; presupuesto con descarga y margen de 2 GiB: {budget/2**30:.2f} GiB")
for l in text.splitlines():
 if "newly installed" in l or "upgraded," in l: print(l)
print("Dependencias completas en el informe local (no versionado).")
' "$raw" "$STATE/plan-$profile.json" "$recommends"
}

inventory() {
    private_dir "$STATE"
    pm exec "$NAME" dpkg-query -W '-f=${binary:Package}\t${Version}\t${Architecture}\n' > "$STATE/packages.tsv"
    printf 'Inventario local: .state/packages.tsv\n'
}

validate() {
    note 'Etapa 5: validación no destructiva; detiene y vuelve a iniciar kali'
    require_managed
    start_container
    pm exec "$NAME" bash -ceu '
        . /etc/os-release
        test "$ID" = kali
        printf "Kali: %s\n" "$PRETTY_NAME"
        apt-get --version | head -n 1
        test -z "$(dpkg --audit)"
        for p in ca-certificates curl iproute2 bind9-dnsutils; do
            test "$(dpkg-query -W -f="\${Status}" "$p")" = "install ok installed"
        done
        curl --fail --silent --show-error --location --connect-timeout 15 --max-time 60 --output /dev/null https://www.kali.org/
        printf "HTTPS externo y resolución DNS: OK\n"
        ip -brief link show lo
        test -n "$(dig +short www.kali.org A)"
        test "$(id -u)" = 0
        grep -q "^NoNewPrivs:[[:space:]]*1" /proc/self/status
        grep -q "^Seccomp:[[:space:]]*2" /proc/self/status
        printf "UID 0 interno; seccomp y NoNewPrivs: OK\n"
    '
    # Read from the host namespace: inside a nested user namespace the parent
    # mapping may legitimately display 0 -> 0, despite being rootless on Fedora.
    local pid
    pid=$(pm inspect --format '{{.State.Pid}}' "$NAME")
    python3 -c '
import os,pathlib,sys
rows=[tuple(map(int,l.split())) for l in (pathlib.Path("/proc")/sys.argv[1]/"uid_map").read_text().splitlines()]
if not any(inside==0 and outside==os.getuid()!=0 and size>=1 for inside,outside,size in rows):
 sys.exit("El UID 0 del contenedor no está mapeado al usuario Fedora esperado.")
print("Mapeo verificado desde Fedora: UID 0 interno corresponde al usuario Fedora sin privilegios.")
' "$pid"
    local token inside shared
    token=$(python3 -c 'import secrets;print(secrets.token_hex(16))')
    inside="/var/tmp/kali-in-fedora-$token"
    shared=".persistence-$token"
    pm exec "$NAME" bash -ceu 'umask 077; printf "%s" "$1" > "$2"; printf "%s" "$1" > "/lab/$3"' bash "$token" "$inside" "$shared"
    [[ $(cat -- "$LAB/$shared") == "$token" && $(stat -c %u "$LAB/$shared") == "$EUID" && $(stat -c %a "$LAB/$shared") == 600 ]] || die 'El intercambio de archivos no conserva permisos y propietario esperados.'
    run pm stop --time 10 "$NAME"
    run pm start "$NAME"
    pm exec "$NAME" bash -ceu 'test "$(cat -- "$2")" = "$1"; test "$(cat -- "/lab/$3")" = "$1"; for p in ca-certificates curl iproute2 bind9-dnsutils; do dpkg-query -W "$p"; done' bash "$token" "$inside" "$shared"
    # Only files created by this validation are removed, inside the container.
    pm exec "$NAME" bash -ceu 'test "$(cat -- "$2")" = "$1"; test "$(cat -- "/lab/$3")" = "$1"; unlink -- "$2"; unlink -- "/lab/$3"' bash "$token" "$inside" "$shared"
    inventory
    note 'Validado: HTTPS/DNS, paquetes, diagnóstico loopback, UID rootless, seccomp, permisos y persistencia tras stop/start.'
    printf 'No probado: reinicio de Fedora, herramientas amplias, interfaz gráfica, Wi-Fi, GPU ni hardware.\n'
    check_space
}

setup() {
    note 'Etapa 2: plan mínimo (ningún metapaquete amplio se instalará)'
    printf 'Base oficial fijada por digest; kali persistente; solo lab/:Z; pasta; sin puertos; reserva de 20 GiB.\n'
    note 'Etapa 3: verificar Podman rootless existente'
    prerequisites
    check_space $((2 * 1024 * 1024 * 1024))
    if container_exists; then
        require_managed
    else
        private_dir "$LAB"
        private_dir "$STATE"
        prepare_image
        local cap args=()
        for cap in "${CAPS[@]}"; do args+=(--cap-add="$cap"); done
        note 'Etapa 4: crear kali persistente'
        run pm create --name "$NAME" --label "$LABEL=1" \
            --hostname kali --userns=keep-id:uid=0,gid=0 --user=0:0 \
            --network=pasta:--no-map-gw --pid=private --ipc=private --uts=private --cgroupns=private \
            --cap-drop=all "${args[@]}" --security-opt=no-new-privileges \
            --pids-limit=512 --restart=no --stop-timeout=10 \
            --log-driver=k8s-file --log-opt=max-size=10mb \
            --volume "$LAB:/lab:rw,Z" --workdir=/lab \
            "$IMAGE" /usr/bin/sleep infinity
        audit
    fi
    private_dir "$STATE"
    start_container
    apt_update
    apt_plan minimum "${MINIMUM[@]}"
    local budget
    budget=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["budget_bytes"])' "$STATE/plan-minimum.json")
    check_space "$budget"
    note 'Etapa 6: instalar únicamente el mínimo'
    run pm exec --env LC_ALL=C --env DEBIAN_FRONTEND=noninteractive "$NAME" \
        apt-get --yes --no-remove --no-install-recommends install "${MINIMUM[@]}"
    validate
}

plan() {
    local profile=${1:-cli} packages=()
    case "$profile" in
        cli) packages=(kali-tools-information-gathering kali-tools-web kali-tools-sniffing-spoofing kali-tools-forensics kali-tools-crypto-stego kali-tools-reverse-engineering);;
        headless) packages=(kali-linux-headless);;
        everything) packages=(kali-linux-everything);;
        everything-full) packages=(kali-linux-everything);;
        *) die 'Perfil desconocido; usa cli, headless, everything o everything-full.';;
    esac
    require_managed
    private_dir "$STATE"
    start_container
    apt_update
    note "Propuesta $profile; NO instala paquetes"
    printf 'Metapaquetes:'; printf ' %s' "${packages[@]}"; printf '\n'
    apt_plan "$profile" "${packages[@]}"
    local budget
    budget=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["budget_bytes"])' "$STATE/plan-$profile.json")
    check_space "$budget"
    printf 'Esta comprobación no garantiza compatibilidad de todas las herramientas. Instalación amplia pendiente de decisión explícita.\n'
}

remove_container() {
    require_managed
    [[ -t 0 ]] || die 'La eliminación exige confirmación en una terminal interactiva.'
    [[ $(pm inspect --format '{{.State.Status}}' "$NAME") != running ]] || die 'Detén el contenedor antes; revisa y respalda los datos.'
    local cid answer
    cid=$(pm inspect --format '{{.Id}}' "$NAME")
    note 'Se perderán TODOS los paquetes y archivos de la capa interna, incluidos /root y /var/tmp.'
    printf 'Se conserva lab/, .state/ y la imagen. Los backups deben realizarse antes.\n'
    pm diff "$cid" | python3 -c 'import sys;print("Entradas de cambios en la capa interna:",sum(1 for _ in sys.stdin))'
    printf 'Para confirmar, escribe exactamente ELIMINAR kali %s: ' "$cid"
    IFS= read -r answer
    [[ "$answer" == "ELIMINAR kali $cid" ]] || die 'Confirmación no válida; no se elimina nada.'
    run pm rm "$cid"
}

command=${1:-help}
case "$command" in
    help|-h|--help) help_text; exit 0;;
    inspect) inspect_host; exit 0;;
esac
prerequisites
case "$command" in
    setup) private_dir "$STATE"; setup 2>&1 | tee "$STATE/setup-$(date -u +%Y%m%dT%H%M%SZ).log";;
    validate) private_dir "$STATE"; validate 2>&1 | tee "$STATE/validate-$(date -u +%Y%m%dT%H%M%SZ).log";;
    start) require_managed; start_container;;
    stop) require_managed; run pm stop --time 10 "$NAME";;
    shell) require_managed; start_container; pm exec -it --workdir /lab "$NAME" bash -c 'umask 077; exec bash -i';;
    status) require_managed; pm inspect --format 'Estado={{.State.Status}} Imagen={{.ImageName}} ID={{.Id}}' "$NAME"; check_space;;
    logs) require_managed; pm logs --tail 100 "$NAME";;
    inventory) require_managed; start_container; inventory;;
    plan) plan "${2:-cli}";;
    remove) remove_container;;
    *) help_text; die 'Comando desconocido.';;
esac
