# Kali Linux en Fedora 44 con Podman

Kali persistente de terminal para aprender ciberseguridad y resolver laboratorios
autorizados, sin reemplazar Fedora ni usar máquinas virtuales.
Puedes preparar un mínimo o instalar Everything con sus recomendaciones APT.

## Instalación

Requiere Fedora 44, Bash, Python 3, Podman rootless, `pasta` (paquete `passt`),
`newuidmap`/`newgidmap` y rangos subordinados de UID/GID. SELinux debe estar en
Enforcing y seccomp activo. Se necesitan al menos 22 GiB libres para empezar;
el script exige conservar 20 GiB para Fedora.

```bash
git clone https://github.com/cschavezv03-ops/kali-in-fedora.git
cd kali-in-fedora
./kali.sh inspect
./kali.sh setup
./kali.sh shell
```

Ejecuta el script como usuario normal, **sin sudo**. Si falta Podman, instala
sus requisitos desde los repositorios oficiales de Fedora:

```bash
sudo dnf --repo=fedora --repo=updates install podman passt shadow-utils fuse-overlayfs
```

`setup` instala `ca-certificates`, `curl`, `iproute2` y `bind9-dnsutils`, y valida
con un stop/start. Se puede repetir sin reinstalar paquetes ya presentes.
Si existe un `kali` ajeno o incompatible, se detiene sin sobrescribirlo.

Para instalar todas las herramientas después del mínimo:

```bash
./kali.sh install everything-full
```

Comprueba el espacio antes de instalar, bloquea el arranque automático de
servicios y conserva los permisos rootless. Puede tardar bastante y se registra
en `.state/install-*.log`. No interrumpas una instalación para reiniciar Kali.

Se usa la [imagen oficial de Kali](https://www.kali.org/docs/containers/official-kalilinux-docker-images/)
`docker.io/kalilinux/kali-rolling`, fijada por digest en `image.lock`. Esto fija la
base, pero APT Rolling puede resolver versiones diferentes. Validado en Fedora
44 x86_64; otras arquitecturas necesitan una base compatible.

## Uso diario

Desde el directorio del proyecto:

| Acción | Comando |
| --- | --- |
| Entrar a Kali, iniciándolo si hace falta | `./kali.sh shell` |
| Salir de Bash y dejar Kali activo | `exit` |
| Iniciar / detener | `./kali.sh start` / `./kali.sh stop` |
| Consultar estado y espacio libre | `./kali.sh status` |
| Validar conexión, seguridad y persistencia | `./kali.sh validate` |
| Guardar versiones de paquetes | `./kali.sh inventory` |
| Ver registros del proceso principal | `./kali.sh logs` |
| Consultar ayuda | `./kali.sh help` |

**`validate` y la validación final de `setup` reinician el contenedor:** evita
usarlos durante trabajo activo. Detenerlo conserva paquetes y archivos internos.
No se inicia automáticamente al arrancar Fedora.

Trabaja en `/lab` dentro de Kali: es el mismo directorio `lab/` visible desde
Fedora, con permisos `0700`. Los cambios y borrados afectan a ambos lados.
No muevas el clon mientras exista el contenedor: el montaje usa su ruta absoluta.

Los informes, inventario y planes están en `.state/`. `logs` suele estar vacío
porque el proceso principal es `sleep`; consulta `setup-*.log` y `validate-*.log`
para las operaciones del script. `lab/`, `.state/` y `backups/` se excluyen de Git.
No publiques credenciales o evidencias ni las fuerces con `git add -f`.

## Rootless y limitaciones

Rootless significa que Podman funciona con tu usuario normal de Fedora. Aunque
aparezcas como `root` dentro de Kali y puedas administrar sus paquetes, **no eres
administrador de Fedora**. El contenedor comparte su kernel y puede modificar
los archivos que le hayas compartido.

- Solo se monta `lab/`, con etiqueta SELinux privada. Se conservan SELinux,
  seccomp y firewalld; sin `--privileged` ni dispositivos adicionales.
- Hay salida de red, sin puertos publicados ni red del anfitrión. No es un
  cortafuegos de salida: también puede alcanzar destinos accesibles de la LAN.
- Sin `NET_RAW` ni `NET_ADMIN`: SYN scan y captura en vivo tienen limitaciones.
  Wi-Fi monitor/inyección, Bluetooth, SDR y GPU necesitan acceso adicional.
- No hay sesión de escritorio ni integración gráfica. No se inicia `systemd` ni todos los
  servicios de las herramientas automáticamente. Instalar no garantiza funcionar.
- `sleep` como PID 1 puede ignorar SIGTERM: se observó que Podman recurrió a
  SIGKILL tras 10 segundos. Cierra ordenadamente los servicios con estado antes
  de detener Kali. La persistencia tras stop/start está comprobada.

## Hack The Box: VPN en Fedora

`htb.sh` prepara un perfil privado de NetworkManager y permite comprobar la ruta,
escanear por TCP y recibir conexiones en Kali. Requiere `openvpn` y
`NetworkManager-openvpn` en Fedora. No ejecutes `htb.sh` con sudo ni copies el
perfil a `lab/` o al repositorio. Sustituye `IP_HTB` por tu objetivo autorizado:

```bash
chmod 600 /ruta/privada/htb.ovpn
./htb.sh import /ruta/privada/htb.ovpn  # Importa sin activar
./htb.sh up                          # Activa solo este perfil
./htb.sh check IP_HTB 80              # Usa un puerto que sepas abierto
./htb.sh scan IP_HTB 1-65535          # Nmap TCP connect, sin sockets raw
./htb.sh down
```

El perfil usa túnel dividido, sin autoconexión ni sustitución del DNS global.
NetworkManager puede solicitar autenticación mediante Polkit. No se modifican
otros perfiles ni se desactiva firewalld. La VPN real de HTB necesita tu perfil
personal y una comprobación posterior; no se afirma haberla probado aquí.

Para reverse shells y listeners, hay un relay limitado a la IP de la VPN y al
objetivo indicado; no requiere recrear Kali ni publicar sus puertos. Consulta
[VPN, escaneos y conexiones de vuelta](docs/htb.md), incluida la excepción de
firewall opcional y temporal. La explicación del kernel y las IP está en
[arquitectura de red](docs/htb.md#kernel-namespaces-e-ip).

## Más herramientas y espacio

Estos comandos **solo estiman; no instalan herramientas** (a diferencia de `install`):

```bash
./kali.sh plan headless        # Conjunto oficial sin escritorio
./kali.sh plan cli             # Metapaquetes temáticos de web, OSINT, tráfico y CTF
./kali.sh plan everything      # Sin recomendaciones APT
./kali.sh plan everything-full # Con recomendaciones APT
```

Everything con recomendaciones se estimó en 36,23 GiB instalados y 12,76 GiB de
descarga. El presupuesto añade 2 GiB de margen y debe dejar 20 GiB libres; es una
comprobación previa, no una cuota. Las cifras pueden cambiar. La instalación
amplia se inicia explícitamente con `install`: algunas herramientas necesitan permisos, hardware,
servicios o interfaz gráfica adicionales. Véase el [diagnóstico](docs/validation.md).

## Respaldo y eliminación

Respalda por separado la capa interna y `lab/`: una exportación de Podman no
incluye el directorio compartido. Sigue la
[guía de respaldo y recuperación](docs/technical-guide.md#respaldo-recuperación-y-eliminación)
y verifica las copias antes de borrar.

`./kali.sh remove` exige Kali detenido y confirmación interactiva escribiendo
`ELIMINAR kali` más el ID completo mostrado. **Se pierden paquetes y archivos
internos**, incluidos `/root` y `/var/tmp`; se conservan `lab/`, `.state/`, backups
y la imagen. No existe eliminación automática.

Para requisitos detallados, permisos, reproducibilidad, recuperación y pruebas,
consulta la [guía técnica](docs/technical-guide.md).
