# Hack The Box desde Kali rootless

Se ejecuta OpenVPN **en Fedora**, administrado por NetworkManager. Kali conserva
su red privada y sus permisos actuales. El soporte se puede configurar sin
añadir NET_ADMIN, NET_RAW, dispositivos TUN ni red del anfitrión al contenedor.

## Preparar y conectar

1. Obtén tu perfil personal desde [Connect to HTB](https://help.hackthebox.com/en/articles/5185687-introduction-to-lab-access).
   Déjalo fuera del repositorio y de `lab/`; no compartas su contenido.
2. En Fedora deben estar instalados `openvpn` y `NetworkManager-openvpn`. Si
   faltan, su instalación requiere administrador y los repositorios de Fedora:

   ```bash
   sudo dnf --repo=fedora --repo=updates install openvpn NetworkManager-openvpn
   ```

3. Importa y activa como usuario normal:

   ```bash
   chmod 600 /ruta/privada/htb.ovpn
   ./htb.sh import /ruta/privada/htb.ovpn
   ./htb.sh up
   ./htb.sh status
   ```

El importador valida un perfil cliente `dev tun` con comprobación de identidad
del servidor. Rechaza scripts, plugins, inclusiones, archivos externos de claves
y opciones de túnel completo. No imprime certificados ni claves. Hace una copia
privada en `$XDG_CONFIG_HOME/kali-in-fedora/vpn/`, normalmente bajo `~/.config/`,
y crea un perfil de NetworkManager limitado al usuario, sin activarlo.
Los cambios se limitan a ese perfil: sin autoconexión, sin ruta por defecto y
sin reemplazar el DNS global. Las rutas privadas que entrega el servidor se
pueden instalar al conectar. Para DNS de un laboratorio concreto, usa consultas
explícitas a su servidor o una configuración específica revisada aparte.

NetworkManager/Polkit puede pedir autenticación. El script no usa sudo, cambia
configuraciones globales ni desactiva el firewall. Si ya hay un perfil propio,
solo permite reimportar el mismo contenido; no reemplaza ni elimina otro perfil.
Metadatos de UUID y hash se guardan en `.state/htb-vpn.json`, fuera de Git.
No borres ese archivo para sortear la protección; una rotación de perfil requiere
revisar los perfiles existentes de NetworkManager.

Para terminar:

```bash
./htb.sh down
```

Solo desconecta el perfil guardado del proyecto. No cambia otras VPN. Evita
mezclar accesos incompatibles de HTB o varios túneles que anuncien las mismas
redes.

## Comprobar la ruta y escanear

Sustituye `IP_HTB` por la IP de una máquina que te corresponda en el laboratorio
y el puerto por uno que sepas abierto:

```bash
./htb.sh check IP_HTB 80
./htb.sh scan IP_HTB 22,80,443
./htb.sh scan IP_HTB 1-65535
```

`check` exige que el perfil seleccionado esté activo y que `ip route get` de
Fedora elija su interfaz e IP VPN para el objetivo. Después intenta conectar a
ese puerto TCP desde Fedora y desde Kali. Un puerto cerrado o filtrado no sirve
para demostrar la conexión: el helper se detiene e informa el fallo.

`scan` admite una IPv4 privada y una lista/rango de puertos, comprueba la ruta
del anfitrión y ejecuta dentro de Kali:

```text
nmap --unprivileged -sT -Pn -sV --reason -p PUERTOS IP_HTB
```

El TCP connect abre conexiones normales. `--unprivileged` evita que Nmap asuma
permisos raw por ver UID 0 dentro de Kali. `-Pn` omite descubrimiento previo por
ping; no demuestra que el destino esté activo. No se solicita `-sS`, `-sU`,
detección de SO ni traceroute raw. Puede tardar más que un SYN scan, pero sirve
para enumeración TCP sin conceder capacidades al contenedor.

`scan` verifica la ruta de Fedora antes de empezar; no es un kill switch ni
monitoriza continuamente la VPN. Ejecuta primero `check` y mantén la VPN activa.
Si Fedora conecta y Kali no, revisa `pasta`, rutas, MTU y posibles conflictos con
otras VPN. Reiniciar Kali tras conectar puede renovar su configuración copiada,
pero interrumpe procesos: no se hace automáticamente y no se garantiza que
solucione todos los entornos. No se cambiarán rutas o MTU de Fedora a ciegas.

## Conexiones de vuelta: relay TCP

La salida por VPN no publica listeners. Para que el objetivo alcance una
herramienta dentro de Kali, se ofrece un relay en Fedora:

```text
Objetivo HTB -> IP_VPN_FEDORA:4444 -> relay sin privilegios
            -> podman exec -i -> socat en Kali -> 127.0.0.1:4444
```

1. Abre la herramienta/listener en Kali en el puerto elegido, por ejemplo:

   ```bash
   ./kali.sh shell
   nc -lvnp 4444
   ```

2. En otra terminal de Fedora, con la VPN conectada:

   ```bash
   ./htb.sh relay IP_HTB 4444
   ```

   El helper muestra la IP VPN que debe usar el objetivo. No uses la dirección
   LAN copiada que ves con `ip addr` dentro de Kali como dirección de callback.
   En una herramienta que distinga IP anunciada y dirección de bind, anuncia
   la IP VPN de Fedora y escucha dentro de Kali en loopback o `0.0.0.0`.

3. Si el firewall bloquea la entrada, consulta la regla exacta:

   ```bash
   ./htb.sh firewall-hint IP_HTB 4444
   ```

   **Solo muestra comandos; no ejecuta cambios.** La regla propuesta requiere
   administrador y autoriza únicamente ese origen `/32`, destino VPN `/32` y
   puerto TCP, durante una hora. También muestra cómo retirarla antes. No añadas
   reglas amplias ni desactives firewalld. Ejecutar la regla es una decisión
   explícita del usuario; no se aplica durante la preparación sin VPN.

El relay escucha solo en la IP local de la VPN, acepta únicamente el objetivo
indicado y transporta bytes; no ejecuta lo que recibe. No publica un socket
Podman ni añade montajes o permisos al contenedor. Sus conexiones de origen hacia
el listener interno aparecen como loopback; no conserva la IP original dentro
de la herramienta. Es TCP y atiende una conexión cada vez; no sustituye un router
para UDP, callbacks concurrentes, pivoting o protocolos que negocien puertos
adicionales. Puedes usar `--container-port 5555` para mapear otro puerto interno.
Ctrl+C cierra el relay, no desconecta la VPN ni borra Kali.

## Kernel, namespaces e IP

Hay **un único kernel Linux**, el de Fedora. Kali aporta programas y bibliotecas
en un sistema de archivos separado. Cuando Nmap llama a `connect()`, hace una
llamada al mismo kernel, que aplica el espacio de red y los permisos del proceso.
El contenedor no tiene otro kernel ni otro sistema operativo arrancado.

Los namespaces separan usuarios, procesos, montajes y red. Los cgroups limitan
recursos; seccomp y SELinux añaden controles. Rootless mapea el root interno al
usuario normal de Fedora. No concede administración del kernel ni de las
interfaces físicas de Fedora. Instalar herramientas o kernels dentro de Kali
no cambia el kernel que ejecuta el equipo.

Con la red `pasta` actual, se copian direcciones/rutas de la interfaz principal
de Fedora al namespace de Kali. **Se comprobó que comparten valores de IP y que
sus namespaces de red son distintos.** La misma dirección numérica puede
existir en ambos sin que sean la misma interfaz ni compartan los sockets. Esta
configuración no proporciona a Kali una dirección independiente del router.

`pasta` traduce el tráfico del dispositivo TAP del namespace a sockets TCP/UDP
de Fedora. En salida, el kernel del anfitrión selecciona la ruta aplicable. La
configuración de interfaces, otras VPN y las versiones de pasta pueden afectar
el resultado: por eso el soporte exige comprobar un puerto real desde ambos
lados, en lugar de deducir conectividad solo por la IP mostrada.

```mermaid
flowchart LR
    K["Nmap / herramientas en Kali"] -->|"syscall connect()"| N["Red aislada de Kali"]
    N -->|"TAP"| P["pasta: sockets en Fedora"]
    P --> R["Kernel Fedora: ruta al objetivo"]
    R --> T["TUN de la VPN en Fedora"]
    T --> O["OpenVPN cifra el tráfico"]
    O --> I["Wi-Fi / Ethernet: Internet"]
    I --> H["Servidor VPN HTB"]
    H --> M["Máquina del laboratorio"]
```

OpenVPN abre `/dev/net/tun` en Fedora y configura una interfaz virtual más las
rutas privadas del laboratorio. El kernel entrega a TUN el tráfico destinado a
esas redes; OpenVPN lo lee, cifra y envía al servidor HTB mediante la conexión
física. El servidor lo descifra y lo conduce al objetivo; las respuestas vuelven
por el camino inverso. El objetivo ve normalmente la IP asignada a tu extremo
VPN, no la IP pública del proveedor ni una IP propia del contenedor.

La VPN está en el namespace de Fedora: no necesitas ver `tun0` dentro de Kali
para usar sus rutas mediante sockets del anfitrión. Compartir salida no hace
que el listener de Kali sea accesible automáticamente; el relay resuelve esa
entrada de forma explícita. No se ha importado un perfil real ni probado el
trayecto a HTB sin las credenciales personales del usuario.

Referencias primarias: [HTB](https://help.hackthebox.com/en/articles/5185687-introduction-to-lab-access),
[Podman](https://docs.podman.io/en/latest/markdown/podman-run.1.html),
[pasta](https://passt.top/builds/latest/web/passt.1.html),
[NetworkManager](https://networkmanager.dev/docs/api/latest/nmcli.html),
[Nmap](https://nmap.org/book/man-misc-options.html) y
[OpenVPN](https://openvpn.net/community-docs/community-articles/openvpn-2-6-manual/).
