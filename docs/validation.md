# Validación del entorno mínimo

Fecha de ejecución: 28 de octubre de 2026, zona America/Guayaquil.
Resultados de una máquina Fedora 44 x86_64; no son una garantía para otros equipos.
No se incluyen rutas personales, credenciales, direcciones de red ni registros
completos. Los informes originales permanecen en `.state/`, fuera de Git.

## Inspección y preparación

| Comprobación | Resultado observado |
| --- | --- |
| Fedora | 44 Workstation |
| Kernel del anfitrión | `7.2.11-200.fc44.x86_64` |
| Podman | `5.8.9`, ya instalado; no se reinstaló |
| Rootless | Confirmado, con rangos subordinados disponibles |
| Red | netavark / pasta |
| Almacenamiento | overlay |
| SELinux / seccomp / firewalld | Enforcing / habilitado / activo |
| Nombre `kali` antes de comenzar | Disponible |
| Espacio antes de la preparación | Aproximadamente 85,00 GiB libres |

La inspección inicial ejecutada dentro del sandbox del agente no podía consultar
Podman ni el bus de systemd y mostraba un estado SELinux distinto. Los resultados
de esta tabla se comprobaron fuera del sandbox, como usuario normal y sin sudo.

Se usó la imagen oficial `docker.io/kalilinux/kali-rolling:latest`, fijada en
`image.lock` como:

```text
docker.io/kalilinux/kali-rolling@sha256:cd030992fed90891eb7de0e2b1b7b1972ab46fcfaf75d969af0b94304214b6e3
```

No se instalaron paquetes de Fedora ni se modificaron sus repositorios,
configuraciones de seguridad o red. Se crearon `lab/`, `.state/`, la imagen local
y el contenedor persistente. El script y la documentación están versionados.

## Paquetes mínimos

APT descargó 9517 kB y calculó 27,0 MB adicionales para 44 paquetes nuevos,
incluidas dependencias. No se realizó una actualización general de Kali.
Los cuatro paquetes solicitados quedaron así:

| Paquete | Versión comprobada |
| --- | --- |
| `ca-certificates` | `20260816+nmu1` |
| `curl` | `8.22.0-1` |
| `iproute2` | `7.2.0-1` |
| `bind9-dnsutils` | `1:9.20.29-1` |

Kali se identifica como `Kali GNU/Linux Rolling`; APT es `3.3.3 (amd64)`.
`bind9-dnsutils` es el paquete instalado que proporciona `dig`; el nombre
`dnsutils` no aparece como un paquete instalado en esta versión.
El inventario local completo contiene 123 paquetes instalados.

## Verificaciones realizadas

- Auditoría de opciones de creación: rootless, sin privileged, sin eliminación
  automática, sin puertos publicados ni dispositivos adicionales.
- Red pasta, namespaces privados y capacidades limitadas, sin NET_ADMIN/NET_RAW.
- `NoNewPrivs=1` y `Seccomp=2` dentro del contenedor.
- Mapeo de UID verificado desde Fedora: UID 0 interno corresponde al usuario
  normal del anfitrión. Consultar únicamente el mapa desde el namespace anidado
  no demuestra ese mapeo con el anfitrión.
- Un único montaje de usuario, `lab/` a `/lab`, con modo `0700`. Su contexto
  SELinux coincide con la etiqueta privada del montaje del contenedor.
- `dpkg --audit` sin incidencias; consulta de versiones de los componentes mínimos.
- Acceso HTTPS a la web oficial de Kali con verificación TLS y resolución DNS.
- Consulta DNS mediante `dig`; diagnóstico de loopback mediante `ip`.
- Archivo compartido visible desde Fedora, propiedad del usuario normal y modo
  `0600`.
- Un archivo interno y otro compartido conservaron su contenido tras detener e
  iniciar; los cuatro paquetes permanecieron instalados. Solo se retiraron los
  archivos temporales creados por esas pruebas.
- Repetición de la preparación sin reinstalar ni añadir paquetes ya presentes.
- Entrada interactiva con `./kali.sh shell` en `/lab`, umask `0077`; salir de Bash
  dejó `kali` en estado `running`. Consulta de estado y registros sin errores.
- Siete pruebas de regresión con motor simulado para proteger contenedores ajenos,
  montajes, red, enlaces, reserva de espacio, confirmación de borrado y nombre.
- Sintaxis Bash y revisión de whitespace de Git.

Advertencia observada: al ser `sleep infinity` el PID 1, no atendió SIGTERM.
Podman esperó 10 segundos y terminó el proceso con SIGKILL. El stop/start y la
persistencia pasaron. Esta limitación está documentada; no se eliminó ni recreó
el contenedor. Antes de detener futuros servicios con estado, hay que cerrarlos
de forma ordenada desde sus propias herramientas.

## Almacenamiento después del mínimo

| Medida | Resultado |
| --- | --- |
| Imagen base, tamaño lógico según Podman | 250861141 bytes, aproximadamente 239,24 MiB |
| Capa escribible, incluidos índices APT | 62763659 bytes, aproximadamente 59,86 MiB |
| Sistema de archivos total lógico | 313624800 bytes, aproximadamente **0,292 GiB** |
| Libre en el sistema de archivos de Podman | Aproximadamente **84,89 GiB** |
| Reserva exigida para Fedora | **20 GiB** |

Los tamaños lógicos no equivalen a ocupación física exacta en Btrfs. El espacio
libre también depende de otras aplicaciones. No se ha atribuido al contenedor
toda la variación del disco durante el trabajo. La consulta de estado al cerrar
la validación mostró **84,81 GiB** libres; la tabla de planes usa la medición de
84,89 GiB tomada al generarlos.

## Estimaciones actuales de APT: no instaladas

Estimaciones adicionales respecto al mínimo, tomadas de los índices disponibles
durante la ejecución. Los perfiles `headless`, `cli` y `everything` deshabilitan
recomendaciones; `everything-full` sí las incluye. Todos impiden eliminaciones.

| Perfil | Paquetes nuevos | Descarga GiB | Instalado adicional GiB | Presupuesto temporal GiB | Libre tras presupuesto GiB |
| --- | ---: | ---: | ---: | ---: | ---: |
| `headless` | 1268 | 1,41 | 5,81 | 9,23 | 75,66 |
| `cli` temático | 1665 | 2,94 | 8,18 | 13,12 | 71,77 |
| `everything` | 3340 | 11,55 | 32,32 | 45,87 | 39,02 |
| `everything-full` | 4192 | 12,76 | 36,23 | 50,99 | 33,90 |

Los cuatro planes incluyen además tres actualizaciones necesarias y cero
eliminaciones. El presupuesto temporal es instalación + descarga + 2 GiB de
margen; no es una cuota ni una predicción exacta del pico de uso. Los tamaños de
APT se convierten de unidades decimales a GiB. Sin conservar archivos descargados,
el espacio libre tras la instalación sería mayor que la última columna, pero no
se ha ejecutado una instalación amplia para medirlo.

Los cuatro perfiles pasan la comprobación aritmética de conservar 20 GiB, con el
disco observado. **Ninguno se instaló ni está aprobado para instalación.** Los
planes no equivalen a una prueba de funcionamiento de sus miles de paquetes.

`headless` ofrece un conjunto oficial orientado a terminal; `cli` agrupa seis
metapaquetes para información/OSINT, web, tráfico, forense, criptografía y análisis
inverso. Ambos pueden incluir servicios o dependencias gráficas. Everything
también instala herramientas de hardware que el contenedor no puede aprovechar
con los permisos actuales. La selección final requiere revisar esa utilidad.

## Comandos principales ejecutados

```bash
./kali.sh inspect
./kali.sh setup
./kali.sh validate
./kali.sh plan headless
./kali.sh plan cli
./kali.sh plan everything
./kali.sh plan everything-full
./kali.sh shell
./kali.sh status
./kali.sh logs
python3 -m unittest discover -s tests -v
bash -n kali.sh
git diff --check
```

También se consultaron metadatos reducidos con `podman info`, `podman inspect
--size`, `podman image inspect`, espacio libre y el contexto SELinux de `lab`.
La entrada interactiva y los comandos de estado/registros también se comprobaron;
no se ejecutaron herramientas contra objetivos de terceros.

## Pendiente o fuera del alcance verificado

- Reiniciar físicamente Fedora o iniciar automáticamente el contenedor al arrancar.
- Instalar o probar los perfiles amplios; identificar todas sus herramientas
  compatibles. No se promete que todas funcionen en rootless.
- Interfaz gráfica, hardware, Wi-Fi monitor, inyección, Bluetooth, SDR y GPU.
- Captura de tráfico del anfitrión, sockets raw y administración de interfaces.
- Configurar servicios y bases de datos de herramientas adicionales.
- Exportar y restaurar una copia de seguridad, o ejecutar una eliminación real.
- Otras máquinas, arquitecturas y reconstrucciones con futuros índices Rolling.

Referencias: [imagen oficial](https://www.kali.org/docs/containers/official-kalilinux-docker-images/),
[metapaquetes](https://www.kali.org/docs/general-use/metapackages/) y
[tamaños históricos](https://www.kali.org/docs/installation/installation-sizes/).
