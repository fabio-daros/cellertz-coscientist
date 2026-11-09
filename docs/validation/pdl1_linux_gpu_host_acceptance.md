# PDL1 Docker host acceptance

This procedure prepares one Linux GPU machine for a later check of the
merged governed Docker executor. Running it is a separate operator
decision. This document does not record a host result, and it does not
make the scientific runner ready.

The repository does not contain a scientific image digest, image id,
entrypoint, or BindCraft argv. The historical Apptainer image is not a
Docker image identity. Do not copy it into `repository`, `image_digest`,
or `image_id`. Do not use the repository's development `Dockerfile` as
the scientific runtime.

A benign image and workload selected by the operator are used only for
the infrastructure checks below. Their success does not authorize a
scientific run, does not set
`CELLERTZ_SCIENTIFIC_RUNNER_READINESS`, and does not replace the
production image or the approved scientific inputs. Production execution
stays disabled unless `CELLERTZ_SCIENTIFIC_RUN_EXECUTION=enabled` is set
by the operator for a later phase. GPU device assignment is
`--gpus device=<id>`. It is not a VRAM quota. A document that contains
`vram_mib` is rejected.

The helper `python -m cellertz.application.pdl1_host_acceptance` prints
a plan and runs no external command. `--execute` is a separate opt-in
that only observes Docker and systemd. It does not create a container
or install a timer. `host_acceptance_complete` in its report stays
false. A check is `passed` or `failed` only when that check's command
or local probe ran. Commands that readiness skips after an earlier
failure stay `not_run`. System mode does not query lingering, so that
check stays `not_run`. The report's `evidence` and `passing` fields are
the planned criteria. `observed` is a separate allowlisted fact from
the command that ran. It drops other Docker fields, extra command
lines, credentials, and input bytes. These host checks stay
unvalidated until an operator runs them on the Linux GPU machine.

## Where each value comes from

Repository contracts, already fixed in code:

- Profile `pdl1-bindcraft-docker-v1`.
- Deployment file environment name `CELLERTZ_PDL1_DOCKER_CONFIG`.
- Readiness environment names `CELLERTZ_SCIENTIFIC_RUNNER_READINESS`
  (default `disabled`) and `CELLERTZ_SCIENTIFIC_RUNNER_MANIFEST`.
- Execution environment name `CELLERTZ_SCIENTIFIC_RUN_EXECUTION`
  (default `disabled`).
- Create flags: `--network none`, `--restart no`,
  `--security-opt no-new-privileges`, `--cpus`, `--memory <n>m`,
  `--storage-opt size=<bytes>`, `--log-driver json-file`,
  `--log-opt max-size=<bytes>`, `--log-opt max-file=1`,
  `--gpus device=<id>`, and one read-only bind mount at
  `/cellertz/input`.
- Command runner: `shell=False`, `PATH=/usr/bin:/bin`, bounded stdout
  and stderr. Docker argv includes `--host` set to the deployment
  `docker_host`. The runner does not inherit `DOCKER_HOST`.
- Daemon facts required by `docker_gaps`: `OSType=linux`,
  `Driver=overlay2`, `DriverStatus` contains
  `["Supports project quotas", "true"]`, `LoggingDriver=json-file`,
  a non-empty `ID`, and `Runtimes` containing `nvidia`.
- Image facts: `Id` equals `image_id`, `image_id` differs from
  `image_digest`, and `RepoDigests` contains `repository@image_digest`.
- Deadline service command:
  `<interpreter> -m cellertz.application.pdl1_host_deadline enforce --state <path>`.
- User-manager lingering query:
  `loginctl show-user <uid> -p Linger` must be `Linger=yes`.
- Scientific envelope bounds used by manifest parsing: `cpu_count` 1–64,
  `memory_mib` 1–262144, and Docker profile `gpu_count` 1.
- For a scientific deployment, `storage_bytes` and `log_bytes` must both
  equal the manifest `max_output_bytes`, and `entrypoint` plus `argv`
  must equal the manifest. Those scientific equalities are not satisfied
  by the benign acceptance image.

Operator-supplied, with no default in this repository:

- `repository`
- `image_digest`
- `image_id`
- `entrypoint`
- `argv`
- `gpu_device`
- `deployment_id` for the acceptance invocation, one token matching
  `[A-Za-z0-9][A-Za-z0-9._-]{0,40}`
- `staging_root`, absolute
- `acceptance_input_dir`, absolute, containing no scientific inputs
- `storage_bytes` and `log_bytes` for the benign container
- `cpu_count` and `memory_mib` for the benign container
- `deadline_manager`, `system` or `user`
- `deadline_state_dir`, absolute
- `deadline_interpreter`, absolute nonempty executable that can import
  `cellertz.application.pdl1_host_deadline`
- `docker_root`, absolute directory with no `.` or `..` component
- `docker_host`, `unix://` plus an absolute socket path inside
  `docker_root`. TCP, SSH, credentials, query, fragment, and a path
  outside that root are rejected. A real Docker command also rejects a
  symlink on the socket or on an ancestor that leaves `docker_root`.
  `build_plan` does not require the socket file to exist. There is no
  default socket. Older
  examples and fixtures that omit these two keys fail
  `pdl1_docker_endpoint_required`

Every `docker` command below is `docker --host <docker_host> ...`.
A command that omits that flag is `docker_endpoint_divergence`. The
helper does not issue a second call against the default socket.

Observed on the target host, then recorded by the operator:

- `docker info --format '{{json .}}'` fields listed above, including the
  daemon id
- `docker image inspect <repository>@<image_digest>` `Id` and
  `RepoDigests`
- `systemctl --version`, `systemd-run --version`, `docker --version`,
  and `systemctl show --property=Version`
- `Linger=yes` when `deadline_manager` is `user`
- the container id returned by `docker create`
- `systemctl show` `Id`, `ActiveState`, `Result`, and `ExecStart` for
  the timer and service created for that container

## Checks

Each automated row is also emitted by the helper. Manual rows stay
`not_run` after `--execute`. Cleanup applies only to resources created
by that check. Do not delete an image the operator supplied, and do not
stop unrelated units.

### Docker daemon

Purpose: record the daemon the executor will bind to.

Command: `docker info --format '{{json .}}'`.

Evidence: `OSType`, `Driver`, `DriverStatus`, `LoggingDriver`, `ID`,
`Runtimes`.

Pass: linux, overlay2, project quotas reported true, json-file logs, a
daemon id, and the nvidia runtime.

Failure: stop. Do not create a container.

Cleanup: none.

### Image identity

Purpose: bind the benign image the operator selected.

Command: `docker image inspect <repository>@<image_digest>`.

Evidence: `Id` and `RepoDigests`.

Pass: `Id` equals the operator `image_id`, that id is not the digest,
and `RepoDigests` contains `repository@image_digest`.

Failure: stop. Do not retag another image into this name.

Cleanup: do not delete the image.

### Container configuration

Purpose: record the create line and the inspected configuration. This
check does not prove CPU, memory, storage, log, or GPU enforcement.

The operator-selected image is not named by this repository. It must be
able to run the long-running benign argv used later, and, for the
behavioral checks, the workloads named there. If it has no shell, use
one container per workload and put that workload in argv.

Prerequisite: `docker inspect cellertz-accept-<deployment_id>` exits
non-zero. If it exits 0, stop. Do not remove that container.

Command: the helper's manual `container_configuration` argv. It is
`docker create` with the flags listed above, the name
`cellertz-accept-<deployment_id>`, the read-only mount, and labels
`cellertz.acceptance=<deployment_id>` and
`cellertz.acceptance_check=container_configuration`. Record stdout as
the container id. Do not start it for this check.

Evidence: `docker inspect <recorded-id>`. `Id` is the recorded id.
`Image` is `image_id`. The acceptance label is `deployment_id`.
`HostConfig.Memory` equals `memory_mib` times 1024 times 1024.
`NanoCpus` equals `cpu_count` times 1000000000. `StorageOpt.size`
equals `storage_bytes`. `LogConfig` is json-file with `max-size` equal
to `log_bytes` and `max-file` equal to `1`. `NetworkMode` is `none`.
Privileged is false. Restart policy is `no`. `DeviceRequests` has
`DeviceIDs` `[gpu_device]` and `Capabilities` `[["gpu"]]`. Those device
and memory fields are configuration. They are not a VRAM quota and they
are not enforcement.

Pass: every inspected field matches the operator document.

Failure: do not start a second container.

Cleanup: `docker inspect <recorded-id>` must still show that id, that
image id, and that acceptance label. Only then
`docker rm --force <recorded-id>`. Never remove a container because its
name matches.

### CPU and memory enforcement

Purpose: show the cgroup cap and the memory cap acting on a workload.

Image capability: one operator-selected command that occupies more than
the configured CPU count, and one that allocates more than
`memory_mib`. This repository does not supply those commands.

Prerequisite: `docker inspect cellertz-accept-<deployment_id>-cpu`
exits non-zero. If the name exists, stop without removing it.

Command: the same `docker create` flags, name
`cellertz-accept-<deployment_id>-cpu`, label
`cellertz.acceptance_check=cpu`, and the operator workload as argv.
Record the container id. `docker start <recorded-id>`.

Evidence for CPU: the host cgroup `cpu.stat` for that container id
shows `throttled_usec` or `nr_throttled` increasing while the workload
runs. Evidence for memory: a second container, name suffix `mem`, whose
workload allocates more than `memory_mib`. `docker inspect
<recorded-id> --format '{{.State.OOMKilled}} {{.State.ExitCode}}'`
shows OOMKilled true or exit code 137. `HostConfig.Memory` and
`NanoCpus` are not this evidence.

Pass: the busy workload is throttled, and the over-limit allocation
does not exit 0.

Failure: stop only the recorded id after the identity check below.

Cleanup: the same recorded-id removal as container configuration, for
each id this check created.

### Storage limit during writes

Purpose: show the overlay quota rejecting an in-container write.

Image capability: a command that writes `storage_bytes + 1` bytes to a
path outside `/cellertz/input` and then stops. A write of
`storage_bytes - 1` may succeed.

Prerequisite: the name `cellertz-accept-<deployment_id>-storage` is
absent. Do not remove an existing container of that name.

Command: create and start one container with the shared flags, that
name, label `cellertz.acceptance_check=storage`, and the operator write
command. Record the id.

Evidence: the over-limit write exits non-zero. `StorageOpt` in
`docker inspect` is not this evidence. Do not fill the host disk
outside the quota: the workload stops at one byte past the limit.

Pass: the bounded over-limit write fails.

Failure: stop that recorded id.

Cleanup: recorded-id removal after the identity check.

### Bounded log retention

Purpose: show json-file retention dropping stdout past `log_bytes`.

Image capability: a command that writes `log_bytes + 1` bytes to stdout
and exits. The acceptance report records the byte count only.

Prerequisite: the name `cellertz-accept-<deployment_id>-logs` is
absent.

Command: create and start with label `cellertz.acceptance_check=logs`
and that command. Record the id.

Evidence: `docker logs <recorded-id>` has length at most `log_bytes`.
Do not paste the log bytes into the acceptance record.

Pass: retained stdout is within `log_bytes` and the log option
`max-file` is `1`.

Failure: stop that recorded id.

Cleanup: recorded-id removal after the identity check.

### Read-only staged input

Purpose: show that `/cellertz/input` rejects a write.

Image capability: a command that tries to create
`/cellertz/input/cellertz-accept-probe` and exits. The acceptance
input directory must contain no scientific inputs. Compare names, not
file bytes.

Prerequisite: the name `cellertz-accept-<deployment_id>-mount` is
absent. Record the host directory names before start.

Command: create and start with label `cellertz.acceptance_check=mount`
and that command. The mount source is `acceptance_input_dir`.

Evidence: the command exits non-zero, and the host directory listing
does not gain `cellertz-accept-probe`.

Pass: the write is denied and the host names are unchanged.

Failure: stop that recorded id. Leave the input directory in place.

Cleanup: recorded-id removal after the identity check. Do not delete
the input directory.

### GPU visibility

Purpose: show the device the container can see.

Image capability: a command that prints one device id. Do not assume
`nvidia-smi` or any other named binary. The printed value is the
evidence. A memory total is not evidence, and this check is not a VRAM
quota.

Prerequisite: the name `cellertz-accept-<deployment_id>-gpu` is absent.

Command: create and start with `--gpus device=<gpu_device>`, label
`cellertz.acceptance_check=gpu`, and the operator command. Record the
id.

Evidence: the workload's single id token equals `gpu_device`.

Pass: the visible id is the assigned device.

Failure: stop that recorded id. Do not record a memory quantity as
success.

Cleanup: recorded-id removal after the identity check.

### systemd manager, lingering, and state

Purpose: prove the deadline commands can run before a timer exists.

Commands, in order, and only through `--execute` or an operator choice:

- `systemctl --version`
- `systemd-run --version`
- `docker --version`
- for `user` only, `loginctl show-user <uid> -p Linger`
- `<deadline_interpreter> -c` import of
  `cellertz.application.pdl1_host_deadline`, requiring
  `MECHANISM=systemd-transient-timer`
- `systemctl show --property=Version` (`--user` when the manager is
  `user`)

The state-directory check creates and removes
`.cellertz-deadline-access` in `deadline_state_dir`. The interpreter
must be a nonempty executable regular file.

Pass: each command that ran returned its expected line. `systemctl
show` starts with `Version=` and the version has no whitespace. The
import that ran exits 0. The access file is gone. User mode that ran
prints `Linger=yes`. System mode does not query `loginctl` and that
check stays `not_run`.

An invalid interpreter, a failed version command, a failed linger
query, or a failed import leaves every command after that point
`not_run`. A command that did not run is not a pass.

Failure: stop. Do not install a timer.

Cleanup: remove the access file if it remains. No unit is installed by
the helper.

### Deadline after the worker stops

Purpose: a paused or exited driver must not be required for the
deadline to finish, and a later start must not run.

This check is manual. It uses `SystemdHostDeadline.install` and
`guarded_start`. Do not assemble a `systemd-run` command by hand, and
do not call scientific authorization or `bind`.

Image capability: the acceptance `entrypoint` and `argv` keep a process
running until the deadline or until enforcement stops it.

Isolated state: `deadline_state_dir` is an empty directory created for
this acceptance. It is not the scientific deployment's state directory.
Record its file names before install. Do not delete unrelated files
there. Leave `CELLERTZ_SCIENTIFIC_RUN_EXECUTION` disabled.

Prerequisite: `docker inspect cellertz-accept-<deployment_id>-deadline`
exits non-zero. Record `docker info --format '{{.ID}}'` and require it
to match the daemon id from the observation report.

Commands:

1. `docker create` with the shared flags, that name, label
   `cellertz.acceptance_check=deadline`, and the long-running benign
   argv. Record stdout as the container id.
2. `docker inspect <recorded-id>` and require `Id`, `Image`, and
   `cellertz.acceptance` to match this invocation. If they do not,
   stop without removing anything.
3. Load the same acceptance document and call
   `drive_acceptance_deadline(document, container_id=<recorded-id>,
   daemon_id=<daemon-id>, deadline_epoch=<epoch>)`. Use a deadline
   about 30 seconds ahead. The driver builds `DockerExecutionConfig`
   from that document, calls `SystemdHostDeadline.install` with
   `DeadlineClaim` for those recorded ids, then `guarded_start`. It
   does not assemble `systemd-run` and it does not call scientific
   authorization.
4. The driver prints `cellertz-acceptance-running <recorded-id>` and
   then sleeps. Leave that process alive. The systemd timer is a
   different process, so terminating the driver does not cancel
   enforcement.
5. The new file in `deadline_state_dir` is the only state file this
   step may read. Its `container_id`, `daemon_id`, and `image_id` must
   match. Its `unit` names the timer.
6. `systemctl show <unit>.timer` for `Id`, `ActiveState`, and `Result`,
   and `systemctl show <unit>.service` for `ExecStart`. Use `--user`
   when `deadline_manager` is `user`.
7. Terminate the driver process. Wait until `deadline_epoch`. Inspect
   the recorded id again. A later process may call `recover` as the
   deadline recovery check. This driver does not call `recover`.

Pass: before the deadline, `ExecStart` `argv[]` is the interpreter,
`-m`, `cellertz.application.pdl1_host_deadline`, `enforce`, `--state`,
and that state file. `ActiveState` is `waiting` or `active`. After the
driver is gone and the deadline has passed, the same container id is
exited or absent and `Image` is still `image_id`. The state phase is
`enforced`. A kill or inspect error stays `uncertain` or `failed`.
The terminated driver is not required for that result, and it must not
call `docker start` after enforcement.

Failure: do not mark the attempt enforced and do not create another
container.

Cleanup: `systemctl stop` that unit only when `ExecStart` still names
this state file and the state file names this container id. Then
`docker rm --force <recorded-id>` only after the identity check. Do not
stop any other unit.

### Deadline recovery

Purpose: a new process calls `SystemdHostDeadline.recover` for the
recorded container id and daemon id. This check does not validate the
scientific executor's external-attempt store, terminal commit, or retry
reconciliation. Those integration checks remain pending unless they are
exercised through the executor's existing path.

Prerequisite: the deadline check left its state file and recorded
container id. Do not create another container first.

Commands, in a new process that does not import authorization services:

1. Build the same `SystemdHostDeadline` on the acceptance state
   directory.
2. Call `recover(container_id, daemon_id)`.
3. `docker ps -aq --no-trunc --filter label=cellertz.acceptance=<deployment_id>`.
4. Call `recover` once more.

Pass: neither call runs `docker create`. The label filter lists only
container ids this invocation recorded. A confirmed exit may make the
first `recover` return `running`, which means that container is still
the one already recorded. A container that is still running, a missing
timer, a failed unit, or a daemon or image mismatch stays fenced.
Passing this check does not mean an external attempt was committed or
retried.

Failure: stop the recorded container after the identity check. Do not
queue a scientific run.

Cleanup: the deadline check's unit and recorded container id only.

## Remaining limitations

Host acceptance does not remove these code limits.

Same-uid staging replacement. Staged inputs are checked for owner and
mode when they are written. Another process running as the same user can
replace those bytes before the container reads them. A benign mount
check does not close that window. Treat the staged directory as trusted
only for the service user, and do not share it with other workloads.

Readiness manifest symlink. The deployment file is opened without
following a symlink. The readiness manifest reader uses `Path.stat` and
`Path.read_bytes`, which follow a final symlink. Replacing the manifest
path with a symlink can change the manifest that readiness re-reads.
Keep that path as a regular file owned by the service, and do not treat
a passed readiness snapshot as proof that the path was not a symlink.

Unpublished design CSV. A scientific attempt can leave
`design_records.csv` on disk under the attempt directory. That file is
not a published artifact and is not scientific validation. Host
acceptance must not promote it, rank it, or ingest it into memory.
