# Guía técnica y recuperación

Complementa el [README](../README.md) con requisitos, permisos,
reproducibilidad, propuestas de herramientas, respaldo y pruebas.

## Requisitos

- Fedora 44, usuario normal y Bash. **No ejecutar el script con `sudo`.**
- Podman local funcional en modo rootless, Python 3 y herramientas de coreutils.
- `newuidmap`/`newgidmap`, rangos de al menos 65536 IDs en `/etc/subuid` y
  `/etc/subgid`, y `pasta` (paquete Fedora `passt`).
- Conexión a Docker Hub, repositorios de Kali y `https://www.kali.org/`.
- Al menos 22 GiB libres en el almacenamiento de Podman para empezar: 20 GiB
  quedan como reserva para Fedora y 2 GiB son un presupuesto inicial conservador.
  Antes de instalar se comprueba también el presupuesto calculado con APT.
- SELinux y seccomp activos para conservar las protecciones normales de Fedora.

Si falta Podman, revisa primero la inspección. La propuesta de instalación usa
únicamente repositorios oficiales de Fedora y necesita autorización de administrador:

```bash
sudo dnf --repo=fedora --repo=updates install podman passt shadow-utils fuse-overlayfs
```

El script **no ejecuta este comando**, no reinstala paquetes de Fedora ni corrige
automáticamente rangos de usuarios, redes o políticas. Si faltan permisos o hay
un conflicto, se detiene. `slirp4netns` no es necesario para este proyecto.

## Imagen oficial y reproducibilidad

Fuente: `docker.io/kalilinux/kali-rolling:latest`, publicada por Kali y documentada
en [imágenes oficiales](https://www.kali.org/docs/containers/official-kalilinux-docker-images/)
y [uso con Podman](https://www.kali.org/docs/containers/using-kali-podman-images/).

`image.lock` contiene la referencia completa con digest SHA-256 verificada en la
descarga inicial. Se versiona para reutilizar la misma base al clonar el proyecto.
El script solo acepta ese repositorio oficial con un digest válido. No cambia el
lock ni actualiza la base cuando se repite `setup`. Si todavía no hay lock, lo
genera tras descargar la etiqueta oficial; revisa y versiona ese archivo.

El digest fija la base, **no congela Kali Rolling ni sus repositorios APT**.
Las dependencias resueltas pueden cambiar o dejar de estar disponibles. El
inventario `.state/packages.tsv` registra las versiones realmente instaladas,
pero no es un repositorio de paquetes ni garantiza reconstrucciones idénticas.
No se omiten verificaciones TLS ni firmas APT. Un digest verifica identidad del
contenido; no equivale por sí solo a una firma independiente del editor.

La imagen fijada debe ser compatible con la arquitectura de la máquina destino.
La validación inicial de este proyecto se realiza en x86_64; no se afirma haber
probado otras arquitecturas de Fedora 44.

No se necesita publicar imágenes grandes en GitHub. Se versionan script,
documentación, digest y un resumen de validación sin datos personales.
Capturas, evidencias, registros y archivos de laboratorio quedan fuera de Git.

## Aislamiento y permisos

- Podman local rootless. Se rechazan ejecución como administrador y variables
  que redirijan el motor o alteren su configuración desde el entorno.
- UID 0 **dentro** del contenedor para administrar APT. Con
  `--userns=keep-id:uid=0,gid=0`, ese UID corresponde al usuario normal de Fedora;
  no es root del anfitrión. Los archivos creados en `/lab` quedan a tu nombre.
- Red `pasta:--no-map-gw`, sin publicar puertos ni usar `--network=host`.
  Se mantiene salida a Internet, necesaria para APT. No es un cortafuegos de
  salida: el contenedor puede iniciar conexiones hacia destinos alcanzables,
  incluidos equipos de la LAN; úsalo solo con autorización.
- Espacios privados de PID, IPC, UTS y cgroup; límite de 512 procesos.
- Se eliminan todas las capacidades y solo se añaden `CHOWN`, `DAC_OVERRIDE`,
  `FOWNER`, `FSETID`, `SETGID`, `SETUID`, `SETFCAP` y `SYS_CHROOT`, para gestión de
  paquetes y permisos dentro del contenedor. Sin `NET_ADMIN` ni `NET_RAW`.
- `no-new-privileges`, seccomp predeterminado y SELinux conservados.
- Sin `--privileged`, dispositivos extra, sockets del motor ni montajes de
  credenciales, claves SSH o del directorio personal completo.
- Único montaje de usuario: `./lab` a `/lab`, lectura/escritura, permisos `0700`
  y `:Z`. Podman aplica el contexto SELinux privado únicamente a ese directorio.
  No se cambian recursivamente propietarios con `:U` ni permisos existentes.
- Sin inicio automático al arrancar Fedora; sin `--rm`. Salir de Bash deja el
  contenedor persistente. Los registros del proceso principal se limitan a 10 MB.

`lab` y `.state` deben ser directorios reales, pertenecer al usuario y tener modo
`0700`; el script rechaza enlaces simbólicos y directorios incompatibles. No
introduzcas credenciales ni archivos sensibles en `lab`, tampoco mediante enlaces
duros. Un contenedor comparte el kernel de Fedora: no ofrece el aislamiento de
una máquina virtual. No es un entorno para ejecutar malware desconocido.

No se cambia SELinux, firewalld, GRUB, kernel, controladores, NetworkManager ni
repositorios del anfitrión. Los repositorios APT de Kali están dentro de Kali.

## Componentes mínimos y validación

Se solicitan únicamente `ca-certificates`, `curl`, `iproute2` y `bind9-dnsutils`, junto
con sus dependencias obligatorias, usando `--no-install-recommends` y
`--no-remove`. La base ya incluye Bash y APT. No se instala un escritorio, SSH
server ni un metapaquete de seguridad durante la preparación mínima.

```bash
./kali.sh validate
./kali.sh inventory
./kali.sh status
```

`validate` puede interrumpir trabajo activo porque detiene y vuelve a iniciar
`kali`. Comprueba:

1. Que Kali y APT responden, los cuatro paquetes están instalados y `dpkg --audit`
   no informa problemas.
2. Resolución DNS y HTTPS hacia la web oficial de Kali, con verificación TLS.
3. Diagnóstico de la interfaz loopback con `ip` y consulta DNS con `dig`.
4. Mapeo de UID rootless, seccomp y `NoNewPrivs` en el proceso del contenedor.
5. Opciones de aislamiento, ausencia de puertos, dispositivos y montajes extra.
6. Escritura compartida con propietario Fedora correcto y permisos `0600`.
7. Persistencia de un archivo interno, otro compartido y paquetes tras `stop/start`.

Usa nombres aleatorios para los archivos de prueba y retira solo esos archivos
después de comprobar su contenido. Si una prueba falla, se conserva el entorno
para diagnosticarlo. No prueba reinicio físico de Fedora ni disponibilidad de
cada herramienta de Kali. No escanea equipos ajenos.

## Instalación progresiva y diagnóstico de almacenamiento

Después de validar el mínimo, compara perfiles **sin instalar herramientas**:

```bash
./kali.sh plan headless
./kali.sh plan cli
./kali.sh plan everything
./kali.sh plan everything-full
```

Cada plan actualiza índices y usa `apt-get --assume-no --no-remove
--no-install-recommends install ...`, salvo `everything-full`, que utiliza
`--install-recommends` para estimar también las recomendaciones habituales de
APT. APT calcula dependencias y aborta antes de
descargar paquetes o instalarlos. Sí ocupa espacio para los índices actualizados
y los informes locales. Si APT no resuelve el perfil o no se puede interpretar
su estimación, el script se detiene y no considera aprobado el plan.

| Perfil | Selección |
| --- | --- |
| `headless` | `kali-linux-headless`, conjunto oficial sin escritorio |
| `cli` | Información/OSINT, web, tráfico, forense, criptografía/esteganografía y análisis inverso, mediante seis metapaquetes `kali-tools-*` |
| `everything` | `kali-linux-everything`, sin recomendaciones adicionales |
| `everything-full` | El mismo metapaquete con recomendaciones APT |

**`cli` es una selección temática, no una garantía de que todas sus dependencias
sean de consola o funcionen sin configuración adicional.** Las agrupaciones
oficiales también pueden incluir aplicaciones gráficas y servicios. La elección
final se hará después de revisar dependencias y limitaciones.

El presupuesto suma instalación adicional, descarga estimada y 2 GiB de margen
temporal, y exige conservar 20 GiB libres en el sistema de archivos donde Podman
almacena sus capas. Es una comprobación previa, **no una cuota ni una reserva
real**: otras aplicaciones pueden consumir espacio durante la instalación.
Snapshots Btrfs, bases de datos, diccionarios extra, cachés y exportaciones pueden
necesitar más. Los tamaños APT no equivalen exactamente a bloques físicos usados.

La [tabla oficial de tamaños](https://www.kali.org/docs/installation/installation-sizes/)
indica unos 34 GB para Everything sin escritorio en Kali **2024.1**. Es una
referencia histórica, no una medición de Kali Rolling actual. Consulta los planes
obtenidos en tu máquina y el diagnóstico versionado antes de decidir.

Las herramientas de red TCP, web, OSINT, CTF y análisis de archivos pueden ser
útiles en este entorno. Requieren revisión individual:

- `nmap -sT` puede trabajar sin sockets raw; SYN scan, ARP y captura en vivo
  necesitan capacidades adicionales o están limitados por la red rootless.
- El análisis de PCAP y evidencias copiadas a `lab` no necesita capturar Fedora.
  El contenedor no observa automáticamente el tráfico del anfitrión.
- Wi-Fi monitor/inyección, Bluetooth, SDR, USB y GPU requieren hardware y permisos
  adicionales, que este script no concede.
- Burp, Wireshark GUI y otras aplicaciones gráficas requieren integrar una sesión
  gráfica; no se comparten X11, Wayland ni D-Bus por defecto.
- Bases de datos y servidores necesitan configuración y procesos propios.
  Este contenedor no inicia `systemd`; instalar un paquete no inicia todos sus
  servicios. OSINT puede requerir cuentas o claves API propias.

Tener todas las herramientas instaladas no significa que todas estén operativas.
No se concederán privilegios extra ni se instalará la selección amplia sin la
decisión correspondiente. Usa herramientas solo en laboratorios y objetivos
para los que tengas autorización.

Referencias: [metapaquetes oficiales](https://www.kali.org/docs/general-use/metapackages/),
[lista de dependencias](https://www.kali.org/tools/kali-meta/) y
[opciones de Podman](https://docs.podman.io/en/latest/markdown/podman-run.1.html).

## Respaldo, recuperación y eliminación

Antes de eliminar, identifica dónde guardaste datos y crea una copia. El respaldo
del contenedor **no incluye** `lab/` ni `.state/`. Una exportación puede contener
credenciales o datos sensibles: no la publiques en GitHub.

Ejemplo manual, desde la raíz del repositorio y con un nombre de backup nuevo:

```bash
./kali.sh stop
mkdir -m 700 backups
# Reemplaza FECHA por una fecha o identificador nuevo; no sobrescribas copias.
(umask 077; set -C; podman export kali > backups/kali-rootfs-FECHA.tar)
(umask 077; set -C; tar -czf backups/lab-FECHA.tar.gz -- lab)
(umask 077; set -C; tar -czf backups/state-FECHA.tar.gz -- .state)
```

Si `backups` ya existe, revisa sus permisos y omite `mkdir`; no lo borres.
Comprueba el código de salida y que los archivos pueden listarse antes de confiar
en ellos. `podman export` conserva el sistema de archivos, pero no toda la
configuración del contenedor: para reconstruirlo hacen falta también este script,
el digest y las opciones documentadas. Restaurar un clon más su `lab` reproduce el
entorno mínimo; no recupera automáticamente paquetes adicionales ni archivos de
la capa interna. Una restauración desde exportación requiere importar la imagen
y crear un contenedor con la configuración equivalente, bajo revisión manual.
No se ha probado esa restauración salvo que lo indique el informe de validación.

```bash
./kali.sh remove
```

`remove` exige que el contenedor esté detenido, vuelve a auditarlo, muestra cuántas
entradas cambiaron y advierte que se perderá **toda su capa interna**, incluidos
paquetes, `/root` y `/var/tmp`. Solo procede en una terminal interactiva después
de escribir `ELIMINAR kali` seguido del ID completo mostrado. Elimina por ese ID,
sin `--force`, y conserva `lab/`, `.state/`, backups y la imagen. No existe una
opción de confirmación automática. No elimina otros contenedores, imágenes o
volúmenes ni ejecuta `podman system prune`.

## Publicación y verificación

Versiona `kali.sh`, `README.md`, `.gitignore`, `image.lock`, `LICENSE` y los
informes resumidos de `docs/`. Revisa siempre `git diff --cached` antes del commit.
`.gitignore` reduce el riesgo de publicar material de laboratorio, pero no
sustituye esa revisión. Nunca uses `git add -f` con evidencias o secretos.

El informe en [`validation.md`](validation.md), si existe, distingue verificaciones realizadas,
estimaciones APT y pruebas pendientes. No supone que todos los equipos Fedora 44
produzcan exactamente los mismos resultados.

Pruebas del script (no necesitan descargar imágenes ni usar el motor real):

```bash
bash -n kali.sh
python3 -m unittest discover -s tests -v
git diff --check
```

Las pruebas de regresión usan un motor simulado y comprueban rechazo de
contenedores ajenos, montajes extra, red del anfitrión, enlaces simbólicos,
espacio insuficiente y borrado sin confirmación, además del nombre fijo `kali`.
Requieren ejecutar como usuario normal en Fedora 44. La validación real del
contenedor es una comprobación adicional, no está sustituida por esas pruebas.
