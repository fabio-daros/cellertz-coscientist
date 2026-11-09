# Bioturing temporary environment for PDL1

Revision: this documentation change starts from `main`
`5f96e5075cc5528eec9024720e2e578c661f0423`. The last Bioturing
consultation recorded the checkout at
`e789b9736d7e0c541de0386c3445987bc250a674`. That older commit does
not contain this procedure. The endpoint selection described below
is implemented in the private deployment and the Docker command
boundary. Neither commit provisions the temporary daemon, installs
the NVIDIA Container Toolkit, or authorizes the worker to install a
system timer.
The historical BindCraft pin
`efb5bfeb8b4b1a5944256f979c34e0c8e6a82d9d` is a different repository
and stays that pin. This page plans one temporary environment on the
Bioturing server. It does not provision it, install packages, download
dependencies, start services, run containers, or use the GPU. It does
not SSH. Phase 6N stays open. Production execution stays disabled. The
Session fixture and the public contracts are unchanged.

The Mac is the editor. Bioturing is a separate machine the operator
reaches by SSH. Commands below are labeled `Mac` or `Bioturing SSH`.
Nothing in the `Bioturing SSH` sections has been run for this revision.

## Observed on Bioturing

These are operator observations from the last consultation. This
revision did not repeat them.

- Linux Mint 22.1, x86_64.
- Cellertz checkout on `main` at
  `e789b9736d7e0c541de0386c3445987bc250a674`.
- Docker 28.2.2, storage driver overlay2 on ext4, data-root
  `/var/lib/docker`.
- No container was running at that consultation.
- User `fabio` has sudo and is not a member of group `docker`.
- `/mnt/hd10tb` is ext4, with 7.3 TB free at that consultation.
- `mkfs.xfs`, `xfs_info`, and `losetup` are installed.
- The user systemd manager was running. `Linger=no`.
- The system manager was degraded. Observed failed units:
  `casper-md5check`, `certbot`, `certs-gend`, `postfix`, `slurmd`.
- GPU RTX 5000 Ada, 32 GB, driver `535.288.01`, reported at 100%
  utilization. Processes using it must be left running.
- Host Python on `PATH` was 3.12.3. `python3.10` was not on `PATH`
  at that consultation. A later run records whether it is present and
  does not require it to stay absent.
- The historical BindCraft tree was clean at
  `efb5bfeb8b4b1a5944256f979c34e0c8e6a82d9d`.
- A historical Apptainer image exists. It is not a Docker image id or
  digest.
- A search limited to that historical directory did not find a
  PyRosetta wheel or the original AlphaFold parameter tar.

## Observed later

These facts were reported after that consultation. This revision did
not repeat them on the host, did not install packages, and did not
download the repository.

- The four NVIDIA Container Toolkit packages are absent:
  `nvidia-container-toolkit`, `nvidia-container-toolkit-base`,
  `libnvidia-container-tools`, and `libnvidia-container1`.
- The current Docker daemon has no `nvidia` runtime.
- The official apt index publishes those four packages at `1.20.1-1`.
  The install guide pins that Debian revision. The release notes list
  the same four packages as `1.20.1`, without the Debian revision.
  The apt candidate is `1.20.1-1`. That publication is not a measured
  install and is not proof that driver `535.288.01` can run the
  governed GPU path.
- The administrator authorizes exclusive temporary resources and
  requires other users' work to be preserved. That authorization does
  not add `fabio` to group `docker`, does not let the worker install
  a system timer, and does not permit a change to the shared daemon.
- The historical Apptainer image is intact. It still is not a Docker
  image id or digest, and it does not replace acceptance of the
  Docker executor. This procedure does not delete it.

## Exclusive XFS file

The system data-root is ext4. `docker_gaps` accepts overlay2 only when
`DriverStatus` contains `["Supports project quotas", "true"]`. This
plan does not enable quotas on an existing disk and does not run
`mkfs` on `/mnt/hd10tb` or on any partition.

Create one new directory on `/mnt/hd10tb`, then one regular file inside
it, then an XFS filesystem on that file, then a loop mount of that
file. The Docker data-root of the temporary daemon sits on that mount.

Proposed file size: 256 GiB, exactly `274877906944` bytes, allocated
with `fallocate` so the blocks are reserved. `truncate` is not used,
because it can leave a sparse file.

Why 256 GiB is the proposal, and not a measurement:

- `docs/validation/pdl1_image_readiness.md` cites the AlphaFold
  archive as about 5.3 GB. That citation is not `params_bytes` and is
  not a measurement for this plan. The exact byte size is the
  operator's file, which was not found in the historical directory.
  The same bytes can exist as the vendor file, as an image layer, and
  again in the build cache.
- `max_output_bytes` in the readiness schema is at most
  `1073741824` (1 GiB). A scientific deployment must set both
  `storage_bytes` and `log_bytes` equal to that manifest value, so one
  governed container's writable layer and its json-file log are each
  capped at or below 1 GiB. That cap is not the image size.
- The base-image size, the PyRosetta wheel size, and the ColabDesign
  tree size are not known. The wheel and the parameter tar are still
  operator-supplied pins.
- 256 GiB leaves room for those unknown layers and a second copy in
  the build cache without reserving terabytes on a disk that other
  work already uses. It is far below the 7.3 TB that were free at the
  last consultation. That free-space figure must be measured again
  before `fallocate`.

Two checks, and neither one fills an unknown size with a number.

Before `fallocate`, the only size gate is free space for the chosen
reservation. Stop unless `df -B1 --output=avail /mnt/hd10tb` reports
at least `343597383680` bytes free (256 GiB plus a 64 GiB margin).
Stop if the path is not the same ext4 mount observed for
`/mnt/hd10tb`. The base-image size, the wheel size, the parameter-tar
size, and the ColabDesign tree size are unknown at this point. They
are not measured, they are not zero, and they are not added into a
total. Do not allocate a second file and do not extend this file onto
another disk.

Before `docker build`, measure only files and images that exist:

- `wheel_bytes` from `stat` of the supplied wheel. Absent file:
  `not_measured`.
- `params_bytes` from `stat` of the supplied tar. Absent file:
  `not_measured`.
- `base_bytes` from `docker image inspect` field `Size` on the
  temporary socket. Image not present: `not_measured`.

Do not start the build while any of the three is `not_measured`. When
all three are measured:

```text
projected = 3 * (base_bytes + wheel_bytes + params_bytes) + 2 * 1073741824 + 10 * 1024**3
```

The factor of three is a planning factor for vendor file, image layer,
and build cache. It is not a measured Docker result. The `2 * 1073741824`
term is the schema cap for one container layer and one log, not a
measured output. Compare `projected` with `df -B1 --output=avail` of
the XFS mount. If `projected` is greater than the free space on that
mount, stop. Do not present `projected` before the three `stat` or
`inspect` results exist.

## Temporary Docker daemon

Do not restart, reload, or reconfigure the daemon whose data-root is
`/var/lib/docker`. Do not edit `/etc/docker/daemon.json`. Do not run
`systemctl restart docker`. Every command that must observe the
existing daemon names `unix:///var/run/docker.sock`. Every command that
must observe the temporary daemon names the socket created below.
A bare `docker` command is not part of this procedure.

Docker 28 can run more than one `dockerd`, but that arrangement is not
accepted here until later validation row 2 passes. The temporary
daemon gets its own files:

| Setting | Location |
| --- | --- |
| config file | `<root>/dockerd.json` |
| data-root | `<mount>/docker-data` |
| exec-root | `<mount>/docker-exec` |
| pidfile | `<root>/dockerd.pid` |
| API socket | `<root>/docker.sock` |
| unit name | `cellertz-pdl1-dockerd-<token>.service` |

`<root>` is the exclusive directory on the ext4 disk. `<mount>` is the
XFS mount. The unit name must not be `docker.service`.

`dockerd.json` sets `data-root`, `exec-root`, `pidfile`, `hosts`, a
bridge name of at most 15 characters, `bip`, and `iptables` false.
While the toolkit install is blocked, it does not set
`runtimes.nvidia`. It does not set
`containerd` to `/run/containerd/containerd.sock`. The supervised
containerd must keep its socket, root, and state under `<mount>`.
Record those three paths from the running process into the inventory.
If any of them is under `/var/lib/docker`, `/var/run/docker`, or
`/run/containerd`, stop and shut this unit down.

`iptables` false is deliberate: governed containers use `--network
none`, and a second daemon must not install rules over the existing
daemon's rules. Image pull uses the daemon's host network, not
container NAT. If a later pull fails while `iptables` is false, stop.
Do not flip the system daemon to compensate.

`bip` and the bridge name are chosen only after the existing daemon's
bridge address is recorded. Stop if the chosen subnet overlaps any
address already present. Do not assume `172.17.0.0/16` is free or
busy; record it.

The system daemon has no `nvidia` runtime, so there is no entry to
copy. Do not copy the rest of the system daemon configuration. The
`runtimes.nvidia` object would be written only in
`<root>/dockerd.json`, and only after the host packages below are
installed and `dpkg -S` has named the runtime binary's provider.
That install is blocked, so the object stays absent. `gpu_runtime`
stays pending and the procedure stops before any container create.
Writing the object would not accept the GPU and would not prove the
driver match.

The API socket is mode `0660`, owner root, group the existing primary
group of `fabio` recorded by `id -g fabio`. Do not use mode `0666`. Do
not add `fabio` to group `docker`. Do not create a new group. Root
still reaches the socket. `fabio` reaches it through that group.

## NVIDIA Container Toolkit candidate

This section is a later procedure. This revision does not SSH, does
not install packages, and does not download the repository.

Two different changes would be required, and neither is a per-user
install. The first would put the four official packages on the host
through apt, as root. Those packages would be visible to every
account on the machine. The second would write `runtimes.nvidia`
only in `<root>/dockerd.json` for the exclusive daemon. The package
install is blocked below, before either change. Configuring that
exclusive file does not install the packages into a home directory,
a rootless Docker config, or a user systemd unit. The official
rootless `--config` example is not used. No account receives its own
copy of the toolkit. This procedure does not `systemctl disable` or
`systemctl mask` a unit that is already present, including
`docker.service`, `containerd.service`, and `crio.service`.

The candidate version is `1.20.1-1` for all four names. The apt
instructions are the Ubuntu and Debian section of
`https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html`.
Linux Mint 22.1 follows that section. The `dnf` and `zypper` sections
are not used. The experimental repository line stays commented. The
driver already on the host stays `535.288.01`. This procedure does
not install or upgrade a driver, CUDA, Docker, containerd, or runc.

### Read-only baseline

Record this baseline before any file from this section is created.
These commands do not install packages and do not add the repository.

- `dpkg-query -W` for the four names. The expected result is that
  none are installed. A version already present stops the procedure.
- `apt-cache policy` for those names, and `dpkg-query -W` for the
  installed `docker*`, `containerd*`, `runc`, `nvidia-driver*`, and
  `cuda*` packages. Versions already installed are the baseline.
- SHA-256 of `/etc/docker/daemon.json` when that file exists, or
  `daemon-json-absent` when it does not.
- `sudo docker --host unix:///var/run/docker.sock info` for `ID`,
  `DockerRootDir`, and `Runtimes`. The expected runtime record has no
  `nvidia` key. `DockerRootDir` remains `/var/lib/docker`.
- Names already present under `/etc/apt/sources.list.d/` and
  `/usr/share/keyrings/`. If
  `nvidia-container-toolkit.list` or
  `nvidia-container-toolkit-keyring.gpg` already exists, stop. If
  another file already points at `nvidia.github.io`, including
  `libnvidia-container.list`, `nvidia-docker.list`, or
  `nvidia-container-runtime.list`, stop. Do not delete or rewrite a
  file this procedure did not create.
- `systemctl show docker.service -p Id -p ActiveState -p SubState
  -p FragmentPath` and `systemctl show docker.service -p
  NRestarts`. Those values are compared again after every later
  command. A restart of this unit stops the procedure.
- Container ids from `sudo docker --host unix:///var/run/docker.sock
  ps -q`, and the GPU process ids from `nvidia-smi`. An empty
  container list at the last consultation is not a standing fact.
- `systemctl show` for `nvidia-cdi-refresh.service` and
  `nvidia-cdi-refresh.path`: `LoadState`, `UnitFileState`,
  `ActiveState`, and `FragmentPath`. Record whether
  `/etc/nvidia-container-runtime/config.toml`,
  `/etc/nvidia-container-toolkit/nvidia-cdi-refresh.env`, and
  `/var/run/cdi/nvidia.yaml` exist.

### Maintainer scripts are outside `apt-get -s`

`apt-get install -s` prints the package transaction. It does not run
`postinst`, `prerm`, or `postrm`, does not process triggers, and does
not show a later `systemctl daemon-reload`, unit enablement, udev
reload, or CDI generation. A simulation with no `Remv` line does not
verify those effects. The published index does not verify them
either.

The tag `v1.20.1` of
`https://github.com/NVIDIA/nvidia-container-toolkit`
shows the following in the source scripts. The `.deb` expands
`#DEBHELPER#` at build time. This page has not read that expanded
text inside the published `1.20.1-1` packages.

`packaging/debian/nvidia-container-toolkit-base.postinst`, in
`configure` and before `#DEBHELPER#`, runs:

```text
/usr/bin/nvidia-ctk --quiet config \
  --config-file=/etc/nvidia-container-runtime/config.toml --in-place
```

When `udevadm` is on `PATH`, that same branch runs
`udevadm control --reload-rules`.
`packaging/debian/rules` invokes `dh` with systemd.
`nvidia-container-toolkit-base.install` at that tag ships:

- `/lib/systemd/system/nvidia-cdi-refresh.service`
- `/lib/systemd/system/nvidia-cdi-refresh.path`
- `/lib/systemd/system/nvidia-cdi-refresh.service.d/10-container-engines.conf`
- `/lib/udev/rules.d/99-nvidia-cdi-refresh.rules`
- `/etc/nvidia-container-toolkit/nvidia-cdi-refresh.env`

The service `ExecStart` runs `nvidia-ctk cdi generate` and sets
`NVIDIA_CTK_CDI_OUTPUT_FILE_PATH=/var/run/cdi/nvidia.yaml`. It also
runs `nvidia-ctk system create-device-nodes`, including
`--load-kernel-modules`. The drop-in sets
`Before=docker.service containerd.service crio.service`. The path
unit watches the module dependency files and `/usr/bin/nvidia-ctk`.
The CDI support page for this release says the specification is
generated when the toolkit is installed or upgraded:
`https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/1.20.1/cdi-support.html`.

`nvidia-container-toolkit.postinst` may create the symlink
`/usr/bin/nvidia-container-toolkit` to
`/usr/bin/nvidia-container-runtime-hook` when the hook exists and the
symlink does not. `nvidia-container-toolkit.postrm` removes that
symlink only in its `purge` branch. The `remove` branch does not.
`nvidia-container-toolkit-base.maintscript` names `rm_conffile` for
older copies of the two units under `/etc/systemd/system/`.

The maintainer scripts of `libnvidia-container1` and
`libnvidia-container-tools` at tag `v1.20.1` were not read for this
page. Their triggers and `configure` actions are not shown to leave
the driver, Docker, and other users' units unchanged.

No command in this procedure disables a unit that the baseline
recorded. Disabling `nvidia-cdi-refresh.service` after a script has
already reloaded systemd is not prevention.

### Installation stays blocked

Prevention of those script effects is not demonstrated. Stop before
this section changes the host. Do not create the keyring. Do not
write `/etc/apt/sources.list.d/nvidia-container-toolkit.list`. Do not
run `apt-get update` for this repository. Do not run `apt-get install`
with or without `-s`. Do not run `nvidia-ctk`. Do not start
`nvidia-cdi-refresh.service` or `nvidia-cdi-refresh.path`.

An install simulation would still be required before any later
unblocked install, and it would still not cover the scripts. Stop
that simulation on a `Remv` line, on an upgrade of an installed
package, on a version other than `1.20.1-1` for any of the four
names, or on any change to an installed Docker, containerd, runc,
NVIDIA driver, or CUDA package. This revision does not reach that
simulation, because adding the repository is already a host change.

### Which package provides each executable

The packaging map at tag `v1.20.1` is not a host query. In that tag,
`nvidia-container-toolkit-base.install` lists
`nvidia-container-runtime`, `nvidia-ctk`, and `nvidia-cdi-hook` for
`/usr/bin`. `nvidia-container-toolkit.install` lists
`nvidia-container-runtime-hook` for `/usr/bin`. The filename does not
establish the provider. `nvidia-container-runtime` is not assumed to
come from the package `nvidia-container-toolkit`.

Before any command uses one of those paths, record `dpkg -S` for the
exact path. The package on that line is the provider. It must be a
package in the ledger, at the version stored for that package. A
provider outside the ledger stops the procedure. `dpkg -L
nvidia-container-toolkit` does not answer this for
`nvidia-container-runtime`. The same `dpkg -S` rule applies to every
other executable this procedure would run from these packages,
including any executable shipped by `libnvidia-container-tools` or
`libnvidia-container1`. This page does not guess those paths.

While the install remains blocked, `dpkg -S` is not expected to find
these paths. Do not use a path that `dpkg -S` does not attribute to
the ledger.

### Runtime only in the exclusive file

`nvidia-ctk runtime configure --runtime=docker` without a config
path edits `/etc/docker/daemon.json`. That command is forbidden, and
so is `systemctl restart docker` from the same guide. `nvidia-ctk`
itself stays unrun while the package install is blocked. Do not
write `runtimes.nvidia` from the package name. A later unblocked
revision may run the command only after `dpkg -S` has named the
provider of `nvidia-ctk` and of the runtime binary, and only as:

```text
sudo nvidia-ctk runtime configure --runtime=docker \
  --config=<root>/dockerd.json
```

`<root>/dockerd.json` would be the only config argument. This
revision does not run that command. The exclusive file written below
therefore has no `runtimes.nvidia` object. Row 2 stays open. Do not
restart `docker.service`, `containerd`, or any unit that was already
running. The later `systemctl daemon-reload` in the exclusive-unit
install is not a license to reload during the package install. If
the system socket lists any container before that exclusive-unit
reload, stop. The install guide warns that a daemon reload can drop
GPU access for containers that already have it.

`nvidia` on the temporary socket is row 2 below. It is not row 6 and
it is not `host_acceptance_complete`. The NVIDIA release notes for
1.20.1 say some features of that release need an R615 or later
driver. The observed driver is `535.288.01`. Those notes are
`https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/release-notes.html`.
The candidate version does not close that comparison.
`host_nvidia_driver` stays unset until the operator compares
`535.288.01` with the CUDA compatibility document cited on the
image-readiness page. This page does not choose a CUDA version and
does not start `--gpus`.

### Interrupted steps and individual removal

Every mutating command in this section is a ledger entry. A stop
between the command and the `created` update leaves `pending`,
including a partial keyring, a partial list file, or a package left
half-installed. Cleanup does not skip `pending`.

Do not run `apt-get -f install`, `dpkg --configure -a`, or
`apt-get autoremove`. Do not run `apt-get purge`. Do not run
`docker system prune` or any other global prune. Do not disable a
unit that the baseline recorded.

For each package name this procedure's ledger marks `pending` or
`created`, simulate the whole transaction before removing it:

```text
sudo apt-get remove -s <one-package-name>
```

Read every `Remv`, `Inst`, and `Conf` line. Stop when any line names
a package outside the ledger. Stop when the transaction would change
Docker, containerd, runc, an NVIDIA driver package, a CUDA package,
or any other package the baseline recorded as installed for other
work. Also record `apt-cache rdepends --installed` for that name.
Stop when a package outside the ledger depends on it. Stop when the
transaction names more than the one ledger package being removed.
Remove the depending ledger package first, then simulate again, so
the accepted transaction names only that one package. A simulation
still does not run `prerm` or `postrm`. It gates the package set. It
does not verify script effects.

Before the removal command, revalidate the ledger:

```text
dpkg-query -W -f '${Package} ${Version} ${Status}\n' <one-package-name>
```

The version and the status must equal the values stored for that
entry. A mismatch stops the removal. Then, and only then:

```text
sudo apt-get remove -y <one-package-name>
```

Do not pass `--auto-remove` or `--purge`. `remove` is not `purge`.
Remove the four toolkit names first, then each recorded dependency,
one name per command, each with its own simulation and revalidation.
A name that was installed before this run is not in that list.

After each removal, record the status again. `deinstall ok
config-files` means conffiles remain. That status is not absence.
The `postrm` source removes `/usr/bin/nvidia-container-toolkit` only
on `purge`, so a `remove` can leave that symlink. Probe these paths
whether or not they are conffiles:

- `/etc/nvidia-container-runtime/config.toml`
- `/etc/nvidia-container-toolkit/nvidia-cdi-refresh.env`
- `/var/run/cdi/nvidia.yaml`
- `/lib/udev/rules.d/99-nvidia-cdi-refresh.rules`
- `/lib/systemd/system/nvidia-cdi-refresh.service`
- `/lib/systemd/system/nvidia-cdi-refresh.path`
- `/lib/systemd/system/nvidia-cdi-refresh.service.d/10-container-engines.conf`
- `/usr/bin/nvidia-container-toolkit` when it is a symlink
- enablement links for `nvidia-cdi-refresh.service` and
  `nvidia-cdi-refresh.path` under `/etc/systemd/system`

Each path that exists, and each package still in `config-files`,
stays a ledger residue. Reconcile a residue only when the baseline
showed it absent, the ledger names it, and the device, inode, and
SHA-256 match. Delete that one file. Do not purge the package to
delete it. A path the baseline already had is left in place. A
mismatch stops cleanup and requests intervention. Do not
`systemctl disable` a unit the baseline recorded. Do not delete
`/usr/bin/nvidia*` by a glob.

Then remove the list file and the keyring only when the path,
device, inode, and SHA-256 match the ledger and the path was absent
before this run. Leave any other NVIDIA list or keyring in place.
Cleanup is not complete while a residue remains, a ledger package is
still `config-files`, or a `pending` entry has no reconciliation.

The package step is cleanup step 11. It runs after the exclusive
daemon is stopped and `<root>` is removed, or when `<root>` was
never created. Removing the toolkit while that daemon is still
running is not recovery.

## One endpoint for every Docker call

`ProductionCommandRunner` starts every governed Docker process with
`env={"PATH": "/usr/bin:/bin"}`. `DOCKER_HOST`, `DOCKER_CONTEXT`,
`DOCKER_CONFIG`, and `HOME` are not passed through. A shell export and
a Docker context in the operator's home are therefore invisible to
Cellertz. Every Docker argv is `docker --host <docker_host> ...`.
There is no default socket. A deployment without `docker_host` fails
as `pdl1_docker_endpoint_required`. An invalid, ambiguous, or
out-of-root value fails as `pdl1_docker_endpoint_rejected`. Examples
and fixtures must set both `docker_host` and `docker_root`. Omitting
them is not filled with `unix:///var/run/docker.sock`.

`build_plan` checks that string only. It does not require the socket
file to exist. A governed Docker command resolves the socket and each
ancestor below `docker_root`. A symlink on the final file or on an
ancestor that leaves that root fails as `pdl1_docker_endpoint_rejected`
before the process starts. Known client globals are read only before
the subcommand. `--config` and `--log-level` consume their value, in a
separate token or after `=`. Unknown globals, and a known value option
with no value, are `pdl1_docker_endpoint_conflict` and do not start a
process. `--host`, `-H`, `--context`, and `-c` are the same rejection
when they select another destination or a context. The approved
endpoint is emitted once. Arguments after the subcommand stay in the
command.

Call sites that hit that runner, all with the configured `--host`:

| Use | Code | Argv shape |
| --- | --- | --- |
| Readiness and enabled startup | `prepare_enabled_infrastructure` calls `daemon_info` and `image_inspect` | `docker --host <docker_host> info --format {{json .}}`, `docker --host <docker_host> image inspect <repository>@<digest>` |
| Create | `CliDockerControl.create` around `ContainerSpec.command` | `docker --host <docker_host> create ... --storage-opt size=<storage_bytes> ...` |
| Start | `CliDockerControl.start` | `docker --host <docker_host> start <id>` |
| Inspect and recovery | `inspect`, `inspect_name` | `docker --host <docker_host> inspect <id-or-name>` |
| Kill and stop | `kill`, `stop` | `docker --host <docker_host> kill <id>`, `docker --host <docker_host> stop --time 1 <id>` |
| Logs and output | `logs`, `copy_out` | `docker --host <docker_host> logs`, `docker --host <docker_host> cp` |
| Daemon identity | `daemon_identity` | `docker --host <docker_host> info --format {{.ID}}` |
| Deadline enforce | `enforce_deadline` via `ProductionCommandRunner` | `docker --host <docker_host>` then `info`, `kill`, `ps`, `inspect`, or `rm --force` |
| Host acceptance plan and `--execute` | `pdl1_host_acceptance` | the same `docker --host <docker_host> info` and `image inspect` argv |

`build_plan` requires the same `docker_host` and `docker_root` and
returns `docker --host <docker_host> build ...`. It does not run the
command.

`run` in `pdl1_host_deadline` does not load
`CELLERTZ_PDL1_DOCKER_CONFIG`. It reads `docker_host` and `docker_root`
from the state file written at install and requires the unit
environment `CELLERTZ_PDL1_DOCKER_HOST` to equal that value. A
deployment edited after the timer is installed does not retarget the
enforcer. The service argv stays
`<interpreter> -m cellertz.application.pdl1_host_deadline enforce --state <path>`.
`_exec_matches` still rejects extra arguments. The unit must also show
that same endpoint in `Environment`. A state file without an endpoint
is uncertain and does not call Docker. Daemon id, container id, and
image id checks remain.

This code does not grant a polkit rule, does not run the worker as
root, and does not make the system-manager timer installable by
`fabio`. That authorization stays pending. Provisioning the XFS file,
starting the temporary daemon, and validating it on the host are also
still pending. `CELLERTZ_SCIENTIFIC_RUN_EXECUTION` stays unset or
`disabled`.

## Deadline identity

The deadline is a systemd transient timer. `start_new_session` is not
that mechanism. The timer must still fire after the worker process is
gone.

| | System manager | User manager |
| --- | --- | --- |
| How the code selects it | `deadline_manager=system`; `systemctl` without `--user` | `deadline_manager=user`; `systemctl --user` and `systemd-run --user` |
| Linger | Not queried. The acceptance row stays `not_run`. | Readiness requires `Linger=yes`. Observed value is `Linger=no`. |
| Logout and SSH drop | The system manager keeps the timer. | With `Linger=no`, the user manager stops when the session ends and the timer goes with it. |
| Process identity | The transient service runs as root. The code does not set `User=`. | The service runs as `fabio`. |
| Socket | Root can open mode `0660`. | `fabio` can open it when the group is `fabio`'s existing group. |
| This host's manager | System manager was degraded. Failed units listed above stay untouched. | User manager was running only for the current session. |

The timer should live in the system manager so it survives SSH logout
without changing linger. Record `Linger=no` before any later command,
and require the same value at cleanup. The previous value is
`Linger=no`. Restoring it is a no-op when the procedure never changes
it. This page does not enable linger and does not adopt the user
manager.

That choice is not yet something the worker can do. `systemd-run`
without `--user` installs the timer in the system manager. The
Cellertz worker runs as `fabio`. This page does not grant `fabio` a
polkit rule, and it does not run the worker as root. An interactive
`sudo systemd-run` is a different identity and does not prove the
worker can install the timer. Until a reviewed authorization exists,
the system-manager deadline stays blocked. Do not add that
authorization while preparing the daemon.

The system manager being degraded is not a reason to repair
`casper-md5check`, `certbot`, `certs-gend`, `postfix`, or `slurmd`.
If the new dockerd unit or the deadline timer fails to become active,
stop. Do not `reset-failed` on those other units.

The deadline interpreter is an absolute path to a virtualenv Python
created later inside `<root>`, based on the host `python3.12.3`. The
project requires `>=3.12`. That interpreter is for
`pdl1_host_deadline` on the host. It is not the image's Python 3.10,
and a successful import there is not the pending 3.10 check. The
virtualenv may import the existing checkout. Cleanup deletes the
virtualenv and does not delete the checkout.

## Later validation

Each row is a later operator action. This revision runs none of them.
Stop the row, and do not start the next, when its pass line is false.
Do not use the GPU while a preserved process still needs it.

1. Python 3.10 imports. Record `command -v python3.10` and, when the
   command prints a path, `python3.10 --version`. If a 3.10 interpreter
   is on `PATH`, run the host import with that binary, `PYTHONPATH` set
   to the checkout `src`, and `python3.10 -S -c` importing
   `cellertz.application.pdl1_container_entrypoint` and
   `cellertz.application.pdl1_image_readiness`. Pass: both imports
   succeed and `cellertz.application.conversation`,
   `cellertz.application.contracts`, and
   `cellertz.application.fixtures` stay out of `sys.modules`. If
   `python3.10` is not on `PATH`, record `python3.10-not-on-path` and
   leave the host check pending. Do not point it at Python 3.12.3.
   The image build's `assert_base_environment` and
   `assert_post_install` are a separate in-image check. They reject a
   base Python other than 3.10. Their pass does not rewrite the host
   record, and a missing host binary is not a failed precondition.
2. NVIDIA runtime on the temporary daemon. The runtime object is the
   one written in `<root>/dockerd.json`. It is not copied from the
   system daemon, which has no `nvidia` runtime. Pass:
   `docker --host unix://<root>/docker.sock info --format '{{json .Runtimes}}'`
   contains `nvidia`, `OSType` is `linux`, `Driver` is `overlay2`,
   and `ID` differs from the system daemon's `ID`. The system daemon's
   `DockerRootDir` is still `/var/lib/docker`, its `ID` is unchanged,
   and its `Runtimes` still have no `nvidia` key. This row does not
   compare the driver and does not accept a GPU container. Project
   quotas are row 3, not this row.
3. Project quotas and the write limit. `xfs_info` is recorded and is
   not the pass. Pass the mount only when both of these hold:
   `findmnt -T <mount> -o FSTYPE,OPTIONS -n` shows `xfs` and an option
   `prjquota` or `pquota`; `sudo xfs_quota -x -c 'state -p' <mount>`
   reports project accounting and project enforcement enabled. If
   `xfs_quota` is not on `PATH`, this row stays blocked. Do not install
   `xfs_quota` to unblock it. The toolkit packages are a separate
   host step and do not satisfy this row.
   Then create one benign container through the temporary socket with
   `--storage-opt size=<storage_bytes>`. Record `projid` of that
   container's upper directory with `xfs_io -r -c stat`. The only
   report row used below is the project whose id equals that `projid`.
   If `xfs_io` is not on `PATH`, or the row is missing, this row stays
   blocked. Do not install `xfs_io` to unblock it.
   Read the integers with `sudo xfs_quota -x -c 'report -p -N' <mount>`.
   Do not pass `-h`. A column with a suffix such as `K`, `M`, or `G`
   is not a report value and stops the row. The report `used` and
   `hard` columns are 1024-byte units. The filesystem block size is a
   different unit, from `stat -f -c %S <mount>`:

   ```text
   used_bytes = used_report * 1024
   limit_bytes = hard_report * 1024
   margin_bytes = 64 * filesystem_block_size
   write_bytes = ((limit_bytes - used_bytes - margin_bytes) // filesystem_block_size) * filesystem_block_size
   ```

   `margin_bytes` is an allowance for the probe file and its metadata.
   It is not a measured image size. Integer division rounds
   `write_bytes` down to a whole filesystem block so the first write
   stays inside the limit. If `write_bytes` is below one filesystem
   block, stop. The layer is already too full to separate a successful
   write from the quota.
   Write a regular file of exactly `write_bytes` of data inside the
   container. Do not use `truncate` and do not leave a hole. `fsync`
   that file, then `sync`. The write and both flushes must complete.
   Read the same `projid` again.
   `used_after_bytes = used_after_report * 1024` and
   `remaining_bytes = limit_bytes - used_after_bytes`. If
   `remaining_bytes` is negative, stop. The first write did not fit.
   The overshoot is one filesystem block past the balance that remains:

   ```text
   attempt_bytes = ((remaining_bytes // filesystem_block_size) + 1) * filesystem_block_size
   ```

   Append `attempt_bytes` to that same file. Open it for append, write
   the additional bytes at the end, and do not truncate it and do not
   replace the first `write_bytes`. Then `fsync`. That append or that
   `fsync` must fail with errno `EDQUOT`. Record the message
   `Disk quota exceeded` when the runtime prints it. The message
   without `EDQUOT` is not the pass, and `ENOSPC` is not the pass.
   Read the same `projid` once more. `hard_report` is unchanged, and
   `used` has not grown by `attempt_bytes`. A quota error on any other
   project is not this result. `StorageOpt` in `docker inspect` is not
   this result. The container id is the one just created, and its name
   is unused on both daemons before create.
4. Timer after worker loss. This row stays blocked while the worker
   cannot install a system timer. When a reviewed authorization exists,
   the identity that installs the timer is recorded. Pass for an armed
   timer, including after the worker process is stopped:
   `systemctl show <unit>.timer -p Id -p ActiveState -p SubState`
   reports `ActiveState=active` and `SubState=waiting`.
   `ActiveState=waiting` is not a substitute for `SubState=waiting`.
   `SubState=running` means the timer is no longer waiting. The
   service unit is shown separately. The current code queries only
   `ActiveState` and accepts the line `ActiveState=waiting` or
   `ActiveState=active`. That acceptor is not this pass line. The
   enforcer then kills only the recorded container id on the temporary
   daemon. `docker info` during enforcement must report the temporary
   daemon id. A kill on the system socket fails this row.
5. Image and input identity. Pass: `docker image inspect` on the
   temporary socket shows `Id` equal to the deployment `image_id` and
   `RepoDigests` containing `repository@image_digest`. Input files
   match the pinned SHA-256 values. The Apptainer image is not
   accepted as either identity. A wheel filename that matches PEP 427
   still has `wheel_abi_verified` false.
6. GPU free before scientific work. Pass: the operator reads
   `nvidia-smi` and records that no preserved process is using the
   RTX 5000 Ada. If utilization is still occupied, stop. Do not kill
   those processes, and do not start a container with `--gpus`.
   Driver `535.288.01` is the observed value to write in
   `host_nvidia_driver` only after the operator compares it with the
   NVIDIA compatibility document already cited in the image-readiness
   page. This page does not make that comparison and does not choose
   a CUDA version.

`host_acceptance_complete` stays false until the host-acceptance
procedure is actually run. A passed row here does not set
`CELLERTZ_SCIENTIFIC_RUNNER_READINESS` and does not enable execution.

## Blockers that remain

- No PyRosetta wheel and no AlphaFold parameter tar from the limited
  historical search. Do not scan all of `/mnt/hd10tb` to hunt for
  them. A later search, if the operator names one directory, stays
  inside that directory. A miss stays a blocker. This repository does
  not download either file.
- Base-image digest, CUDA version, ColabDesign commit, wheel digest,
  and parameter SHA-256 are still operator pins. The historical
  Apptainer image is not one of them.
- Host `python3.10` was absent at the last consultation. The later
  procedure records the binary if it appears and runs the host import
  with it. Until that record exists, the host dynamic check stays
  pending. Python 3.12.3 is not that check. The in-image probe is
  separate.
- The four toolkit packages were absent, and the system daemon has
  no `nvidia` runtime. The apt candidate `1.20.1-1` stays uninstalled.
  `apt-get -s` does not show the maintainer-script effects, and those
  effects are not prevented, so the install stays blocked before any
  host change from that section. Driver compatibility and GPU
  acceptance stay pending. `gpu_runtime` and row 6 stay open. The
  intact Apptainer image does not close them.
- Multiple-daemon behavior is unverified on this Docker 28.2.2 until
  the isolated-daemon row passes.
- The endpoint selection is implemented. The temporary daemon is not
  provisioned, and host validation of that daemon has not been run.
  A checkout that contains this procedure is not that validation.
- The worker, as `fabio`, is not authorized to install a transient
  timer in the system manager. No polkit rule is added here. A sudo
  shell is not the worker.
- GPU processes must be preserved. Scientific work stays blocked while
  the device is in use.
- Driver and CUDA compatibility is not decided by this page.
- System manager degraded. Unrelated failed units are not fixed here.
- Phase 6N is not complete.

## Inventory and cleanup

Cleanup is required after the tests, including after a stop in the
middle of preparation. The inventory is
`<root>/inventory.json` once `<root>` exists. Before `<root>` exists,
the planned paths are written to
`/mnt/hd10tb/cellertz-pdl1-plan-<token>.json`. Cleanup removes that
plan file only after `<root>` is gone, or when `<root>` was never
created, and only after the toolkit entries in it are `absent`.
Each mutating step is one ledger entry.
Before the command, append
`state` `pending`, the predicted path, and the probe for that
resource, and `fsync` the plan or inventory file. Do not start the
command until that append is durable. After the command, update the
same entry to `created`, with the observed device, inode, size, or
SHA-256, or to `absent` only when the probe shows that the predicted
path and identity are not present. A stop between the command and that
update leaves `pending`. `pending` means the resource may exist,
including a partial file, a loop with no recorded device name, or a
mount that returned before the ledger moved. It is not evidence of
absence, and cleanup does not skip it.

Cleanup reconciles every `pending` and every `created` entry by the
predicted path and the predicted identity. Remove the resource only
when the probe matches. Mark `absent`, and only then leave it
untouched, when the probe shows the path is missing and the identity
is not attached anywhere else that probe can see. If the path exists
but the identity was never stored, the stored identity does not match,
or more than one object matches the probe, stop. Write that entry
under the evidence directory, leave the resource in place, and do not
declare cleanup complete.

Probes:

- Directory: the predicted path. Remove it when the path exists and
  its stored device and inode both match the directory on disk. If
  that device or inode was never stored, or either differs, preserve
  the directory and request intervention.
- File, including a short `xfs.img`: the predicted path is not enough,
  and a record that the path was absent before the command is not
  enough. Remove the file only when it is a regular file, its device
  matches the recorded filesystem device, any inode or size already
  stored for the file itself matches, and the parent directory's
  stored device and inode match `stat` of the parent now. A short
  `xfs.img` is a partial allocation and uses this same rule. If the
  parent identity was never stored, the parent differs, the file is
  not a regular file, or a stored file inode or size differs, preserve
  the file, record the entry, and request intervention.
- Loop: `losetup --list --noheadings --output NAME,BACK-FILE`. Detach
  when exactly one line names the predicted backing file, and the
  stored device name matches if one was stored. `absent` when no line
  names that backing file. Stop when more than one line matches, or a
  stored name differs.
- Mount: `findmnt` of the predicted target. Unmount when the source is
  the loop identified above. `absent` when that target is not mounted.
  Stop when the source is different.
- Unit and `dockerd`: the identity check below. A `pending` unit file
  whose SHA-256 was not stored is not deleted. `absent` when the path
  is gone and `LoadState=not-found`. Do not kill a live process whose
  command line is not the predicted `ExecStart`.
- Bridge: the bridge rule in the cleanup order. If the ledger has no
  bridge name, do not choose one. Stop instead of deleting `docker0`
  or any interface from the pre-start snapshot.

The dockerd identity is taken from the unit that was installed, not
from a pidfile alone. Before any `systemctl stop` of
`cellertz-pdl1-dockerd-<token>.service`, read:

```text
systemctl show cellertz-pdl1-dockerd-<token>.service \
  -p Id -p FragmentPath -p ActiveState -p SubState \
  -p MainPID -p ExecStart -p ExecMainStartTimestamp
sha256sum <root>/dockerd.json
docker --host unix://<root>/docker.sock info --format '{{.ID}} {{.DockerRootDir}}'
tr '\0' ' ' < /proc/<MainPID>/cmdline
```

Pass, and only then stop the unit, when all of these hold:

- `Id` is `cellertz-pdl1-dockerd-<token>.service`.
- `FragmentPath` is the unit path recorded before install.
- `ExecStart` begins with `/usr/bin/dockerd` and its argv is
  `--config-file` plus the inventory config path, with no second
  config file.
- The SHA-256 of the complete config file equals the hash stored when
  the file was written. That hash is the content check of those
  bytes: data-root, exec-root, pidfile, hosts, bridge, `bip`, and
  `iptables` false. `runtimes.nvidia` is part of that hash only when
  the toolkit section has written it. While the install is blocked,
  the file does not contain that object.
- `MainPID` is the process whose `/proc/<MainPID>/cmdline` is that
  same argv, and `ExecMainStartTimestamp` equals the timestamp stored
  at the first successful start.
- The temporary socket reports the inventory daemon `ID` and
  `DockerRootDir`.

If the unit is already stopped, `ActiveState=inactive`,
`SubState=dead`, and `MainPID=0`, skip the signal and do not require
the socket to answer. Confirm the pidfile does not name a live
process. If it names a live process whose command line and start time
do not match the inventory, abort and do not kill it. Removing a unit
that is already inactive, when the unit file hash matches, is the
idempotent path. Do not stop `docker.service`.

Record at least:

- the token, `<root>`, its device and inode, and the Cellertz `HEAD`
  at the start of the run
- the loop file path, byte size `274877906944`, device and inode
- the loop device name and its backing-file path
- the mount point, its source, and the XFS device id
- `findmnt` options and the `xfs_quota` project state, once those
  commands have been run
- pidfile path, socket path, socket device and inode, data-root,
  exec-root, the containerd socket, root, and state discovered under
  `<mount>`, config path, config SHA-256, unit path, unit SHA-256
- `ExecMainStartTimestamp` and `MainPID` from the identity check
- bridge name and `bip`, plus the pre-start `ip -o link` snapshot
- pre-start device and inode of `/var/run/docker.sock`, and of
  `/run/containerd/containerd.sock` when that socket exists
- system daemon `ID` and `DockerRootDir` taken before the temporary
  daemon starts
- temporary daemon `ID` after it starts
- full container ids created through the temporary socket
- deadline unit names and state-directory files created for those ids
- `Linger` before any command. The recorded value to restore is
  `Linger=no`. This page does not change it.
- `id -g fabio` and the socket mode
- GPU process ids observed before the test, so cleanup can show it
  did not signal them
- the path and checksum of the historical Apptainer image, recorded
  before any command. The checksum is not a Docker image id
- the toolkit baseline: package versions, `/etc/docker/daemon.json`
  hash or `daemon-json-absent`, system `Runtimes`, and `NRestarts`
- each toolkit package and added dependency: name, version, status,
  and the `dpkg -S` provider of every executable this procedure uses
- each removal simulation, and the revalidated version and status
- conffiles, unit files, enablement links, and generated files that
  remain after `remove`, including a `config-files` status
- the keyring path, the sources-list path, and both SHA-256 values
  when this procedure created them. This revision does not create
  them
- each ledger step: predicted path, probe, `state`
  (`pending`, `created`, or `absent`), and any identity stored after
  the command

Before deletion, copy preserved files to
`/mnt/hd10tb/cellertz-pdl1-evidence-<token>/`, which is not inside the
loop file, the Cellertz checkout, or the historical BindCraft tree.
`operational/` receives the inventory, the temporary daemon's
`docker info` and `docker image inspect` JSON, the acceptance report,
and the build log. `scientific/` receives scientific results if any
exist, including a `design_records.csv` the test wrote. If none exist,
write `scientific/NONE`. Do not copy weights, wheels, or the parameter
tar. The evidence directory is kept.

Cleanup order. Reconcile `pending` and `created` entries as above.
Do not treat `pending` as absence. An unresolved probe stops the
order. Cleanup is complete only when every ledger entry is `absent` and
the confirmation step passes. After a matched removal, update that
entry from `created` to `absent`.

1. Export operational evidence and any scientific results.
2. Reconcile the socket entry before any container command. If the
   socket path is absent, container removal through it cannot run. Do
   not treat that as identification of the containers, and do not
   continue to dockerd stop, unmount, or deletion of the data-root
   while a container entry is still unresolved. If the path exists but
   does not answer, stop. Before any removal, `docker info` on that
   socket must report the temporary daemon `ID` stored in the
   inventory. If that `ID` was never stored, or the reported `ID`
   differs, stop and do not remove a container. A container is removed
   only when its own ledger entry stores its full id and `docker
   inspect` on that socket shows the same `Id`. A `pending` entry with
   no stored id is not that identification. An empty daemon at start
   does not make every later id part of this test. Preserve those
   containers, record the entry, and request intervention. Do not
   `docker rm` by name. Do not run these commands against
   `unix:///var/run/docker.sock`.
3. Reconcile each deadline entry by its predicted unit name, including
   a `pending` name. `LoadState=not-found` marks that entry `absent`.
   A unit that exists is stopped, including when it is already
   inactive. `reset-failed` only those unit names. A `pending` entry
   with no predicted name stops cleanup.
4. Run the dockerd identity check above. Stop the unit only when the
   check passes. If it is already inactive and no mismatched live
   process remains, continue. Disable the unit if it is enabled.
   Remove the unit file only when its SHA-256 matches the inventory,
   then `systemctl daemon-reload`. If the file is present and the hash
   was not stored or does not match, stop and do not mark that entry
   `absent`. Pass when
   `systemctl show -p LoadState -p FragmentPath` reports
   `LoadState=not-found`, the unit path is gone, and no drop-in
   directory for that unit remains.
5. Compare `ip -o link` with the pre-start snapshot. The inventory
   bridge may be deleted only if that name was absent before start,
   it is still present, `ip -d link show` says it is a bridge, and its
   address is the inventory `bip`. Do not delete an interface that
   existed before, and do not delete `docker0`.
6. The system containerd socket inode, when it existed, must be
   unchanged, and a socket that was absent must still be absent. Do
   not signal a containerd process that is not a child of the recorded
   `MainPID`. Temporary containerd paths under `<mount>` go away with
   the unmount.
7. Reconcile the mount entry. Unmount only when `findmnt` shows the
   predicted target and the source is the loop from the loop probe.
   A `pending` mount is not skipped. `absent` only when that target
   is not mounted. A different source stops cleanup.
8. Reconcile the loop entry by backing-file path. Detach only the one
   device the loop probe identifies. A `pending` loop is not skipped.
   `absent` only when no device lists that backing file. More than one
   match stops cleanup.
9. Read `Linger` and require `Linger=no`.
10. Remove `<root>` only after the mount is gone, the directory device
    is the ext4 device of `/mnt/hd10tb`, and the inode equals the
    inventory inode. Do not `rm -rf` a path that fails those checks.
    Leave the plan file in place. It still holds toolkit entries when
    `<root>` never existed.
11. Remove toolkit packages only after the exclusive daemon is stopped
    and `<root>` is gone, or when `<root>` was never created. Follow
    "Interrupted steps and individual removal": simulate each
    transaction, stop if it names a package outside the ledger or a
    package another installed package still needs, revalidate version
    and status, then `apt-get remove` without `--purge`. Reconcile
    conffiles, units, enablement links, and generated files one path
    at a time. `config-files` is not absence. Step 4 disables only
    `cellertz-pdl1-dockerd-<token>.service` after its identity check.
    Do not disable `docker.service` or a unit the baseline recorded.
    After the toolkit entries and residues are `absent`, remove the
    plan file if it is still outside `<root>`.
12. Confirm the system daemon `ID`, `DockerRootDir`, `Runtimes`, and
    `/etc/docker/daemon.json` hash still match the pre-start record.
    `docker.service` was not restarted and was not disabled. The
    Cellertz `HEAD` still matches the `HEAD` recorded at the start of
    this run. The historical BindCraft tree is still
    `efb5bfeb8b4b1a5944256f979c34e0c8e6a82d9d`. The historical
    Apptainer checksum is unchanged. The loop device, socket, and
    exclusive unit are gone. The recorded GPU process ids were not
    signaled by this procedure. A toolkit package this run added is
    not left in `config-files`. No residue path from the toolkit
    section remains unreconciled. This confirmation is not cleanup
    complete while any ledger entry is `pending` or unresolved.

Do not use `apt-get autoremove`, `apt-get purge`, `apt-get -f install`,
or `dpkg --configure -a`. Do not use `docker system prune`,
`docker volume prune`, `docker image prune`, or a delete by a shared
name prefix. Do not remove `/var/lib/docker`. Do not remove the
Cellertz checkout, the historical BindCraft artifacts, or the
historical Apptainer image.

## Mac

The Mac does not SSH and does not receive weights or wheels.

```text
git status --short
git rev-parse HEAD
git branch --show-current
```

Pass: the branch is `phase-6n-bioturing-nvidia-toolkit-plan` and
`git status --short` shows only the documentation edits. `HEAD` is
still the base commit until this procedure is committed. Stop if the
worktree contains a wheel, a parameter tar, a credential, or a Docker
context.

## Bioturing SSH

These commands are the later procedure. They are not run by this
revision. Run them as `fabio` on Bioturing. Prefix system-daemon
commands with `sudo` because `fabio` is not in group `docker`.

### Preconditions

Stop immediately if a line that is not explicitly allowed to fail
fails.

```text
git -C <cellertz-checkout> rev-parse HEAD
git -C <cellertz-checkout> merge-base --is-ancestor e789b9736d7e0c541de0386c3445987bc250a674 HEAD
git -C <cellertz-checkout> status --short
test -f <cellertz-checkout>/docs/validation/pdl1_bioturing_temporary_environment.md
python3 --version
if command -v python3.10; then python3.10 --version; else echo python3.10-not-on-path; fi
id -g fabio
loginctl show-user fabio -p Linger
findmnt -T /mnt/hd10tb
df -B1 --output=avail /mnt/hd10tb
if test -S /run/containerd/containerd.sock; then stat -c '%d %i' /run/containerd/containerd.sock; else echo no-system-containerd-socket; fi
stat -c '%d %i' /var/run/docker.sock
sudo docker --host unix:///var/run/docker.sock info --format '{{.ID}} {{.DockerRootDir}} {{.Driver}}'
sudo docker --host unix:///var/run/docker.sock info --format '{{json .Runtimes}}'
sudo docker --host unix:///var/run/docker.sock network inspect bridge --format '{{json .IPAM}}'
ip -o link
nvidia-smi --query-gpu=name,driver_version,utilization.gpu --format=csv
command -v xfs_quota || echo xfs_quota-not-on-path
systemctl is-system-running || true
systemctl --user is-system-running || true
```

Pass: `e789b973` is an ancestor of `HEAD`, the checkout is clean, and
this procedure file is in that tree. `HEAD` may be newer than
`e789b973`. Python 3.12 is present. `python3.10` is either reported
with its version or recorded as `python3.10-not-on-path`; absence is
not a failure. `Linger` prints `Linger=no`. `/mnt/hd10tb` is ext4 with
at least `343597383680` bytes free. The system data-root is
`/var/lib/docker`. The bridge JSON, link list, and GPU processes are
recorded. `xfs_quota-not-on-path` blocks only the quota row.
`systemctl is-system-running` may print `degraded`. That is not a
failure by itself. Stop if it cannot report at all. Do not repair the
failed units named above.

If `python3.10` has a path, run the host import from later validation
row 1 before any image build. Record pass or the pending line. Do not
use `python3` for that import.

Choose a token with `python3 -c 'import secrets; print(secrets.token_hex(4))'`.
Stop if `<root>` or the evidence directory already exists. Write the
plan file before creating `<root>`. The toolkit package install is
blocked, so do not create the keyring, the sources list, or the
packages before `<root>`. Do not run `nvidia-ctk`.

### Prepare

Before each command, append a `pending` entry and `fsync` it. Mark
`created` or `absent` only after the probe. A failure or an interrupt
jumps to cleanup, which reconciles `pending` and `created` entries.
It does not skip a `pending` path.

```text
sudo install -d -o fabio -g fabio -m 0750 /mnt/hd10tb/cellertz-pdl1-temporary-<token>
install -d -m 0750 /mnt/hd10tb/cellertz-pdl1-temporary-<token>/mnt
fallocate -l 274877906944 /mnt/hd10tb/cellertz-pdl1-temporary-<token>/xfs.img
python3 -c 'import os; p="/mnt/hd10tb/cellertz-pdl1-temporary-<token>/xfs.img"; print(os.stat(p).st_blocks * 512)'
sudo mkfs.xfs -f /mnt/hd10tb/cellertz-pdl1-temporary-<token>/xfs.img
sudo losetup --find --show /mnt/hd10tb/cellertz-pdl1-temporary-<token>/xfs.img
sudo mount -o rw,noatime,prjquota <loop-device> /mnt/hd10tb/cellertz-pdl1-temporary-<token>/mnt
findmnt -T /mnt/hd10tb/cellertz-pdl1-temporary-<token>/mnt -o SOURCE,FSTYPE,OPTIONS -n
sudo xfs_quota -x -c 'state -p' /mnt/hd10tb/cellertz-pdl1-temporary-<token>/mnt
```

Pass: the printed allocated size is at least `274877906944` bytes.
`findmnt` shows `xfs` and `prjquota` or `pquota`. `xfs_quota` shows
project accounting and enforcement. `losetup` lists that device with
backing file `xfs.img`. Stop if `mkfs.xfs` or `mount` would target
anything other than that file and that loop device. `xfs_info` may be
recorded and is not this pass.

Write `dockerd.json` with the exclusive settings and hash it. Do not
run `nvidia-ctk`, and do not add `runtimes.nvidia`, while the toolkit
install is blocked. Record that hash before installing the unit.
The unit `ExecStart` is
`/usr/bin/dockerd --config-file <root>/dockerd.json`,
with an `ExecStartPost` that runs `chgrp <fabio-gid>` and `chmod 0660`
on `<root>/docker.sock` only. Record the unit path and its SHA-256
before `daemon-reload`. Do not copy
`/lib/systemd/system/docker.service` over itself.

```text
sudo systemctl daemon-reload
sudo systemctl start cellertz-pdl1-dockerd-<token>.service
systemctl show cellertz-pdl1-dockerd-<token>.service -p Id -p FragmentPath -p ActiveState -p SubState -p MainPID -p ExecStart -p ExecMainStartTimestamp
docker --host unix://<root>/docker.sock info --format '{{.ID}} {{.DockerRootDir}} {{.Driver}}'
sudo docker --host unix:///var/run/docker.sock info --format '{{.ID}} {{.DockerRootDir}}'
stat -c '%a %u %g' <root>/docker.sock
sha256sum <root>/dockerd.json
if test -S /run/containerd/containerd.sock; then stat -c '%d %i' /run/containerd/containerd.sock; else echo no-system-containerd-socket; fi
stat -c '%d %i' /var/run/docker.sock
tr '\0' ' ' < /proc/<MainPID>/cmdline
```

Pass: the identity check in "Inventory and cleanup" holds;
`ActiveState=active`; the socket `ID` differs from the system `ID`;
`DockerRootDir` is `<mount>/docker-data`; the system `DockerRootDir`
is still `/var/lib/docker`; the socket mode is `660` and the group is
the recorded `fabio` group; the system socket inodes are unchanged.
Store `ExecMainStartTimestamp` and `MainPID`. Stop if any of those
differ. Do not continue to a container.

The virtualenv, vendor files, image build, and host-acceptance checks
are later steps. The build stays blocked until the before-build size
check is measured, the pins exist, and the GPU row allows it. The
endpoint change and the worker's system-timer authorization stay
blocked. `CELLERTZ_SCIENTIFIC_RUN_EXECUTION` is not set to `enabled`.

### Cleanup

Run the cleanup order in "Inventory and cleanup". Afterward:

```text
test ! -e /mnt/hd10tb/cellertz-pdl1-temporary-<token>
findmnt /mnt/hd10tb/cellertz-pdl1-temporary-<token>/mnt
sudo losetup --list --output NAME,BACK-FILE
systemctl show cellertz-pdl1-dockerd-<token>.service -p LoadState -p FragmentPath
test ! -e /etc/systemd/system/cellertz-pdl1-dockerd-<token>.service
ip -o link show <bridge-name>
sudo docker --host unix:///var/run/docker.sock info --format '{{.ID}} {{.DockerRootDir}}'
loginctl show-user fabio -p Linger
git -C <cellertz-checkout> rev-parse HEAD
test ! -S /mnt/hd10tb/cellertz-pdl1-temporary-<token>/docker.sock
test -d /mnt/hd10tb/cellertz-pdl1-evidence-<token>
```

Pass: the exclusive directory is gone; `findmnt` does not show that
mount; `losetup` does not list `xfs.img`; the unit `LoadState` is
`not-found`. `ip -o link show <bridge-name>` exiting because the name
is absent is the expected result when this run created that bridge.
If the command prints a link, that link must already have been in the
pre-start snapshot. The system daemon `ID` and `DockerRootDir` match
the inventory. `Linger=no`. Cellertz `HEAD` matches the `HEAD`
recorded at the start.
The evidence directory remains, including `scientific/NONE` when no
scientific result was produced. The historical tree remains.
