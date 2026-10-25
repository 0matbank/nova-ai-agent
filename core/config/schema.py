"""Typed schema for every config/*.yaml file (plan §17G).

Every model forbids unknown keys so typos fail loudly at startup. Safety rules
from the plan that must never be configured away are enforced here too.
"""

from __future__ import annotations

import ipaddress
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Percent = Annotated[int, Field(ge=1, le=100)]
Priority = Annotated[int, Field(ge=0, le=100)]
PositiveInt = Annotated[int, Field(gt=0)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------- default.yaml

class AgentSection(Strict):
    name: str
    timezone: str
    languages: list[str]

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"unknown timezone {v!r}") from None
        return v


class PathsSection(Strict):
    root: Path | None = None
    data_dir: Path
    database: Path
    secrets_dir: Path
    sessions_dir: Path
    workspace_dir: Path
    downloads_dir: Path
    logs_dir: Path
    backups_dir: Path
    local_models_dir: Path


LOG_CATEGORIES = ("core", "desktop", "browser", "provider", "tasks", "audit")


class RotationSection(Strict):
    max_bytes: Annotated[int, Field(ge=1_048_576)]
    backup_count: Annotated[int, Field(ge=1)]


class LoggingSection(Strict):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]
    format: Literal["json"]
    rotation: RotationSection
    categories: list[str]

    @field_validator("categories")
    @classmethod
    def _all_categories(cls, v: list[str]) -> list[str]:
        missing = set(LOG_CATEGORIES) - set(v)
        if missing:
            raise ValueError(f"required log categories missing: {sorted(missing)}")
        return v


# §28 — these may never be throttled.
EXEMPT_MESSAGE_TYPES = (
    "approval_required",
    "user_input_required",
    "critical_security_alert",
    "task_completed",
    "task_failed_permanently",
    "agent_going_offline",
    "recovery_after_restart",
)


class NotificationThrottle(Strict):
    min_progress_interval_seconds: PositiveInt
    soft_max_progress_updates_per_task: PositiveInt
    repeated_error_cooldown_seconds: PositiveInt
    batch_small_results: bool
    exempt_message_types: list[str]

    @field_validator("exempt_message_types")
    @classmethod
    def _critical_always_exempt(cls, v: list[str]) -> list[str]:
        missing = set(EXEMPT_MESSAGE_TYPES) - set(v)
        if missing:
            raise ValueError(f"critical message types must stay exempt: {sorted(missing)}")
        return v


class ShutdownSection(Strict):
    graceful_timeout_seconds: PositiveInt
    worker_kill_after_seconds: PositiveInt

    @model_validator(mode="after")
    def _kill_within_graceful(self) -> ShutdownSection:
        if self.worker_kill_after_seconds > self.graceful_timeout_seconds:
            raise ValueError("worker_kill_after_seconds must be <= graceful_timeout_seconds")
        return self


class TaskEngineSection(Strict):
    max_retries: Annotated[int, Field(ge=0, le=10)]
    idle_poll_seconds: PositiveInt
    list_limit: Annotated[int, Field(ge=1, le=50)]


class TTSSection(Strict):
    enabled: bool = False
    engine: Literal["edge", "gemini"] = "edge"
    gemini_voice: str = "Kore"
    gemini_style: str = ""
    daily_char_budget: Annotated[int, Field(ge=0, le=1_000_000)] = 20000
    voices: dict[str, str] = {}
    max_chars: Annotated[int, Field(ge=50, le=5000)] = 1200
    rate: Annotated[str, Field(pattern=r"^[+-]\d{1,2}%$")] = "+0%"


class PushToTalkSection(Strict):
    """V1.2: hold a shortcut on the PC and speak (the microphone is open only while held)."""
    enabled: bool = False
    hotkey: Annotated[str, Field(pattern=r"^((ctrl|control|alt|shift|win)\+)*[a-z0-9]+$")] = (
        "ctrl+alt+space")
    max_seconds: Annotated[float, Field(ge=3, le=120)] = 60
    min_seconds: Annotated[float, Field(ge=0.1, le=5)] = 0.4
    cue: bool = True                 # a short tone when listening starts and stops
    # V1.2 dictation: hold THIS shortcut and speak — the words are typed at the cursor of
    # the focused field (None = off)
    dictation_hotkey: Annotated[str | None, Field(
        pattern=r"^((ctrl|control|alt|shift|win)\+)*[a-z0-9]+$")] = None
    speak_on_pc: bool = True         # the spoken answer plays on the PC speakers


class VoiceSection(Strict):
    max_duration_seconds: Annotated[int, Field(ge=5, le=1800)]
    confirm_below_confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    confirm_below_confidence_safe: Annotated[float, Field(ge=0.0, le=1.0)] = 0.5
    # V1.2: below this Whisper has not understood anything (noise, a tap of the
    # push-to-talk key) — ask again instead of reading made-up words aloud. Measured
    # 2026-10-25 on 37 real voice notes: real speech ≥ 0.62, made-up text 0.24 and 0.00.
    not_understood_below: Annotated[float, Field(ge=0.0, le=1.0)] = 0.4
    confirm_timeout_seconds: Annotated[int, Field(ge=30, le=3600)]
    keep_audio: bool
    beam_size: Annotated[int, Field(ge=1, le=10)]
    vad_speech_pad_ms: Annotated[int, Field(ge=0, le=2000)] = 400
    hint_words: list[str] = []
    # V1.1: give Whisper this many installed app names as hotwords (0 = off)
    app_hotwords: Annotated[int, Field(ge=0, le=80)] = 0
    tts: TTSSection = TTSSection()
    ptt: PushToTalkSection = PushToTalkSection()


class WhatsAppSection(Strict):
    """Phase 21: secondary channel on the official WhatsApp Cloud API."""
    enabled: bool = False
    graph_version: str = "v21.0"
    webhook_port: Annotated[int, Field(ge=1024, le=65535)] = 47803
    # quick = Cloudflare quick tunnel (new address each start, re-registered with Meta);
    # fixed = public_url points at this webhook already (own named tunnel)
    tunnel: Literal["quick", "fixed"] = "quick"
    public_url: str | None = None
    cloudflared: str = "tools/cloudflared/cloudflared.exe"     # relative to the root


class UpdateSection(Strict):
    """Phase 23: /update installs commits from the owner's repo (fast-forward only)."""
    remote: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]{1,40}$")] = "origin"
    branch: Annotated[str, Field(pattern=r"^[A-Za-z0-9_./-]{1,80}$")] = "main"
    # the new version must come up (core health file) within this time, else rollback
    health_timeout_seconds: Annotated[int, Field(ge=20, le=900)] = 180
    stable_seconds: Annotated[int, Field(ge=5, le=300)] = 20
    run_unit_tests: bool = True          # preflight: tests/unit with the new code


class DashboardSection(Strict):
    """Phase 24 (plan §40): local-only web dashboard inside core."""
    enabled: bool = True
    port: Annotated[int, Field(ge=1024, le=65535)] = 47810


class DefaultConfig(Strict):
    agent: AgentSection
    paths: PathsSection
    logging: LoggingSection
    notification_throttle: NotificationThrottle
    task_engine: TaskEngineSection
    voice: VoiceSection
    shutdown: ShutdownSection
    whatsapp: WhatsAppSection = WhatsAppSection()
    update: UpdateSection = UpdateSection()
    dashboard: DashboardSection = DashboardSection()


# -------------------------------------------------------------- providers.yaml

REQUIRED_PROVIDERS = (
    "openai_codex", "google_antigravity", "gemini_api", "anthropic_claude", "ollama_local",
)


class ProviderEntry(Strict):
    enabled: bool | Literal["optional"]
    mode: Literal["subscription", "api", "subscription_or_api", "local"]
    model_alias_set: str
    priority: dict[str, Priority]
    # An API provider used only through a free-tier key: routable even while paid
    # API usage is off (budget guard). Quota errors fall back to the next provider.
    free_tier_only: bool = False


class CircuitBreakerSection(Strict):
    transient_failure_threshold: PositiveInt
    cooldown_backoff_minutes: Annotated[list[PositiveInt], Field(min_length=1)]


class RetrySection(Strict):
    timeout_retries: Annotated[int, Field(ge=0, le=5)]
    server_error_retries: Annotated[int, Field(ge=0, le=5)]


class BudgetGuard(Strict):
    allow_paid_api_usage: bool


class TaskBudget(Strict):
    """Per task type usage budget (plan §58 Token / Context Control)."""
    timeout_seconds: PositiveInt
    max_output_tokens: PositiveInt


class ProvidersConfig(Strict):
    providers: dict[str, ProviderEntry]
    circuit_breaker: CircuitBreakerSection
    retry: RetrySection
    budget_guard: BudgetGuard
    # task type → budget; when set it replaces the call site's own limits
    task_budgets: dict[str, TaskBudget] = {}
    # CU-2 (plan §72): where a request carrying screenshots/images may go.
    # local_first = this PC's model first, cloud only as fallback (owner 2026-09-25);
    # local_only = never leaves this PC, enforced by the router (not just ranking).
    screenshot_privacy: Literal["local_first", "local_only"] = "local_first"

    @field_validator("providers")
    @classmethod
    def _all_adapters_present(cls, v: dict[str, ProviderEntry]) -> dict[str, ProviderEntry]:
        missing = set(REQUIRED_PROVIDERS) - set(v)
        if missing:
            raise ValueError(f"provider entries missing: {sorted(missing)}")
        return v


# ----------------------------------------------------------------- models.yaml

class OllamaPolicy(Strict):
    default_alias: str
    on_demand_aliases: list[str]
    max_heavy_models_loaded: Annotated[int, Field(ge=1, le=2)]
    idle_unload_seconds: PositiveInt
    # keep the default (light) model in GPU memory instead of unloading it when idle
    keep_default_loaded: bool = False
    # Only one LLM in GPU memory at a time (owner 2026-10-15, VRAM hit 98% with qwen3 +
    # qwen3-vl): a heavy model replaces the default while it works; after
    # `heavy_idle_seconds` without heavy work the default comes back.
    exclusive_vram: bool = False
    heavy_idle_seconds: PositiveInt = 60
    think_task_types: list[str] = []
    # Context window per request. Ollama's own default (4096) silently cuts longer
    # prompts such as page snapshots.
    num_ctx: Annotated[int, Field(ge=2048, le=131072)] = 16384


class WhisperConfig(Strict):
    model: str
    path: Path
    device: Literal["cuda", "cpu"]
    compute_type: Literal["float16", "int8_float16", "int8", "float32"]


class UiTarsConfig(Strict):
    server: Path = Path("models/llama.cpp/llama-server.exe")      # relative to the root
    model: Path = Path("models/ui-tars/UI-TARS-1.5-7B.Q4_K_M.gguf")
    mmproj: Path = Path("models/ui-tars/UI-TARS-1.5-7B.mmproj-f16.gguf")
    port: Annotated[int, Field(ge=1024, le=65535)] = 8091
    idle_unload_seconds: Annotated[int, Field(ge=10)] = 120


class GroundingConfig(Strict):
    # "vision" = the existing qwen3-vl path (default); "ui-tars" = UI-TARS first, then it
    engine: Literal["vision", "ui-tars"] = "vision"
    ui_tars: UiTarsConfig = Field(default_factory=UiTarsConfig)


class ModelsConfig(Strict):
    alias_sets: dict[str, dict[str, str | None]]
    ollama_policy: OllamaPolicy
    whisper: WhisperConfig
    grounding: GroundingConfig = Field(default_factory=GroundingConfig)

    @model_validator(mode="before")
    @classmethod
    def _split(cls, data: object) -> object:
        # YAML keeps alias sets at top level (plan §9A); fold them into alias_sets.
        if isinstance(data, dict) and "alias_sets" not in data:
            data = dict(data)
            policy = data.pop("ollama_policy", None)
            whisper = data.pop("whisper", None)
            grounding = data.pop("grounding", None) or {}
            return {"alias_sets": data, "ollama_policy": policy, "whisper": whisper,
                    "grounding": grounding}
        return data

    @model_validator(mode="after")
    def _policy_refers_to_aliases(self) -> ModelsConfig:
        ollama = self.alias_sets.get("ollama_models")
        if not ollama:
            raise ValueError("ollama_models alias set is required")
        wanted = {self.ollama_policy.default_alias, *self.ollama_policy.on_demand_aliases}
        unknown = wanted - set(ollama)
        if unknown:
            raise ValueError(f"ollama_policy refers to unknown aliases: {sorted(unknown)}")
        return self


# ----------------------------------------------------------------- agents.yaml

class AgentsRuntime(Strict):
    max_active_agents_per_task: Annotated[int, Field(ge=1, le=5)]


class AgentEntry(Strict):
    enabled: bool
    kind: Literal["reasoning"]
    task_types: list[str]
    # the kind of AI work — the provider router picks the provider (plan §8.8, §8.11)
    provider_task_type: str | None = None
    # the only skills this agent may call: "skill" or "skill.tool" (plan §11)
    skills: list[str] = []


class ReviewPolicy(Strict):
    """Plan §8.10 cross-provider review: which coding tasks get a read-only second
    opinion from a DIFFERENT provider before the commit (Phase 18)."""
    enabled: bool = True
    min_changed_lines: PositiveInt = 150          # large refactor
    min_files: PositiveInt = 5
    roles: list[str] = ["security", "devops", "backend_architect"]
    # production code, security-sensitive change, deployment config, important logic,
    # or the owner asking for it
    keywords: list[str] = ["production", "security", "secure", "refactor", "deploy",
                           "release", "critical", "important", "payment", "auth",
                           "review", "double check", "second opinion", "ভালো করে দেখ"]
    path_patterns: list[str] = ["Dockerfile", "docker-compose*", ".github/workflows/*",
                                "*.tf", "deploy*", "*.env.example", "*auth*", "*security*"]
    max_fix_rounds: Annotated[int, Field(ge=0, le=3)] = 1


class AgentsConfig(Strict):
    runtime: AgentsRuntime
    agents: dict[str, AgentEntry]
    project_agents: list[str]
    review: ReviewPolicy = ReviewPolicy()

    @field_validator("agents")
    @classmethod
    def _core_agents_enabled(cls, v: dict[str, AgentEntry]) -> dict[str, AgentEntry]:
        # A task is never complete without the Verifier (plan §30, §47).
        for name in ("master_orchestrator", "verifier"):
            if name not in v or not v[name].enabled:
                raise ValueError(f"agent '{name}' must exist and be enabled")
        return v


# ----------------------------------------------------------------- skills.yaml

class SkillEntry(Strict):
    status: Literal["planned", "enabled", "disabled"]


class SkillsConfig(Strict):
    skills: dict[str, SkillEntry]


# ------------------------------------------------------------ permissions.yaml

class Level(StrEnum):
    GREEN = "GREEN"
    BLUE = "BLUE"
    YELLOW = "YELLOW"
    RED = "RED"


# Plan §18 RED examples (+ §34 path-wide delete) — never downgradable by config.
MANDATORY_RED = (
    "file.delete_important", "file.delete_pathwide", "git.push_production",
    "message.send_external",
    "system.shutdown", "system.restart", "account.security_change", "admin.destructive",
)


class ApprovalSection(Strict):
    timeout_seconds: Annotated[int, Field(ge=30, le=3600)]
    single_use: Literal[True]
    require_totp_for: list[str]

    @field_validator("require_totp_for")
    @classmethod
    def _totp_not_implemented(cls, v: list[str]) -> list[str]:
        # Optional §17D feature, not built yet. Refuse rather than silently skip it.
        if v:
            raise ValueError("TOTP confirmation is not implemented yet; keep this list empty")
        return v


class PermissionsConfig(Strict):
    default_level: Literal[Level.RED]
    approval: ApprovalSection
    actions: dict[str, Level]

    @field_validator("actions")
    @classmethod
    def _red_stays_red(cls, v: dict[str, Level]) -> dict[str, Level]:
        bad = [a for a in MANDATORY_RED if v.get(a, Level.RED) is not Level.RED]
        if bad:
            raise ValueError(f"these actions must stay RED: {bad}")
        return v


# ---------------------------------------------------------------- workers.yaml

class HttpWorker(Strict):
    enabled: bool
    transport: Literal["http"]
    host: str
    port: Annotated[int, Field(ge=1024, le=65535)]

    @field_validator("host")
    @classmethod
    def _loopback_only(cls, v: str) -> str:
        try:
            ok = ipaddress.ip_address(v).is_loopback
        except ValueError:
            ok = False
        if not ok:
            raise ValueError(f"internal worker RPC must bind to a loopback IP, got {v!r}")
        return v


class CoreServiceWorker(Strict):
    enabled: bool


_SERVICE_NAME = r"^[A-Za-z0-9_.-]{1,80}$"
_TASK_NAME = r"^[a-z0-9_]{1,40}$"


class BrokerWorker(Strict):
    enabled: bool
    transport: Literal["named_pipe"]   # never a localhost admin HTTP endpoint (§17A)
    pipe_name: str
    # Phase 22 policy — copied into the protected install by scripts/broker_install.py;
    # editing it changes nothing until the owner reinstalls (UAC).
    install_dir: str = r"C:\Program Files\Nova AI\broker"
    scheduled_task: str = r"\Nova AI\Broker"
    approval_max_age_seconds: Annotated[int, Field(ge=60, le=3600)] = 900
    services: list[Annotated[str, Field(pattern=_SERVICE_NAME)]] = Field(default_factory=list)
    # name → fixed argv; %SystemRoot% / %ProgramFiles% are resolved by the installer
    tasks: dict[Annotated[str, Field(pattern=_TASK_NAME)], list[str]] = Field(
        default_factory=dict)
    packages: bool = False             # winget install/uninstall (each one asks the owner)

    @field_validator("pipe_name")
    @classmethod
    def _is_pipe(cls, v: str) -> str:
        if not v.startswith("\\\\.\\pipe\\"):
            raise ValueError("pipe_name must start with \\\\.\\pipe\\")
        return v

    @field_validator("tasks")
    @classmethod
    def _fixed_programs(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        for name, argv in v.items():
            exe = argv[0] if argv else ""
            if not exe.lower().startswith(("%systemroot%\\", "%programfiles%\\")):
                raise ValueError(f"task {name}: the program must live under %SystemRoot% or "
                                 "%ProgramFiles% (admin-only folders)")
        return v


class WorkersSection(Strict):
    core_service: CoreServiceWorker
    desktop_worker: HttpWorker
    browser_worker: HttpWorker
    privileged_broker: BrokerWorker


class WorkersConfig(Strict):
    protocol_version: Annotated[int, Field(ge=1)]
    workers: WorkersSection

    @model_validator(mode="after")
    def _unique_ports(self) -> WorkersConfig:
        if self.workers.desktop_worker.port == self.workers.browser_worker.port:
            raise ValueError("desktop_worker and browser_worker must use different ports")
        return self


# -------------------------------------------------------------- resources.yaml

class RamThresholds(Strict):
    moderate: Percent
    high: Percent
    critical: Percent

    @model_validator(mode="after")
    def _ordered(self) -> RamThresholds:
        if not self.moderate < self.high < self.critical:
            raise ValueError("ram thresholds must satisfy moderate < high < critical")
        return self


class VramThresholds(Strict):
    high: Percent
    critical: Percent

    @model_validator(mode="after")
    def _ordered(self) -> VramThresholds:
        if not self.high < self.critical:
            raise ValueError("vram thresholds must satisfy high < critical")
        return self


class IdleUnload(Strict):
    whisper: PositiveInt
    ollama: PositiveInt


class ResourceActions(Strict):
    high: list[str]
    critical: list[str]


class ResourcesConfig(Strict):
    monitor_interval_seconds: PositiveInt
    ram_percent: RamThresholds
    vram_percent: VramThresholds
    disk_free_gb_min: PositiveInt
    idle_unload_seconds: IdleUnload
    actions: ResourceActions


# -------------------------------------------------------------- schedules.yaml

class SchedulesConfig(Strict):
    schedules: list[dict[str, object]]


# ------------------------------------------------------------ projects/*.yaml

class ProjectProfile(Strict):
    name: str
    local_folder: str | None = None
    repository: str | None = None
    site_url: str | None = None
    build_command: str | None = None
    test_command: str | None = None
    deploy_method: str | None = None
    knowledge_dir: str | None = None
    approval_required_for: list[str] = []
    # Phase 16 project specialist: other names the owner uses for it, domain words
    # that point to it even without its name, and the role that does its code work
    aliases: list[str] = []
    keywords: list[str] = []
    specialist_role: str = "developer"


# ------------------------------------------------------------------ aggregate

class AppConfig(Strict):
    app_dir: Path
    root: Path
    default: DefaultConfig
    providers: ProvidersConfig
    models: ModelsConfig
    agents: AgentsConfig
    skills: SkillsConfig
    permissions: PermissionsConfig
    workers: WorkersConfig
    resources: ResourcesConfig
    schedules: SchedulesConfig
    projects: dict[str, ProjectProfile]

    @model_validator(mode="after")
    def _agents_route_to_known_things(self) -> AppConfig:
        known_types = set(self.providers.task_budgets)
        for p in self.providers.providers.values():
            known_types |= set(p.priority)
        for name, a in self.agents.agents.items():
            if a.provider_task_type is not None and a.provider_task_type not in known_types:
                raise ValueError(f"agent '{name}': unknown provider_task_type "
                                 f"{a.provider_task_type!r}")
            for s in a.skills:
                if s.split(".")[0] not in self.skills.skills:
                    raise ValueError(f"agent '{name}': unknown skill {s!r}")
        for key, proj in self.projects.items():
            role = self.agents.agents.get(proj.specialist_role)
            if role is None or "coding" not in role.task_types:
                raise ValueError(f"project '{key}': specialist_role "
                                 f"{proj.specialist_role!r} is not a coding agent")
        return self

    def path(self, name: str) -> Path:
        """Absolute runtime path for a `paths.*` entry, e.g. path('logs_dir')."""
        rel = getattr(self.default.paths, name)
        assert isinstance(rel, Path)
        return rel if rel.is_absolute() else self.root / rel
