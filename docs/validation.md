# Validación de Kali Everything

Resultados observados en Fedora 44 x86_64 el 28 de octubre de 2026
(zona America/Guayaquil). Los valores corresponden a este equipo y a sus índices
Rolling en esa fecha; no garantizan idénticas resoluciones en otra instalación.
No se versionan perfiles VPN, direcciones privadas, archivos de laboratorio ni
logs completos.

## Resultado de la instalación

Se instaló en el contenedor persistente `kali` el metapaquete oficial
`kali-linux-everything` con recomendaciones APT, a partir de la imagen
`docker.io/kalilinux/kali-rolling@sha256:cd030992fed90891eb7de0e2b1b7b1972ab46fcfaf75d969af0b94304214b6e3`.

| Medición | Resultado observado |
| --- | ---: |
| Versión del metapaquete | `2026.4.2` |
| Paquetes con estado instalado (`ii`) | 4.315 |
| Capa escribible del contenedor | 39.996.255.827 bytes, aproximadamente 37,25 GiB |
| Imagen base | 250.861.141 bytes, aproximadamente 239 MiB |
| Tamaño total lógico del rootfs | 40.247.116.968 bytes, aproximadamente 37,49 GiB |
| Espacio libre informado por Podman | 61,81 GiB |
| Espacio libre del filesystem del proyecto | 62 GiB (redondeado por `df`) |
| Reserva mínima requerida por el instalador | 20 GiB |

El plan previo para `everything-full` estimó 4.192 paquetes nuevos, 12,76 GiB
de descarga y 36,23 GiB adicionales. El tamaño real incluye la capa inicial,
índices y efectos de la instalación; no es una cuota. Los bloques físicos pueden
diferir por el filesystem/snapshots. El proceso terminó y ya no mantiene el lock
de APT ni el lock de operaciones.

La instalación no activó la mayoría de servicios automáticamente: el script
comprueba `policy-rc.d` para impedirlo. Tener un paquete instalado no garantiza
que su servicio, base de datos, GUI, firmware o hardware estén configurados.
Algunas herramientas pueden necesitar pasos individuales.

## Pruebas realizadas

- `kali-linux-everything` y herramientas representativas (`nmap`, `tcpdump`,
  `tshark`, `sqlmap`, `nikto`, `gobuster`, `ffuf`, `gdb`, `yara`, `python3`)
  figuran instaladas y sus ejecutables —salvo la excepción descrita abajo— están
  disponibles en `PATH`.
- `dpkg --audit` no reportó paquetes incompletos.
- Kali Rolling y APT respondieron. HTTPS con verificación TLS a `kali.org` y
  resolución DNS desde Kali funcionaron.
- `ip -brief link show lo` funcionó.
- Nmap 7.991 ejecutó `--unprivileged -sT -Pn -p 1-5 127.0.0.1`; el escaneo
  TCP connect de loopback concluyó sin sockets raw y reportó los cinco puertos
  cerrados.
- El paquete de Nmap instala `cap_net_bind_service,cap_net_admin,cap_net_raw=eip`
  en `/usr/lib/nmap/nmap`. Esa file capability impedía ejecutar el ELF bajo
  `no-new-privileges` con las capacidades de contenedor reducidas. Se quitaron
  solo las file capabilities de ese binario dentro de Kali. Nmap volvió a
  ejecutarse con `--unprivileged`, sin `NET_RAW` ni `NET_ADMIN`. El instalador
  automatiza esta corrección tras futuras instalaciones del perfil; una
  actualización manual de Nmap puede requerir repetir
  `podman exec kali setcap -r /usr/lib/nmap/nmap`.
- La auditoría del contenedor pasó: imagen oficial fijada por digest, rootless,
  sin privileged, persistente, red pasta, sin puertos publicados ni dispositivos
  extra, PID/IPC privados, `no-new-privileges`, capacidades limitadas, SELinux y
  seccomp conservados, y un solo bind mount `lab/` con permisos 0700.
- Se detuvo e inició `kali`. `kali-linux-everything` siguió instalado y se
  conservaron archivos temporales de prueba tanto en la capa del contenedor
  como en `/lab`; luego se retiraron esos archivos.
- El escaneo fue solo a loopback. No se conectó a una VPN real ni se escaneó HTB.

## Advertencias y límites comprobados

- `/usr/bin/sleep` como PID 1 no atendió SIGTERM. Podman agotó el timeout y
  recurrió a SIGKILL. Stop/start y persistencia funcionaron; cierra servicios con
  estado desde su interfaz antes de parar el contenedor.
- El modo rootless no proporciona sockets raw ni administración de red. Nmap
  TCP connect sirve para enumeración TCP autorizada; SYN/UDP, captura en vivo del
  anfitrión, Wi-Fi monitor/inyección, Bluetooth, SDR y GPU no quedaron validados.
- La instalación Everything incluye herramientas que pueden requerir GUI,
  hardware, servicios, bases de datos, credenciales/API o ajustes adicionales.
  No se afirma que todos los programas hayan sido ejecutados.
- Fedora ya tenía OpenVPN y el plugin OpenVPN de NetworkManager. Se dejó el
  soporte documentado/preparado, pero no se importó ni activó un perfil personal,
  no se añadieron reglas de firewall y no se probó conectividad a HTB porque no
  se proporcionaron un perfil y un objetivo para ello.
- No se probó reinicio físico de Fedora, exportación/restauración, otras
  arquitecturas ni reconstrucción con futuros índices Rolling.

## Comandos de verificación principales

```bash
./kali.sh status
./kali.sh inventory
podman --remote=false exec kali dpkg-query -W kali-linux-everything
podman --remote=false exec kali bash -c 'dpkg --audit'
podman --remote=false exec kali nmap --unprivileged -sT -Pn -p 1-5 127.0.0.1
```

La instalación se realizó mediante `./kali.sh install everything-full` tras
estimar dependencias y comprobar que se conservaba la reserva de 20 GiB. Los
registros locales están en `.state/` y no se publican.
