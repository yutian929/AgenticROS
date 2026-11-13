# ROSBody — Codex + ROS inside Docker

A simulation-first robot agent. Codex and ROS 2 Jazzy run inside an isolated development
container. A separate MuJoCo container exposes **raw joint servos and sensors**. The agent
must discover the body, implement the standard adapter, verify it, then execute text tasks.

```text
Mac terminal                              Mac browser
    │                                      │ localhost:8080
    ▼                                      ▼
agent container ── ROS 2 / UDP ──► body container
  Codex + ROS + build tools        MuJoCo physics + raw driver
  interface contract               joint commands / sensors
  body documentation               no standard move/grip API
  generated adapter + task code
    ▲                                      │
    └──────── read-only observations ───────┘
```

The agent image does not contain the simulator, old Robot client, reference adapter,
color detector, grasp script or evaluator. The host repository and Docker socket are not
mounted. Only body documentation, driver message definitions and the target API contract
are supplied. This separates the filesystem; it is not protection against a malicious
agent exploiting the kernel, network or driver. Only simulation is authorized.

## Run on an Apple Silicon Mac

Install and start Docker Desktop. Both services run Linux/ARM64 by default.

```bash
docker compose build
docker compose run --rm agent codex login --device-auth
bash scripts/docker-chat.sh
```

Authenticate using the URL and code printed by Codex. Credentials stay in a dedicated
Docker volume; host Codex credentials are neither copied nor mounted. API-key login is
also supported by Codex's `login --with-api-key` via stdin; never put keys into project files.
The standard Codex terminal UI shows commands, tool results and conversation.
Open http://localhost:8080 for the live camera, then tell Codex:

```text
适配一下你的身体
把红色方块夹取到红色盘子上
```

The first instruction should produce `/workspace/adapter/body.py` and verification evidence.
There is deliberately no prebuilt Cartesian adapter. The task agent may write and launch
ROS nodes, compile packages and install Python packages in a workspace virtual environment.
System-wide dependencies belong in the agent Dockerfile. Real-time servos remain in the driver.

## Configuration and persistence

Edit `docker/agent/codex.toml` to select model and reasoning effort. It is mounted read-only
and copied into the container's Codex home at startup. Example defaults:

```toml
model = "gpt-6-astra"
model_reasoning_effort = "medium"
```

`BODY_SEED=3 bash scripts/docker-chat.sh` chooses the initial scene seed when starting a
new body. The chat launcher preserves an already running body, even if source or seed changes.
To deliberately apply new body code or a seed, stop controlling agents, then run
`BODY_SEED=3 docker compose up -d --build --force-recreate --wait body`. Driver restart produces a new session ID and resets physical
state. Saved code and chat history do not restore that state: reobserve before acting.
`ROSBODY_VIEWER_PORT=8081` changes the host camera port.

Docker named volumes preserve `/workspace` (adapter/programs/evidence), Codex authentication
and conversations, and public observations. To resume a conversation:

```bash
bash scripts/docker-chat.sh resume --last
```

Exiting Codex leaves the body running, so subsequent agent sessions can share its current
state. Only run one controlling agent at a time. Stop both services with `docker compose down`;
this preserves named volumes. Do not use `down -v` unless deliberately erasing all saved data,
including credentials and generated adapters.

## What Codex receives

| File | Purpose |
|---|---|
| `docker/agent/contract.md` | Required `Body` API, units, receipts and recovery semantics |
| `docker/agent/body.md` | Raw endpoints, actuator order, geometry and limits |
| `docker/agent/skills/setup-body/SKILL.md` | Discover → implement → verify → record |
| `docker/interfaces/` | Raw ROS action/service IDL, no implementation |

Generated copies live in `/workspace`, along with the generated adapter. Raw driver and
private scene files live only in the body image. Observations mount read-only in the agent.
DDS is configured to use UDP, avoiding cross-container shared-memory dependencies. Both
containers share a Compose bridge; no host ROS installation is needed.

## Validation

Run the container integration check after building and starting the body:

```bash
docker compose up -d --wait body
python3 scripts/audit_agent_image.py
python3 scripts/check_docker.py
```

This checks image/mount isolation, raw ROS discovery, sensor access and invalid-goal rejection.
It does not provide an adapter to the agent. A successful autonomous adaptation and pick/place
trial is a separate acceptance test; do not substitute a prewritten adapter for that test.

The earlier native macOS prototype remains on `codex/macos-minimal-sim` and
`archive/macos-prototype-20261113`. Its source is retained in this checkout for regression
reference, but is excluded from Docker build inputs by `.dockerignore` and not used at runtime.
See `docs/macos-prototype.md` for that version. `pixi run chat` still invokes the old native
prototype; **use `bash scripts/docker-chat.sh` for this Docker rewrite**.

### Separate acceptance stages

`python3 scripts/check_docker.py --motion` additionally checks a small raw joint motion,
exclusive admission, active UUID, cancellation acknowledgment, terminal stop confirmation,
and ordinary servo completion. These are driver tests, not autonomous adaptation.
Only run motion checks when no agent task is active.

After authenticating, run the autonomous acceptance harness:

```bash
python3 scripts/docker-trial.py
```

It creates a **new named workspace volume**, verifies the adapter directory is empty,
and gives container Codex exactly the two user instructions above, in sequence. It supplies
no code solution or task coordinates. It records JSONL commands/results under `runs/`, keeps
the trial container and workspace, and never automatically retries a failed stage. Both calls
use the editable model configuration. Inspect generated code, image-view events, manifest and
test evidence; a Codex exit code alone is not a passing adaptation test. Do not run this beside
another controlling agent. The body is not reset by this script. The trial workspace is separate from the default
interactive workspace. Continue using its generated adapter with the printed container name:

```bash
docker exec -it TRIAL_CONTAINER /usr/local/bin/agent-entrypoint codex resume --last
```

Do not run `docker-chat.sh` concurrently with that trial; its default workspace is intentionally
separate. Generated trial code and evidence remain in the trial's named volume.

Independent task evaluation is available only to the maintainer:

```bash
bash scripts/docker-evaluate.sh
```

It uses a Unix socket inside the body container, not a ROS service, HTTP endpoint, shared
observation file or agent mount. Success requires the entire red-block footprint inside the
red plate, bottom on the plate surface (3 mm tolerance), low velocity, no finger contact,
and an idle driver. The simulation advances during commands and holds otherwise; this is
an instantaneous simulated-state check, not a real-world robustness claim. Never feed this
result back to the task-performing agent. The browser and ROS snapshots contain only sensors.

Actual results and blockers are recorded in `docs/docker-validation.md`.

## Lifecycle, state and isolation limits

Each body process start generates a new session ID. Every raw goal must include that ID;
stale-session goals are rejected. Snapshots contain the session and acquisition timestamp.
`/driver/status` exposes the active UUID and terminal receipts for reconciliation. Unknown
or missing receipts after timeout must not cause an automatic replay. Cancel acknowledgment
is only acknowledgment: wait for a terminal receipt with `stop_confirmed` before proceeding.
Receipt history is in memory for the current session; the agent saves durable evidence.

Codex uses `sandbox_mode = "danger-full-access"` **inside the container**: the default
Docker security profile rejects its nested bwrap namespace creation. This does not grant
host filesystem access or root privileges. The agent runs as uid 1001 without capabilities
or the Docker socket. It has network access
for Codex and package installation; the ROS bridge is **not an adversarial security boundary**.
DDS is unauthenticated, and any peer on that bridge can issue raw commands. The browser is
bound to Mac loopback. The setup separates files and processes but does not claim protection
against all malicious code, kernel exploits, network access or resource exhaustion. Use only
trusted simulation sessions. Only the four documented mounts are available to the agent.

Exiting a client does not cancel an already accepted raw goal. Reconcile its UUID or
cancel and verify its terminal stop receipt before handing control to another client.
Stop the interactive agent by exiting Codex. Stop the simulation and Compose services with
`docker compose down` (without `-v`). Trial containers have separate names printed by the
harness: stop those with `docker stop NAME`; keep their named volumes. Before intentionally
restarting a body, stop agent control and expect a new session and scene. Changing BODY_SEED
is a physical reset, not a continuation of the previous task.

Authentication uses container-local device login, following the official
[Codex authentication documentation](https://learn.chatgpt.com/docs/auth).
Model and effort keys follow the official
[configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).
No host credentials are imported. If device login is unavailable for your account, use the
CLI's `login --with-api-key` via stdin in your own terminal; do not send secrets in chat.
