# Personal AI Agent OS — চূড়ান্ত বাংলা বিল্ড প্ল্যান v3

> এই v3-তে Multi-AI Provider Router, automatic failover, OpenAI/Google/Claude/Local adapters, graceful shutdown, notification throttle, Ollama model selection, DB migration (Alembic), dashboard tech stack, IPC, dependency management, testing, authentication, structured logging, error handling, resource management এবং config management final করা হয়েছে।

## ১. মূল লক্ষ্য

Windows PC-এর জন্য একটি all-in-one Personal AI Agent বানানো হবে, যেটাকে ফোন থেকে দূরে বসে নিয়ন্ত্রণ করা যাবে।

প্রধান নিয়ন্ত্রণ মাধ্যম:
- Telegram → Primary
- WhatsApp → Secondary

Agent ইনপুট হিসেবে বুঝবে:
- বাংলা text
- English text
- Banglish / mixed text
- বাংলা voice
- English voice
- Banglish / mixed voice
- Image
- File
- Command

Agent-এর কাজ হবে:

1. User কী চেয়েছে সেটা বুঝবে
2. কাজের risk level বুঝবে
3. সঠিক AI/model বেছে নেবে
4. সঠিক specialist agent বেছে নেবে
5. প্রয়োজনীয় skill বেছে নেবে
6. কাজের plan করবে
7. PC-তে কাজ execute করবে
8. result observe করবে
9. কাজ সত্যি হয়েছে কিনা verify করবে
10. দরকার হলে retry/fallback করবে
11. dangerous action হলে approval চাইবে
12. Telegram/WhatsApp-এ progress এবং final result পাঠাবে

Target experience:

Phone
→ Telegram/WhatsApp
→ AI Agent
→ PC
→ কাজ
→ Verification
→ Result / Approval


## ২. PC Hardware

Target PC:

- CPU: AMD Ryzen 7 7700
- GPU: NVIDIA RTX 5060 Ti 16 GB
- RAM: 16 GB
- Storage: 1 TB NVMe SSD

Assessment:

- CPU: খুব ভালো
- GPU: Local AI, vision, voice-এর জন্য খুব ভালো
- NVMe: খুব ভালো
- RAM: V1-এর জন্য যথেষ্ট
- ভবিষ্যতে recommended upgrade: 32 GB RAM

কারণ local AI + Chrome + VS Code + voice model + automation একসাথে বেশি চালালে 16 GB RAM bottleneck হতে পারে।


## ৩. Final Architecture

Phone
│
├── Telegram (Primary)
└── WhatsApp (Secondary)
        │
        ▼
Communication Gateway
        │
        ├── Authentication
        ├── Message handling
        ├── Voice/File/Image receiving
        └── Task queue
        │
        ▼
Input Processor
        │
        ├── Language detection
        ├── faster-whisper speech-to-text
        ├── Image/Vision input
        ├── File parsing
        └── Intent extraction
        │
        ▼
Master Orchestrator
        │
        ├── Task বুঝবে
        ├── Risk classify করবে
        ├── AI/model select করবে
        ├── Specialist Agent select করবে
        ├── Skill select করবে
        ├── Execution plan বানাবে
        ├── Execute করবে
        ├── Observe করবে
        ├── Verify করবে
        ├── Retry/Fallback করবে
        └── Report করবে
        │
        ├── Specialist Agents
        ├── Memory Engine
        ├── Permission Engine
        └── Model Router
        │
        ▼
Skills Layer
        │
        ├── Windows
        ├── Desktop UI
        ├── Browser
        ├── Files
        ├── Terminal
        ├── PowerShell
        ├── Coding
        ├── Git/GitHub
        ├── Research
        ├── Documents
        ├── PDF
        ├── Spreadsheet
        ├── Screenshot
        ├── Clipboard
        ├── Download
        ├── Media/FFmpeg
        ├── Network
        ├── Monitoring
        ├── Scheduler
        ├── Voice
        └── Project-specific skills
        │
        ▼
Windows Execution Layer
        │
        ├── Core Service
        ├── Desktop Worker
        ├── Browser Worker
        ├── Privileged/Admin Broker
        └── Local AI Runtime


## ৪. Runtime Design

সবকিছু একটাই Python process-এর মধ্যে বানানো হবে না।

### Core Service

PC boot হওয়ার পর background-এ চলবে।

কাজ:

- Telegram receive
- Task queue
- AI reasoning
- File operation
- Terminal command
- Git
- Scheduler
- Monitoring
- Memory
- Background browser
- Approval handling
- Logs

### Desktop Worker

Windows user session-এর ভিতরে চলবে।

কাজ:

- Mouse
- Keyboard
- Window control
- App control
- Chrome
- VS Code
- Screenshot
- Foreground UI automation

### Browser Worker

কাজ:

- Playwright
- Browser profile
- Login session
- Website testing
- Browser automation
- Upload/Download
- Screenshot

### Privileged/Admin Broker

শুধু approved admin-level কাজ করবে।

যেমন:

- Service restart
- Approved package install
- Approved network/firewall change
- PC restart
- PC shutdown
- নির্দিষ্ট allowlisted elevated task

Master Agent-কে unrestricted Administrator shell দেওয়া হবে না।


## ৫. PC Locked থাকলে কী হবে

PC locked থাকলেও Agent-এর অনেক কাজ চলবে।

Allowed:

- Telegram
- AI reasoning
- File work
- Terminal
- Git
- Coding
- Download
- Background browser
- API/server task
- Monitoring
- Scheduler

Restricted:

- Real foreground mouse click
- Real keyboard input
- Interactive desktop action

যদি কোনো task unlock ছাড়া সম্ভব না হয়, Agent Telegram-এ বলবে:

"এই কাজের desktop অংশ চালাতে PC unlock দরকার। Background অংশ চালু আছে।"


## ৬. Communication Channel

### Primary: Telegram

Telegram দিয়ে support থাকবে:

- Text
- Voice
- Image
- File
- Command
- Button
- Approval
- Screenshot
- Progress update

V1-এ long polling ব্যবহার করা হবে।

ফলে সাধারণ ব্যবহারে:

- VPS লাগবে না
- Port forwarding লাগবে না
- Public IP লাগবে না

PC internet connected থাকলেই যথেষ্ট।

### Secondary: WhatsApp

Telegram stable হওয়ার পরে add করা হবে।

WhatsApp-এ থাকবে:

- Text
- Voice
- Remote command
- Task result
- Approval flow

Administrative কাজের জন্য Telegram primary থাকবে।


## ৭. Voice System

Use:

- faster-whisper
- GPU acceleration

Support:

- বাংলা
- English
- Banglish
- Mixed Bangla-English

Pipeline:

Voice Message
→ Audio download
→ Audio cleanup / VAD
→ faster-whisper
→ Language detection
→ Transcript
→ Intent parser
→ Master Agent

Dangerous command-এর transcription clear না হলে Agent confirm না করে কাজ করবে না।


## ৮. AI Provider Layer — কোনো একটি AI-এর ওপর নির্ভর করা হবে না

এই system-এর সবচেয়ে গুরুত্বপূর্ণ design rule:

**কোনো একটাকে “Main AI” ধরে পুরো system বানানো হবে না।**

Agent, Skill, Memory, Permission, Browser, Windows control এবং Task Engine provider-independent থাকবে।

AI provider শুধু reasoning/coding/review engine হিসেবে plug-in হবে।

এর ফলে:

- OpenAI/Codex limit শেষ হলে Google Antigravity-তে switch করা যাবে
- Google service সমস্যা করলে Codex-এ switch করা যাবে
- ভবিষ্যতে Claude Pro/Max কিনলে Claude Code enable করলেই চলবে
- Cloud provider unavailable হলে Ollama local fallback থাকবে
- কোনো provider-এর model name বদলালেও core system rewrite লাগবে না


### ৮.১ Provider Adapter Interface

সব provider একই common interface implement করবে।

providers/
├── openai_codex/
├── google_antigravity/
├── gemini_api/
├── anthropic_claude/
├── ollama_local/
└── provider_base/

Common input:

- Task ID
- Task type
- Workspace
- User request
- Relevant memory/context
- Allowed tools
- Risk level
- Current checkpoint
- Previous attempt summary
- Time/token/cost limits

Common output:

- Status
- Answer/plan
- Files changed
- Commands proposed/executed
- Tool events
- Session ID
- Usage information
- Error category
- Retry-after information
- Confidence / verification hints

Master Orchestrator provider-specific format জানবে না। Adapter translation করবে।


### ৮.২ Subscription-backed Provider

বর্তমানে target:

#### OpenAI / ChatGPT Pro
Primary integration:
- Codex CLI
- Non-interactive execution
- ChatGPT account authentication

Use:
- Coding
- Debugging
- Repo analysis
- Refactor
- Test/build repair
- Complex software work

Optional future:
- OpenAI API Adapter

Important:
ChatGPT subscription এবং OpenAI API billing আলাদা হিসেবে treat করতে হবে।


#### Google AI Pro / Gemini access
Primary integration:
- Google Antigravity CLI / supported SDK path
- Google AI Pro account

Use:
- Coding
- Reasoning
- Project analysis
- Alternative implementation
- Review
- Browser/dev workflows where supported

Optional:
- Gemini Developer API Adapter using API key

Important:
Google AI Pro subscription এবং Gemini Developer API billing/quota একই জিনিস ধরে নেওয়া হবে না।


#### Anthropic / Claude
এখন disabled থাকবে, কিন্তু code structure প্রথম দিন থেকেই থাকবে:

providers/anthropic_claude/

Supported future modes:
- Claude Code with Claude Pro/Max subscription
- Claude API if user later wants separate API billing

যেদিন Claude subscription নেওয়া হবে:

1. Claude Code install/authenticate
2. config-এ `enabled: true`
3. health check pass

এর বাইরে Master Agent, Skills, Memory, Telegram, Browser বা project code change করতে হবে না।


#### Local Ollama
সবসময় available fallback হিসেবে থাকবে।

Use:
- Intent classification
- Short reasoning
- Summarization
- Memory extraction
- Offline work
- Provider outage fallback
- Low-risk tasks


### ৮.৩ Provider Configuration

config/providers.yaml-এর ধারণা:

openai_codex:
  enabled: true
  mode: subscription
  priority:
    coding: 100
    review: 80

google_antigravity:
  enabled: true
  mode: subscription
  priority:
    coding: 90
    reasoning: 100
    review: 90

gemini_api:
  enabled: optional
  mode: api
  priority:
    reasoning: 85
    vision: 90

anthropic_claude:
  enabled: false
  mode: subscription_or_api
  priority:
    coding: 95
    reasoning: 95
    review: 100

ollama_local:
  enabled: true
  mode: local
  priority:
    simple: 100
    offline: 100
    fallback: 70

Model name hard-code করা হবে না। Model alias config-এ থাকবে।


### ৮.৪ Provider Health States

প্রতিটি provider-এর runtime health state থাকবে:

- HEALTHY
- BUSY
- RATE_LIMITED
- COOLDOWN
- DEGRADED
- AUTH_REQUIRED
- UNAVAILABLE
- DISABLED

Router task দেওয়ার আগে health check করবে।


### ৮.৫ Automatic Failover

Example:

Coding task
→ Codex
→ rate limit / timeout / service error
→ task checkpoint save
→ Codex circuit open
→ Antigravity
→ fail হলে Claude (যদি enabled)
→ fail হলে Local model / user report

Provider change হওয়ার সময় কাজ zero থেকে শুরু করা হবে না।

Next provider পাবে:

- Original request
- Current task plan
- Current Git diff
- Files already changed
- Commands already run
- Test results
- Last successful checkpoint
- Previous provider-এর concise failure summary


### ৮.৬ Circuit Breaker

এক provider বারবার fail করলে Router বারবার একই provider call করবে না।

Example:

3 transient failures
→ provider = COOLDOWN
→ 10/20/30 minute backoff
→ অন্য provider ব্যবহার
→ পরে health probe
→ healthy হলে pool-এ ফেরত


### ৮.৭ Error অনুযায়ী Provider Switch

- 429 / usage limit → সঙ্গে সঙ্গে next provider
- Temporary 5xx / overloaded → limited retry, তারপর next provider
- Timeout → retry once, তারপর next provider
- Authentication error → provider disable + Telegram alert
- Model unavailable → same provider-এর configured alternative model, তারপর next provider
- Tool failure → AI provider না বদলে skill/tool repair first
- Unsafe/destructive conflict → provider switch নয়, user approval/block


### ৮.৮ Task-type Based Routing

একটা global “primary AI” থাকবে না।

Task অনুযায়ী best provider:

#### Coding
Codex
→ Antigravity
→ Claude Code (enabled হলে)
→ Ollama coding model

#### Code Review
Claude (enabled হলে)
→ Antigravity
→ Codex
→ Local

#### General Reasoning
Antigravity/Gemini
→ Claude (enabled হলে)
→ Local
→ Codex only if suitable

#### Offline / private local task
Ollama first

#### Provider-specific capability
Capability registry অনুযায়ী route হবে।


### ৮.৯ Load Balancing / Quota Awareness

Router শুধু failure-এর পর switch করবে না।

এগুলোও দেখবে:

- Current quota/limit status
- Task complexity
- Provider health
- Historical success rate
- Latency
- Local resource pressure
- Cost mode
- Required capability

Simple কাজ expensive/limited cloud provider-এ পাঠানো হবে না।


### ৮.১০ Cross-Provider Review

High-value task-এ optional second opinion:

Provider A
→ কাজ করবে

Provider B
→ read-only review করবে

Verifier
→ deterministic test চালাবে

Use cases:
- Production code
- Security-sensitive change
- Large refactor
- Deployment configuration
- Important automation logic

দুই AI-কে একই working tree-তে একই সময়ে edit করতে দেওয়া হবে না।

Parallel কাজ দরকার হলে আলাদা Git worktree/sandbox ব্যবহার করা হবে।


### ৮.১১ Provider-neutral Specialist Agents

Developer Agent নিজে Codex-specific হবে না।

Example:

Developer Agent
→ Provider Router
→ আজ Codex

একই Developer Agent
→ কাল Antigravity

একই Developer Agent
→ পরে Claude

Agent definition change লাগবে না।


## ৯. Cloud + Local Brain Strategy

System-এর rule:

**এক provider কাজ না করলে পুরো Agent বন্ধ হবে না।**

Provider pool:

1. OpenAI Codex
2. Google Antigravity
3. Claude Code / Claude API — future-ready, initially disabled
4. Gemini API — optional
5. Ollama Local

Router প্রয়োজন অনুযায়ী provider select/switch করবে।

Subscription-backed tools আগে ব্যবহার করা হবে যাতে existing subscription-এর value পাওয়া যায়।

Separate API key/provider ব্যবহার করলে সেটা explicit config হবে এবং accidental paid usage বন্ধ রাখতে budget guard থাকবে।


## ৯A. Ollama Model Selection Policy

Local model hard-code করে স্থায়ীভাবে lock করা হবে না। `models.yaml`-এ role-based alias থাকবে।

Initial profile:

```yaml
ollama_models:
  fast_general: "qwen3:8b"
  intent_classification: "qwen3:8b"
  summarization: "qwen3:8b"
  bangla_banglish: "qwen3:8b"
  local_code_review: "deepseek-coder-v2:16b"
```

Rules:

- `qwen3:8b` হবে default lightweight local model
- `deepseek-coder-v2:16b` শুধু coding/review দরকার হলে on-demand load হবে
- একই সময়ে unnecessary দুইটা heavy model loaded রাখা হবে না
- Model idle timeout-এর পরে unload করা যাবে
- Install করার পরে Bengali/Banglish, tool-use, latency, VRAM/RAM benchmark চালানো হবে
- Benchmark fail করলে config থেকে model বদলানো যাবে, core code change লাগবে না
- General purpose-এর জন্য একই কাজের ৩টা 8B model install করে storage/RAM নষ্ট করা হবে না

Current hardware-এর জন্য খুব বড় local coding model default করা হবে না। Cloud coding providers complex কাজের জন্য আগে থাকবে।


## ৯.১ Side-effect Safety During Failover

Provider failover-এর সবচেয়ে বড় risk হলো একই action দুইবার হয়ে যাওয়া।

তাই:

AI Provider
→ plan/proposal

Execution Layer
→ real PC action

External side-effect-এর জন্য ledger থাকবে:

- File delete ID
- Message send ID
- Git push ID
- Order/action ID
- Admin action ID

এক action already complete হলে নতুন provider সেটা আবার execute করতে পারবে না।

এটাকে idempotency protection হিসেবে ধরা হবে।


## ৯.২ Provider Session / Context Portability

Provider-specific full chat history-এর ওপর dependency রাখা হবে না।

Shared task state database-এ থাকবে:

- Goal
- Plan
- Important decisions
- Files
- Diff
- Commands
- Results
- Errors
- Checkpoints

ফলে provider switch করলে portable context তৈরি করা যাবে।

## ১০. Master Orchestrator-এর দায়িত্ব

প্রতিটি task-এর জন্য:

1. Sender verify করবে
2. Request বুঝবে
3. Risk classify করবে
4. AI দরকার কিনা দেখবে
5. Model select করবে
6. Specialist Agent select করবে
7. Skill select করবে
8. Desktop unlocked কিনা দেখবে
9. Admin privilege দরকার কিনা দেখবে
10. Approval policy check করবে
11. Execution plan বানাবে
12. Execute করবে
13. Result observe করবে
14. Success verify করবে
15. দরকার হলে retry/fallback করবে
16. Task state save করবে
17. Final report পাঠাবে


## ১১. Agent আর Skill-এর পার্থক্য

Rule:

AGENT = কীভাবে চিন্তা করবে

SKILL = কীভাবে কাজ করবে

Example:

Developer Agent
+
Git Skill
+
Terminal Skill
+
Browser Skill
+
File Skill
=
Repository fix + test + verify করতে পারবে


## ১২. Core Specialist Agents

প্রথমে focused roster থাকবে:

1. Master Orchestrator
2. Windows Operator Agent
3. Developer Agent
4. Frontend/UI Agent
5. Browser Agent
6. Research Agent
7. File & Document Agent
8. DevOps Agent
9. Git Agent
10. Media Agent
11. Automation Agent
12. Security Agent
13. Verifier Agent
14. Project-specific Agents

শত শত agent একসাথে load করা হবে না।


## ১৩. Project-Specific Agents

Folder:

agents/projects/
├── click-tv/
├── stream-doctor/
├── social-selling/
├── website-development/
└── seo/

Example: Click TV Agent জানবে:

- Repo location
- GitHub repo
- Site URL
- Folder structure
- Build process
- Scanner
- JSON structure
- Deploy process
- Test rules
- Known architecture

তখন শুধু বলা যাবে:

"Click TV test koro."

প্রতিবার পুরো project explain করতে হবে না।


## ১৪. Skills System

Skills modular হবে।

skills/
├── windows/
├── desktop-ui/
├── app-control/
├── browser/
├── browser-vision/
├── terminal/
├── powershell/
├── files/
├── search/
├── git/
├── github/
├── coding/
├── download/
├── screenshot/
├── clipboard/
├── documents/
├── pdf/
├── spreadsheet/
├── archive/
├── ffmpeg/
├── audio/
├── image/
├── network/
├── monitoring/
├── scheduler/
├── notifications/
├── voice/
├── web-research/
└── project/

প্রতিটি Skill-এর ভিতরে:

- SKILL.md
- manifest.json
- tools/
- tests/
- permissions.json


## ১৫. Browser Architecture

তিনটা layer থাকবে।

### Layer 1: Playwright CLI

Default use:

- Testing
- Routine browsing
- Coding agent-এর browser কাজ
- Deterministic automation

### Layer 2: Playwright MCP

Use:

- Exploratory browser task
- Structured browser interaction
- Complex agent-driven browsing

### Layer 3: Vision Fallback

Normal browser automation fail করলে:

Screenshot
→ Vision
→ UI target
→ Browser/Desktop action
→ Verify

Raw coordinate click হবে last resort।


## ১৬. Browser Profile / Login Session

Agent-এর জন্য আলাদা Browser Profile থাকবে।

User-এর everyday Chrome profile পুরোপুরি Agent-কে দেওয়া হবে না।

Local-only থাকবে:

- Browser cookie
- Login session
- Storage state
- Authentication data

এসব GitHub-এ যাবে না।


## ১৭. Windows Control Priority

Priority:

1. Native API / PowerShell
2. Windows UI Automation
3. Accessibility / structured UI tools
4. Keyboard shortcut
5. Mouse coordinate → Last resort

Coordinate automation fragile, তাই default হবে না।


## ১৭A. IPC — Worker-গুলো কীভাবে communicate করবে

V1-এ IPC স্পষ্টভাবে define করা থাকবে।

### Core ↔ Desktop Worker
- `127.0.0.1` loopback FastAPI
- Random local bearer token
- Port শুধু localhost-এ bind
- Request ID + Task ID required

### Core ↔ Browser Worker
- `127.0.0.1` loopback FastAPI
- Same authenticated internal RPC pattern
- Browser session ID আলাদা

### Core ↔ Privileged Broker
- Windows Named Pipe
- Windows ACL
- Generic localhost admin HTTP endpoint থাকবে না
- Only allowlisted elevated actions

Future-এ scaling দরকার হলে gRPC-তে migrate করা যাবে।

Internal protocol version থাকবে যাতে worker update mismatch detect করা যায়।


## ১৭B. Dependency Management

Python project:

- `pyproject.toml`
- `uv`
- `uv.lock`
- Python version pinned
- Worker-specific optional dependency groups

Example groups:

- core
- desktop
- browser
- voice
- local-ai
- dev
- test

Node/Playwright:

- `package.json`
- lock file
- Playwright browser version controlled

Install reproducible হতে হবে।


## ১৭C. Testing Strategy

tests/
├── unit/
├── integration/
├── e2e/
├── security/
├── provider/
├── skills/
└── mocks/

Use:
- pytest
- Provider mocks
- Telegram mock
- File-system sandbox
- Browser test environment

GitHub Actions:
- Unit test
- Non-GUI integration test
- Lint/type checks
- Config validation

Local Windows E2E:
- Desktop UI
- Real browser profile test
- Voice/GPU
- Privileged Broker

প্রতিটি build Phase-এর নিজস্ব pass/fail test থাকবে।


## ১৭D. Authentication

Telegram:
- Exact `chat_id` whitelist
- Unknown user = ignore + security log

WhatsApp:
- Allowed phone-number whitelist

Internal Worker API:
- Local bearer token
- Token rotate support

High-risk admin actions:
- Telegram account authentication যথেষ্ট না হলে optional TOTP/secondary confirmation
- Approval expires after short timeout

Secrets কোনো log-এ print হবে না।


## ১৭E. Structured Logging & Audit

logs/
├── core/
├── desktop/
├── browser/
├── provider/
├── tasks/
└── audit/

Log fields:

- timestamp
- task_id
- worker
- agent
- skill
- provider
- action
- status
- duration
- error_code

Secret redaction বাধ্যতামূলক।

Audit log:
- Approval
- Rejection
- Permission change
- Admin action
- External message
- Git push
- Delete

Log rotation থাকবে। Critical error Telegram-এ alert করবে।


## ১৭F. Error Handling

Error classes:

### TRANSIENT
Retry করা যাবে
- timeout
- temporary network failure

### DEGRADED
Fallback provider/tool ব্যবহার
- AI rate limit
- browser feature unavailable

### BLOCKING
User input দরকার
- login expired
- ambiguous destructive command

### CRITICAL
Immediate alert
- database corruption
- repeated privileged worker failure
- possible security breach

Retry policy skill/provider অনুযায়ী আলাদা হবে।
Exponential backoff যেখানে দরকার সেখানে ব্যবহার হবে।


## ১৭G. Config Management

config/
├── default.yaml
├── providers.yaml
├── models.yaml
├── agents.yaml
├── skills.yaml
├── permissions.yaml
├── workers.yaml
├── resources.yaml
├── projects/
│   ├── click-tv.yaml
│   └── stream-doctor.yaml
└── schedules.yaml

`.env` / encrypted vault:
- শুধু secrets

YAML:
- human-readable non-secret configuration

Startup-এর সময় schema validation হবে।
Invalid config হলে Agent unsafe mode-এ start করবে না।


## ১৭H. Resource Manager

16 GB RAM-এর জন্য resource-aware behaviour থাকবে।

Monitor:
- RAM
- VRAM
- CPU
- GPU
- Disk
- Browser tabs
- Local model usage

Example behaviour:

RAM moderate:
→ normal

RAM high:
→ idle browser tabs close
→ low-priority worker pause

RAM critical:
→ Ollama unused model unload
→ Whisper model unload when idle
→ new heavy local task cloud provider-এ route
→ user notification if necessary

Whisper সবসময় VRAM/RAM-এ loaded থাকবে না।
Local LLM-ও idle timeout অনুযায়ী unload করা যাবে।

## ১৮. Permission System

চার level:

### GREEN

Automatic

Examples:

- Read file
- Search
- Screenshot
- PC status
- Research

### BLUE

Automatic + log

Examples:

- File create
- Browser navigation
- Test run
- Download

### YELLOW

Safety check

Examples:

- Overwrite file
- Package install
- Git commit
- Configuration change

### RED

Explicit approval required

Examples:

- Important data delete
- Production push
- External message send
- Shutdown/restart
- Account/security change
- Destructive admin action

Approval একটি নির্দিষ্ট task/action-এর জন্য valid হবে। পুরনো approval reuse হবে না।


## ১৯. Prompt Injection Protection

নিচের জিনিসগুলো untrusted data হিসেবে treat হবে:

- Website
- PDF
- Downloaded file
- Email content
- External document

এসব কখনো পারবে না:

- System policy override করতে
- নিজের permission বাড়াতে
- Secret reveal করতে
- Dangerous action approve করতে
- User instruction replace করতে

Trust order:

User Instruction
→ Master Policy
→ Agent Rules
→ External Content = Data only


## ২০. Memory Architecture

চার layer:

### Working Memory

Current task

### Session Memory

Current conversation/task chain

### Project Memory

Project-specific context

### Long-Term Memory

Stable preference/configuration

Initial database:

SQLite

Suggested tables:

- tasks
- task_steps
- messages
- projects
- memories
- skills
- agents
- approvals
- audit_log
- files_index
- settings

Vector database শুরুতেই দরকার নেই।


## ২১. Memory Write Policy

Temporary info
→ Permanent memory না

Project fact
→ Project memory

Stable preference
→ Long-term memory

Password / Token / API key
→ Memory-তে না

Credential
→ Secrets vault only


## ২১A. Database Migration Strategy

SQLite schema ভবিষ্যতে বদলাবে, তাই migration first day থেকেই থাকবে।

Final choice:

- SQLAlchemy ORM
- Alembic migration
- Migration files GitHub-এ version controlled
- Agent startup-এর আগে current schema version check
- Backup ছাড়া destructive migration চলবে না
- Migration fail হলে Agent safe mode-এ যাবে
- Automatic rollback সম্ভব হলে rollback করবে, না হলে Telegram alert দেবে

Structure:

migrations/
├── versions/
│   ├── 001_initial.py
│   ├── 002_provider_history.py
│   └── 003_idempotency_ledger.py
└── alembic.ini

Production/local memory DB-তে manual schema edit করা হবে না।


## ২২. Secrets Management

Local-only:

- Telegram bot token
- API keys
- Browser cookies
- WhatsApp sessions
- Google sessions
- GitHub credentials
- Authentication token
- Encryption key

Repo-তে থাকবে:

- .env.example

Repo-তে থাকবে না:

- .env
- credentials
- cookies
- sessions


## ২৩. GitHub Strategy

PRIVATE GitHub repository-তে থাকবে:

- Source code
- Agents
- Skills
- Tests
- Install scripts
- Docs
- Config templates

LOCAL PC-তে থাকবে:

- Memory
- Secrets
- Sessions
- Logs
- Downloads
- Local models
- Personal files


## ২৪. Final Windows Folder Structure

D:\Personal-Agent\
│
├── app\                         # Private GitHub repo
│   ├── core\
│   │   ├── orchestrator\
│   │   ├── router\
│   │   ├── planner\
│   │   ├── executor\
│   │   ├── verifier\
│   │   ├── permissions\
│   │   └── queue\
│   ├── channels\
│   │   ├── telegram\
│   │   ├── whatsapp\
│   │   └── local\
│   ├── agents\
│   │   ├── master\
│   │   ├── developer\
│   │   ├── windows\
│   │   ├── browser\
│   │   ├── research\
│   │   ├── devops\
│   │   ├── media\
│   │   ├── security\
│   │   ├── verifier\
│   │   └── projects\
│   ├── skills\
│   ├── models\
│   │   └── router\
│   ├── workers\
│   │   ├── core-service\
│   │   ├── desktop-worker\
│   │   ├── browser-worker\
│   │   └── privileged-broker\
│   ├── memory\
│   ├── tests\
│   ├── installer\
│   ├── scripts\
│   └── docs\
│
├── data\
│   ├── agent.db
│   ├── projects\
│   ├── task-history\
│   └── indexes\
│
├── secrets\
├── sessions\
│   ├── browser\
│   └── whatsapp\
├── workspace\
├── downloads\
├── logs\
├── backups\
└── local-models\


## ২৫. Programming Stack

Core:
- Python 3.12

Browser:
- Node.js
- Playwright

Windows:
- PowerShell
- Windows UI Automation
- Native Windows tools

Local AI:
- Ollama

Voice:
- faster-whisper

Media:
- FFmpeg

Database:
- SQLite

Version Control:
- Git
- GitHub


## ২৬. Task State Machine

প্রতিটি task-এর ID থাকবে।

Example:

TASK #1024

RECEIVED
→ PLANNING
→ RUNNING
→ WAITING_APPROVAL
→ VERIFYING
→ COMPLETED

Additional state:

- PAUSED
- FAILED
- CANCELLED
- WAITING_DESKTOP
- WAITING_RESOURCE
- RETRYING


## ২৭. Telegram Commands

Natural language ছাড়াও:

/status
/tasks
/task <id>
/cancel <id>
/pause
/resume
/screenshot
/pc
/skills
/agents
/logs
/restart-agent
/update
/lockdown
/help

/lockdown দিলে:

- New task stop
- Dangerous pending action cancel
- Privileged broker disable
- Telegram control alive থাকবে


## ২৮. Progress Reporting + Notification Throttle

Agent message spam করবে না।

Routine progress update-এর default policy:

```yaml
notification_throttle:
  min_progress_interval_seconds: 30
  soft_max_progress_updates_per_task: 5
  repeated_error_cooldown_seconds: 300
  batch_small_results: true
```

`soft_max` hard limit না। নিচের message কখনো routine throttle-এর কারণে আটকে যাবে না:

- Approval required
- User input required
- Critical security alert
- Task completed
- Task failed permanently
- Agent going offline
- Recovery after restart

একই error বারবার হলে 5 মিনিটের মধ্যে duplicate alert suppress করা যাবে, কিন্তু severity বাড়লে সঙ্গে সঙ্গে নতুন alert যাবে।

Long task যদি অনেক ঘণ্টা চলে, progress update soft cap dynamically বাড়তে পারবে।

Example:

Task #1024 Running

Completed:
- Repo updated
- Build complete
- Browser opened
- Categories checked

Current:
- Player test

Buttons:
- Details
- Cancel


## ২৯. Checkpoint + Resume

Long task step-by-step checkpoint save করবে।

Example:

1. Pull repo                 DONE
2. Analyze files             DONE
3. Find bug                  DONE
4. Modify code               DONE
5. Build                     DONE
6. Browser test              CURRENT
7. Regression fix
8. Final test
9. Push

যদি:

- Agent restart হয়
- PC reboot হয়
- Model limit শেষ হয়
- Tool crash করে

তাহলে safe হলে last checkpoint থেকে resume করবে।


## ৩০. Verifier Agent

Agent নিজে "কাজ হয়েছে" বললেই task complete হবে না।

Coding example:

Developer
→ Code changed
→ Build
→ Tests
→ Browser test
→ Verifier
→ PASS / FAIL

FAIL হলে:

- Retry
- Fallback
- Retry limit শেষে blocker report


## ৩১. Self-Healing + Health Check

Monitor করবে:

- Telegram connection
- Core Service
- Desktop Worker
- Browser Worker
- Database
- Ollama
- Playwright
- Disk space
- Internet
- Codex availability

Component crash হলে:

→ Restart component

Repeated failure হলে:

→ Telegram notification


## ৩২. Reboot Recovery

Windows boot হলে:

1. Core Service start
2. Telegram reconnect
3. SQLite load
4. Pending task recover
5. Background task resume

User login হলে:

6. Desktop Worker start
7. Full desktop capability restore


## ৩২A. Graceful Shutdown Procedure

Agent stop/restart/update হওয়ার আগে controlled shutdown করবে।

Order:

1. নতুন task accept বন্ধ
2. Incoming message queue temporarily pause
3. Running task-কে safe checkpoint-এ নেওয়ার চেষ্টা
4. Current task state + provider session summary DB-তে save
5. Queued task DB-তে persist
6. Pending external side-effect state/idempotency ledger flush
7. Browser Worker-কে graceful stop signal
8. Desktop Worker-কে graceful stop signal
9. Local AI request drain/cancel
10. Database transaction commit + close
11. Logs/Audit logs flush
12. Telegram-এ প্রয়োজন হলে "Agent going offline" status
13. Core Service stop

Forced shutdown timeout থাকবে। Worker নির্দিষ্ট সময়ের মধ্যে stop না করলে kill করা হবে, কিন্তু আগে state persistence চেষ্টা করা হবে।

Windows shutdown/restart initiated by Agent হলেও একই procedure চলবে।


## ৩৩. PC সম্পূর্ণ OFF থাকলে

PC fully off থাকলে local Agent Telegram/WhatsApp receive করতে পারবে না।

Future optional feature:

Wake-on-LAN

এটার জন্য suitable always-on sender/router/device অথবা external relay দরকার হবে।


## ৩৪. File Capabilities

Agent পারবে:

- File search
- Recent file find
- Rename
- Copy
- Move
- Create
- Edit
- Zip/unzip
- Folder organize
- Duplicate find
- Metadata analyse
- Download/upload
- Document read

Important/path-wide delete হলে approval দরকার।


## ৩৫. Coding Workflow

Example:

"Click TV latest repo te player problem fix koro."

Flow:

Telegram/Voice
→ Master Agent
→ Click TV project identify
→ Click TV Agent
→ Developer Agent
→ Git pull
→ Codex
→ Edit
→ Git diff
→ Build
→ Test
→ Browser test
→ Verifier
→ Telegram report
→ Approval পেলে push


## ৩৬. Browser Workflow

Example:

"Badhonsworld seller account-e giye new product gula check koro."

Flow:

Browser Agent
→ Agent browser profile
→ Navigate
→ Inspect
→ Playwright
→ দরকার হলে Vision
→ Data collect
→ Verify
→ Report

যদি action হয়:

- Message send
- Order placement
- Payment
- Account change

তাহলে approval policy apply হবে।


## ৩৭. Media Skill

FFmpeg/direct tools দিয়ে:

- Video trim
- Cut
- Crop
- Resize
- Compress
- Convert
- Merge
- Audio extract
- Audio normalize
- Subtitle burn
- Thumbnail create
- Frame capture
- Metadata inspect

Deterministic media কাজের জন্য LLM token waste করা হবে না।


## ৩৮. Research Skill

Workflow:

Search
→ Source open
→ Compare
→ Cross-check
→ Summarize
→ Report save
→ Telegram result

Important research-এ একটাই source-এর ওপর নির্ভর করবে না।


## ৩৯. Scheduler

Support:

- One-time task
- Recurring task
- Delayed task
- Future condition-based task

Examples:

"Raat 2 tay scanner run koro"

"Protidin shokal 9 tay website health check koro"

"30 minute por download status bolo"

Schedule Agent restart-এর পরেও persist করবে।


## ৪০. Local Dashboard

Core system stable হওয়ার পরে Dashboard বানানো হবে।

Final preferred stack:

- Backend/API: FastAPI
- Frontend: React + Vite
- Local-only binding by default
- Existing Core API-এর ওপর dashboard বসবে
- Dashboard নিজে privileged action bypass করতে পারবে না
- High-risk action একই Permission/Approval Engine দিয়ে যাবে

Dashboard:

- Agent Status
- Active Tasks
- Task History
- Providers + Health
- Agents
- Skills
- Memory
- Logs
- PC resources
- Models
- Approvals
- Schedules
- Settings

Reason:
FastAPI already Python core-এর সঙ্গে natural fit, আর React/Vite future-এ richer control panel বানাতে সুবিধা দেবে।

কিন্তু Dashboard Phase 24-এর আগে build করা হবে না।

Priority:

Reliability
→ Security
→ Automation
→ UI


## ৪১. FINAL BUILD ORDER v3

কোনো Phase skip করা যাবে না।

### Phase 0 — Repository Foundation
- Private GitHub repo
- Folder structure
- `.gitignore`
- Secrets boundary

Pass:
Clean base ready


### Phase 1 — Config + Dependency + Logging Foundation
- `pyproject.toml`
- `uv.lock`
- YAML config
- Structured logs
- Notification throttle config
- Config validation

Pass:
Reproducible install + valid startup


### Phase 2 — Core Service + Telegram + Authentication
- Telegram text
- `chat_id` whitelist
- `/status`
- basic health

Pass:
Authorized phone → PC command works


### Phase 3 — Task DB + Queue + State Machine
- SQLite + SQLAlchemy
- Alembic migrations
- Task ID
- Cancel
- Resume state
- Task history

Pass:
Persistent task engine works


### Phase 4 — Permission + Approval + Audit
- GREEN/BLUE/YELLOW/RED
- Approval button
- Audit trail

Pass:
Dangerous action approval ছাড়া blocked


### Phase 5 — IPC + Worker Skeleton
- Core
- Desktop
- Browser
- Privileged broker interface

Pass:
Authenticated worker communication works


### Phase 6 — Windows + File + PowerShell Skills

Pass:
Basic safe PC control works


### Phase 7 — Screenshot + Windows UI Automation

Pass:
Agent PC দেখতে, action নিতে এবং result verify করতে পারে


### Phase 8 — Provider Abstraction + Ollama
- Provider base interface
- Provider registry
- Health states
- Local model
- Capability registry

Pass:
Agent provider-independentভাবে local AI task চালাতে পারে


### Phase 9 — Voice + faster-whisper

Pass:
Bangla + English + Banglish voice works


### Phase 10 — Browser Worker + Playwright CLI

Pass:
Routine browser automation reliable


### Phase 11 — Playwright MCP + Vision Fallback

Pass:
Complex browser flow works


### Phase 12 — OpenAI Codex Adapter
- ChatGPT-plan auth
- Non-interactive adapter
- Session/result parser
- Health/limit detection

Pass:
Coding task Codex দিয়ে end-to-end works


### Phase 13 — Google Antigravity Adapter
- Google AI Pro auth
- CLI/SDK adapter
- Result normalization
- Health/limit detection

Pass:
Same coding/reasoning task Google provider দিয়েও works


### Phase 14 — Multi-Provider Failover Engine
- Circuit breaker
- Quota-aware routing
- Provider switch
- Context portability
- Idempotency ledger

Also create:
- Claude adapter stub
- Gemini API optional adapter

Pass:
Provider A intentionally unavailable করলে Provider B checkpoint থেকে task continue করে


### Phase 15 — Git/GitHub + Full Coding Workflow

Pass:
Repo edit → test → diff → approval → push works


### Phase 16 — Specialist Agents + Project Agents

Pass:
Correct role + provider + skill routing works


### Phase 17 — Project Memory

Pass:
Project context persists between tasks


### Phase 18 — Verifier + Retry + Cross-Provider Review

Pass:
Multi-step কাজ independent verificationসহ complete হয়


### Phase 19 — Scheduler

Pass:
Future/recurring jobs restart-এর পরেও থাকে


### Phase 20 — Media + Document Skills

Pass:
General PC work expands


### Phase 21 — WhatsApp Adapter

Pass:
Secondary remote channel works


### Phase 22 — Privileged/Admin Broker

Pass:
Approved elevated actions securely work


### Phase 23 — Self-update + Rollback + Graceful Shutdown

Pass:
Update/restart-এর আগে state flush হয় এবং failed update থেকে automatic rollback works


### Phase 24 — Local Dashboard

Pass:
Visual management interface works


### Phase 25 — Full Stress + Security + Provider Failure Testing

Tests:
- Codex limit simulation
- Google provider outage simulation
- Claude disabled/enabled simulation
- Internet loss
- PC restart
- Browser crash
- Worker crash
- Duplicate action prevention
- Prompt injection
- Authentication attack
- Resource pressure

Pass:
Release candidate stable

## ৪২. V1 Release Requirements

V1 complete বলা হবে তখনই যখন সবগুলো কাজ করে:

- Telegram text command
- Bangla voice
- English voice
- Banglish voice
- PC screenshot
- CPU/GPU/RAM/disk status
- App open/close
- File search
- File create/edit/move
- PowerShell
- Terminal command
- Browser control
- Website login/session
- Download
- PDF/text reading
- Git clone/pull/diff
- Codex coding task
- Project build
- Browser test
- Approval system
- Git push after approval
- Scheduler
- Task cancel
- Progress reporting
- Restart-এর পর task recovery
- Local AI fallback
- Activity logs
- Verifier


## ৪৩. V2 Features

V1-এর পরে:

- WhatsApp উন্নত support
- Gmail integration
- Calendar integration
- Google Drive
- Facebook/TikTok automation
- Social Selling workflow
- Advanced video automation
- Wake-on-LAN
- Remote file transfer
- Advanced browser profiles
- Multiple simultaneous agents
- Multi-PC control
- Advanced web dashboard


## ৪৪. Cost Strategy

লক্ষ্য:

Extra monthly cost যতটা সম্ভব ৳0 রাখা।

Use:

- Python: Free
- Telegram: Free
- SQLite: Free
- Playwright: Free
- PowerShell: Free
- FFmpeg: Free
- faster-whisper: Free
- Ollama: Free
- Open-source agent definitions: Free
- Git: Free
- GitHub private repo: Free tier
- Codex: Existing ChatGPT Plus quota
- Gemini API: Free tier যেখানে available
- Antigravity: Existing Google AI Pro access যেখানে applicable

Cloud AI unlimited নয়, তাই local-first routing রাখা হবে।


## ৪৫. যেসব জিনিস বানানো হবে না

- One huge Python file
- One process for everything
- Every command through LLM
- Hundreds of agents loaded together
- Secret/token source code-এর ভিতরে
- Secret GitHub-এ
- Unrestricted admin shell
- Coordinate-only browser automation
- Automatic production push
- Silent destructive delete
- Website text-কে trusted instruction ধরা
- Password memory-তে রাখা
- Core stable হওয়ার আগে fancy UI বানানো


## ৪৬. Full End-to-End Example

User Telegram voice দেয়:

"PC te Click TV repo ta update koro, movie page-er current problem gula browser diya dekho, fix koro, desktop mobile dui ta test koro. Everything thik thakle amk bolo. Amar permission chara push korba na."

Flow:

Voice
→ faster-whisper
→ Master Agent
→ Click TV Project Agent
→ Developer Agent
→ Git pull
→ Browser inspection
→ Bug find
→ Codex fix
→ Build
→ Desktop test
→ Mobile viewport test
→ Verifier
→ Git diff
→ Telegram report
→ Push approval-এর জন্য wait

Expected final report:

TASK COMPLETE

Repository updated
Problems found: 3
Fixed: 3

Build: PASS
Desktop test: PASS
Mobile test: PASS

Modified files:
- site/index.html
- site/app.js
- site/styles.css

GitHub push: NOT performed

Actions:
- View Changes
- Screenshots
- Approve Push
- Reject


## ৪৭. Final Locked Decisions v2

- Telegram = Primary remote control
- WhatsApp = Secondary
- Python = Core language
- Core Service, Desktop Worker, Browser Worker, Privileged Broker = আলাদা
- Agent এবং Skill = আলাদা concept
- **কোনো একক Main AI থাকবে না**
- AI Provider Layer = provider-neutral
- OpenAI Codex = enabled cloud coding provider
- Google Antigravity = enabled cloud provider
- Claude = architecture-এ first day থেকেই থাকবে, subscription না থাকা পর্যন্ত disabled
- Gemini Developer API = optional adapter
- Ollama = local/offline provider
- Task-type based routing = default
- Automatic failover = required
- Circuit breaker = required
- Provider health/quota awareness = required
- Shared checkpoint/context = provider switch-এর ভিত্তি
- Side-effect idempotency = required
- High-value কাজের জন্য optional cross-provider review
- Playwright CLI = Default browser automation
- Playwright MCP = Complex browser task
- Vision = Browser/UI fallback
- faster-whisper = Voice
- SQLite = Task + Memory
- SQLAlchemy + Alembic = Database schema/migration
- Graceful shutdown = Required
- Telegram notification throttle = Required, critical messages exempt
- Ollama model aliases = Config-driven, benchmark-based
- FastAPI + React/Vite = Preferred future dashboard stack
- `uv` + `pyproject.toml` = Python dependency management
- YAML = Non-secret config
- Private GitHub = Source code
- Local PC = Secrets, sessions, memory, logs, models
- Verifier ছাড়া task complete নয়
- Dangerous action approval ছাড়া execute হবে না
- Privileged Broker unrestricted shell expose করবে না
- External content untrusted থাকবে
- Modular design থাকবে যাতে provider/model/skill/agent future-এ বদলালেও core rewrite না লাগে


## ৪৮. Final Target

এই project-এর উদ্দেশ্য simple voice assistant বানানো না।

Final system হবে:

নিজের Windows PC-এর জন্য remotely controllable,
multi-agent,
multi-skill,
voice-enabled,
browser-capable,
coding-capable,
memory-enabled,
self-verifying,
permission-controlled
Personal AI Agent OS.

এটাই full build-এর baseline specification।
# FINAL INTEGRATED ADDITIONS — মূল v3 Plan-এর কোনো অংশ পরিবর্তন/বাদ দেওয়া হয়নি

> এই অংশে Personal AI Assistant agent/capability review থেকে শুধু যেসব জিনিস আমাদের system-এর জন্য বাস্তবে কাজে লাগবে, সেগুলো যোগ করা হয়েছে। উপরের মূল v3 Plan-এর কোনো existing section, rule, architecture, build order বা decision পরিবর্তন বা remove করা হয়নি।

## ৪৯. Core Brain Extension — Context Manager + Intent Router

Master Orchestrator-এর নিচে দুইটি lightweight core module যোগ হবে।

### ৪৯.১ Intent & Task Router

কাজ:

- User request classify করা
- Coding / PC Control / Browser / Research / Social / Project / Reminder / Communication type শনাক্ত করা
- অপ্রয়োজনীয় Agent চালু না করা
- Task ছোট subtask-এ ভাগ করার আগে task category নির্ধারণ
- কোন capability deterministic skill দিয়ে করা যায় আর কোথায় AI দরকার তা ঠিক করা

Example:

- প্রশ্ন → Answer/Research path
- Coding → Developer path
- PC control → Windows/Computer path
- Browser → Browser Agent
- Click TV → Click TV Specialist
- Android → Android Specialist
- Reminder → Scheduler
- Social Media → Social/Growth path
- Document → Document capability

এটা আলাদা heavy AI হবে না। Master Orchestrator-এর routing subsystem হবে।


### ৪৯.২ Context Manager

এটা token/usage control-এর জন্য core component হবে।

কাজ:

- প্রতিটি Agent-কে full chat history না পাঠানো
- শুধু task-relevant context নির্বাচন
- Project Memory থেকে দরকারি decision বের করা
- Relevant file/path নির্বাচন
- Previous task checkpoint summary নেওয়া
- Previous provider/agent handoff compact করা
- Irrelevant conversation বাদ দেওয়া
- Same repository বারবার full scan না করে cached index ব্যবহার
- Reviewer-কে আগে changed files/diff দেওয়া
- Long task-এর context compact করে checkpoint summary বানানো

Context package-এর suggested structure:

```text
Task Goal
Relevant User Instruction
Project Rules
Relevant Memory
Selected Files
Current Diff
Previous Decisions
Current Checkpoint
Known Errors
Allowed Skills
Risk Level
```

Full conversation dump defaultভাবে কোনো specialist agent-কে দেওয়া হবে না।


## ৫০. Role Classification — সবকিছুকে Agent বানানো হবে না

Capability বাড়াতে ৪০+ role list থাকতে পারে, কিন্তু runtime-এ সবকিছুকে আলাদা AI Agent বানানো হবে না।

Final classification:

### A. True Reasoning Agents

এগুলো AI reasoning-heavy:

- Master Orchestrator
- Developer Agent
- Frontend/UI Agent
- Backend Architect
- Android Architect
- Browser Agent
- Research Agent
- DevOps/Release Agent
- Code Reviewer
- Security Auditor
- Social/Growth Strategist
- Project-specific Specialists
- Reality Checker / Verifier

### B. Core Services / Engines

এগুলো deterministic বা system-level component:

- Intent & Task Router
- Model/Provider Router
- Context Manager
- Memory Manager
- Permission Engine
- Task Queue
- Scheduler
- Failure Recovery Engine
- Checkpoint Engine
- Notification Engine
- Resource Manager
- Audit Logger

### C. Skills / Tools

এগুলো কাজ execute করবে:

- Windows Control
- File Manager
- Terminal
- PowerShell
- Git/GitHub
- Browser/Playwright
- Download
- Screenshot
- Clipboard
- PDF/Document
- Spreadsheet
- FFmpeg/Media
- Network
- Monitoring
- Voice STT/TTS
- Vision/Screenshot tools

### D. Project Specialists

Project rules/context জানবে:

- Click TV Specialist
- Stream Doctor Specialist
- Video Downloader Specialist
- Android Downloader Specialist
- Reseller Automation Specialist
- Website Project Specialist
- SEO Project Specialist
- Personal AI Assistant Project Specialist

ফলে capability বড় হবে, কিন্তু একই request-এ অল্প সংখ্যক Agent active হবে।


## ৫১. Project Knowledge System

প্রতিটি বড় project-এর dedicated knowledge profile থাকবে।

Structure:

```text
project-knowledge/
├── click-tv/
│   ├── profile.yaml
│   ├── rules.md
│   ├── architecture.md
│   ├── known-issues.md
│   └── decisions.md
├── video-downloader/
├── stream-doctor/
├── reseller-automation/
├── personal-agent/
└── website-projects/
```

প্রতিটি profile-এ থাকতে পারে:

- Project name
- Local folder
- Repository
- Site/App URL
- Build command
- Test command
- Deploy method
- Architecture
- Important files
- Current rules
- Known limitations
- Previous important decisions
- Approval requirements
- Project-specific verification checklist

কাজের আগে Project Knowledge System শুধু current project-এর relevant rules load করবে।


## ৫২. Project Specialists — আমাদের বাস্তব Project অনুযায়ী

### ৫২.১ Click TV Specialist

জানবে:

- Live TV
- Today Match
- Upcoming Match
- Movies
- Scanner
- Proxy system
- Stream verification
- HLS player
- Categories
- Current UI rules
- Deployment
- Existing project decisions

Generic Coding Agent-এর বদলে Click TV task-এ প্রথমে এই specialist context ব্যবহার হবে।


### ৫২.২ Stream Doctor Specialist

কাজ:

- M3U/M3U8 analysis
- Header analysis
- Referer/Origin requirements
- Stream status
- TTL/expiry diagnosis
- Playback failure diagnosis
- Backup source analysis
- Clean playlist generation
- Verification status classification

### ৫২.৩ Android / Video Downloader Specialist

Sub-capability:

- Android architecture
- Compose/Activity/ViewModel
- Service
- WorkManager
- Permissions
- Storage
- Compatibility
- Download queue
- Resume/retry
- HLS/DASH
- MP4/MKV
- Audio/video merge
- Failure recovery
- Foreground service
- Floating Bubble / Overlay
- Clipboard/share flow
- Optional Accessibility
- Battery optimisation
- Zero-overhead OFF state

### ৫২.৪ Reseller Automation Specialist

Workflow:

```text
Product Source / Telegram Media
→ Product Identify
→ Media Organise
→ Trend/Product Research
→ Content Plan
→ Video Preparation
→ Caption/Copy
→ Social Analytics
→ Best Platform/Time
→ USER APPROVAL
→ Publish/Schedule
→ Result Tracking
→ Growth Experiment
```


## ৫৩. Failure Recovery Engine — আরও শক্তিশালী Recovery

Existing retry/checkpoint system-এর সঙ্গে এই deterministic recovery engine কাজ করবে।

কাজ:

- Last successful step detect
- Failed step identify
- Retry eligibility check
- Same action duplicate হয়েছে কিনা idempotency ledger check
- Partial state cleanup
- Temporary file cleanup
- Safe rollback
- Previous Git state restore যেখানে দরকার
- Broken browser/session restart
- Worker safe restart
- Alternative provider/skill selection
- Human intervention দরকার কিনা নির্ধারণ

Recovery result:

- AUTO_RECOVERED
- RETRY_WITH_FALLBACK
- ROLLED_BACK
- WAITING_USER
- BLOCKED
- FAILED_SAFE

Critical rule:

Recovery কখনো approval-required action নিজে approve করবে না।


## ৫৪. Reality Checker / Verifier — Proof-Based Completion

Existing Verifier Agent-এর verification আরও স্পষ্ট হবে।

কাজ শেষ বলার আগে task অনুযায়ী proof চাইবে।

Coding task:

- Requested change code-এ আছে?
- Build pass?
- Tests pass?
- Regression আছে?
- Browser/UI test pass?
- Changed file সঠিক?
- Git diff expected?
- Failure path test হয়েছে?

PC automation:

- App সত্যি open/closed?
- File সত্যি তৈরি/move হয়েছে?
- Expected state screenshot/system query দিয়ে confirm হয়েছে?

Browser:

- Target page reached?
- Required data পাওয়া গেছে?
- Form/action আসলে complete?
- Download file exist করছে?

Background system:

- Service start/stop সত্যি হয়েছে?
- Restart-এর পরে recovery কাজ করেছে?
- OFF state-এ background overhead acceptable?

Final verdict:

- PASS
- PASS WITH KNOWN LIMITATIONS
- NEEDS WORK
- BLOCKED BY USER/AUTH/EXTERNAL SERVICE

Agent শুধু নিজের statement দিয়ে PASS দিতে পারবে না।


## ৫৫. Social Media & Business Growth Squad

এগুলো সবসময় active থাকবে না। Social/Reseller/SEO কাজ এলেই on-demand load হবে।

### ৫৫.১ Social Media Manager

- Facebook/TikTok/Instagram/YouTube workflow
- Content calendar
- Platform formatting
- Scheduling
- Performance monitoring
- Comment/reply draft
- Post preparation

### ৫৫.২ Growth Strategist

- Growth opportunity
- Acquisition funnel
- Conversion optimisation
- Retention
- Referral/viral loop
- A/B test ideas
- CTA optimisation
- Landing-page experiment
- Growth metrics

Rule:

- Fake engagement না
- Spam না
- Deceptive tactic না
- Platform policy violation না

### ৫৫.৩ Content Creator

- Facebook caption
- TikTok hook
- Reels script
- Shorts script
- Product description
- Story copy
- Hashtag suggestion
- CTA

### ৫৫.৪ Video Content Specialist

- Hook
- Video structure
- Cut point
- Subtitle plan
- B-roll suggestion
- Thumbnail concept
- Duration optimisation
- Platform adaptation

### ৫৫.৫ Social Analytics Specialist

Analyse করবে:

- Views
- Watch time
- Retention
- CTR
- Engagement
- Conversion
- Product interest
- Best posting time
- Winning format

শুধু raw metric নয়, actionable conclusion দেবে।

### ৫৫.৬ Product / Trend Research

- Trending product
- Competitor content
- Audience interest
- Content trend
- Search demand
- Product positioning

Current data দরকার হলে web/search capability ব্যবহার করবে।

### ৫৫.৭ SEO Specialist

- Keyword research
- Search intent
- On-page SEO
- Technical SEO
- Content brief
- Internal linking
- SEO audit

Ranking guarantee করবে না।

### ৫৫.৮ Conversion Copywriter

- Product copy
- Landing page copy
- Headlines
- CTA
- Offer variant
- Ad creative concept
- Email copy

Growth experiment অনুযায়ী copy variant তৈরি করতে পারবে।


## ৫৬. Communication & Productivity Capabilities

এগুলো V1 core-এর বাধ্যতামূলক নয়, existing V2 integration-এর সঙ্গে যুক্ত হবে।

### Email & Communication

- Email summary
- Draft/reply
- Important email detection
- Follow-up draft
- Professional message

External send action Permission Engine-এর মাধ্যমে যাবে।

### Calendar / Planning

- Meeting planning
- Free-time detection
- Reminder
- Daily plan
- Deadline tracking

### Document Capability

- Report
- PRD
- Specification
- Checklist
- Summary
- PDF/document analysis


## ৫৭. Voice + Vision — আমাদের ক্ষেত্রে Core Capability

Voice/Vision optional future feature হিসেবে treat করা হবে না।

### Voice

Existing voice system-এর সঙ্গে:

- Bangla
- English
- Banglish
- Mixed voice
- Telegram voice
- WhatsApp voice
- Optional TTS reply

### Vision / Screen Understanding

কাজ:

- Screenshot analysis
- UI state understanding
- Visual error detection
- UI element identification
- Computer-use support
- Before/after verification
- Browser fallback

Vision result সরাসরি dangerous action approve করতে পারবে না।


## ৫৮. Token / Usage / Context Control Rules

এই rules বাধ্যতামূলক:

1. সব Agent একসঙ্গে চালানো যাবে না।
2. Orchestrator শুধু প্রয়োজনীয় Agent select করবে।
3. Full conversation history specialist Agent-কে defaultভাবে দেওয়া হবে না।
4. Context Manager relevant context pack করবে।
5. একই repository বারবার full scan করা হবে না।
6. Repository index/cache ব্যবহার করা হবে।
7. Handoff summary compact হবে।
8. Reviewer প্রথমে changed files/diff দেখবে।
9. Simple deterministic task premium cloud model-এ যাবে না।
10. একই task Codex + Google + Claude-কে duplicate implementation হিসেবে একসঙ্গে দেওয়া হবে না।
11. Independent second review শুধু high-impact task-এ।
12. Large files full পাঠানোর আগে relevant section extraction হবে।
13. Memory retrieval project/task scoped হবে।
14. Provider failover-এর সময় full history নয়, checkpoint package যাবে।
15. Repeated failed reasoning-এর জন্য context reset/compact mechanism থাকবে।
16. Token/usage budget task class অনুযায়ী configurable হবে।


## ৫৯. Coding Squad Routing

Coding task-এর জন্য fixed সব-agent pipeline থাকবে না।

Task অনুযায়ী route:

### Normal Code Fix

```text
Orchestrator
→ Project Knowledge
→ Developer
→ Build/Test
→ Reality Checker
```

### Frontend/UI

```text
Orchestrator
→ Project Specialist
→ Frontend/UI Agent
→ Browser Test
→ Reality Checker
```

### Android

```text
Orchestrator
→ Android Specialist
→ Developer
→ Build/Test
→ Device/Emulator Verification
→ Code Reviewer if needed
→ Reality Checker
```

### Backend/Architecture

```text
Orchestrator
→ Backend Architect
→ Developer
→ Integration Test
→ Security Review if needed
→ Reality Checker
```

### High-Impact Production Change

```text
Project Specialist
→ Implementation Provider
→ Independent Code Reviewer
→ Security Auditor if required
→ Build/Test
→ Reality Checker
→ USER APPROVAL
→ Release/Push
```


## ৬০. Social/Business Workflow

Default workflow:

```text
Source/Product
→ Reseller Automation Specialist
→ Product/Trend Research
→ Social Analytics Context
→ Growth Strategist
→ Content Creator
→ Video Content Specialist
→ Social Media Manager
→ USER APPROVAL
→ Publish/Schedule
→ Analytics Collection
→ Growth Strategist next experiment
```

সব step প্রতিবার লাগবে না। Orchestrator প্রয়োজন অনুযায়ী ছোট workflow বানাবে।


## ৬১. Final Integrated Runtime Principle

System যত বড় হবে, প্রতিটি request-এর runtime তত বড় হবে না।

Target:

```text
Large Capability Library
        ↓
Intent Router
        ↓
Small Relevant Context
        ↓
1–3 Relevant Agents
        ↓
Required Skills Only
        ↓
Verification
```

এতে:

- Token usage কমবে
- RAM usage কমবে
- Provider quota বাঁচবে
- Handoff clear হবে
- Debugging সহজ হবে
- Wrong-agent interference কমবে
- Project-specific accuracy বাড়বে


## ৬২. Existing Build Order-এর সঙ্গে Addition Mapping

মূল v3 Build Order পরিবর্তন করা হয়নি। নতুন additions existing phase-এর মধ্যে এভাবে implement হবে:

- Intent Router + Context Manager → Core/Provider foundation-এর সঙ্গে
- Project Knowledge → Existing Project Memory phase-এর সঙ্গে
- Failure Recovery Engine → Existing Verifier/Retry/Checkpoint + Self-update/Recovery অংশে
- Reality Checker enhancement → Existing Verifier phase
- Voice/Vision enhancement → Existing Voice + Browser/Vision phases
- Android/Downloader Specialists → Specialist Agents phase
- Click TV/Stream Doctor Specialists → Specialist Agents + Project Memory phase
- Social/Growth Squad → V1 stable হওয়ার পরে V2/Project modules
- Email/Calendar/Communication → Existing V2 integrations
- Token/Context Control → Context Manager + Provider Router + Reviewer policy
- Project-specific knowledge profiles → Project Memory phase


## ৬৩. FINAL ADDITION LOCK

এই integrated additions-এর পর final design rule:

- Capability list বড় হতে পারবে
- Runtime agent count ছোট থাকবে
- Agent reasoning এবং Skill execution আলাদা থাকবে
- Project knowledge scoped থাকবে
- Context selective থাকবে
- AI provider interchangeable থাকবে
- Sensitive action approval ছাড়া হবে না
- Recovery state-aware হবে
- Verification proof-based হবে
- Social/business automation approval-aware হবে
- Voice + Vision remote assistant-এর core capability থাকবে
- কোনো নতুন specialist যোগ করতে core architecture rewrite লাগবে না


# REFERENCE AGENT ADDITIONS (2026-10-23) — মূল plan-এর কোনো অংশ বদলানো/বাদ দেওয়া হয়নি

Owner-এর দেওয়া ৯টা open-source agent repository আর ২টা demo video (Stonic AI) দেখে Nova-তে কী নেওয়া যায়, তার তালিকা। নিয়ম: **ধারণা নেওয়া হবে, কোড কপি নয়** (license আলাদা; Agent Zero-র license স্পষ্ট নয়)। Nova-র নিজের approval, verifier, broker আর secret নিয়ম সব জায়গায় আগের মতোই থাকবে। কোনো framework দিয়ে Nova-কে বদলানো হবে না।

## ৬৪. কোন repo থেকে কী শেখার আছে

| Repo / উৎস | কী আছে | Nova-র জন্য কাজের অংশ |
|---|---|---|
| Dodaios/Dodaios (Windows, local) | wake phrase, টানা কথোপকথন (Listening → Thinking → Speaking), কথার মাঝে থামানো, "thinking pause", Speech Dictionary, dictation, safe/monitor/ask/block | কণ্ঠের পুরো অভিজ্ঞতা — সবচেয়ে কাছের মডেল |
| NousResearch/hermes-agent | অভিজ্ঞতা থেকে নিজে skill বানানো ও ব্যবহারের সময় উন্নত করা, পুরনো কথোপকথন খোঁজা (FTS5), memory-তে মাঝে মাঝে মনে করিয়ে রাখা, /retry /undo /usage /insights, personality | শেখার loop + দরকারি command |
| HKUDS/nanobot | ছোট core, "Dream" — memory একত্র করে USER.md-তে রাখা, দেখার মতো log, temporary chat | memory consolidation, অস্থায়ী কথোপকথন |
| codeninjavik/myra-ultra (★3, Windows) | Gemini Live দিয়ে হাতমুক্ত কথা, ভাসমান orb + কিনারায় আলো (শুনছে বোঝাতে), camera/screen দেখা | ভাসমান orb, realtime কণ্ঠ ধারণা (কোড মান যাচাই হয়নি — শুধু ধারণা) |
| Stonic AI (video ২টা) | জীবন্ত অবতার, Memory/Skills/Character/Settings সংযোগ-চিত্র, pixel "agent office" — কোন agent কোন টেবিলে কী করছে, earbud দিয়ে বাইরে কথা | dashboard-এ "Visual Hub", চরিত্র/ব্যক্তিত্ব |
| citrolabs/ego-lite | agent-এর আলাদা browser Space (owner-এর tab ছোঁয় না), ভালো page snapshot, সফল browser কাজ মনে রেখে পরে দ্রুত করা | browser অভিজ্ঞতা reuse; ego-lite নিজে install নয় (telemetry আছে) |
| agent0ai/agent-zero | Docker-এ পুরো Linux desktop, browser-এ element দেখিয়ে নির্দেশ, document cowork, project ভিত্তিক memory/secret | element দেখিয়ে নির্দেশ (পরে); Docker desktop নয় (RAM ভারী) |
| openclaw/openclaw | এক Gateway-তে অনেক channel, অচেনা sender pairing, model/harness plugin | আমাদের channel কাঠামো আগেই এমন — নতুন কিছু লাগবে না |
| google/artemis (Apache-2.0) | Android phone স্বাভাবিক ভাষায় চালানো, MCP server, প্রতি ধাপ ৩–৫ সেকেন্ড, AndroidWorld ৯৯% | Android Specialist (V2) |
| droidrun/mobilerun (MIT) | Android/iOS চালানো, Ollama সহ যেকোনো model, accessibility tree + screenshot | Android Specialist (V2) — local model দিয়েও চলে |

## ৬৫. যা নেওয়া হবে — অগ্রাধিকার অনুযায়ী

### V1.1 — Voice Quality + Latency (এখন চলছে, আগে শেষ হবে)
Telegram voice-এর দেরি মাপা ও কমানো, installed app catalog + নাম মেলানো (exact → normalized → alias → উচ্চারণ → local AI), owner-এর শুধরে দেওয়া নাম শেখা, Whisper শুধু বাংলা/ইংরেজি, warm-idle, `windows.open_windows`। Regression PASS + owner-এর phone test PASS না হওয়া পর্যন্ত নিচের কিছু শুরু হবে না।

### V1.2 — Live Voice (Dodaios + MYRA + Stonic থেকে)
1. **উত্তর কণ্ঠে দ্রুত শুরু:** পুরো উত্তর তৈরি হওয়ার আগে প্রথম বাক্য বলা শুরু (streaming TTS); ছোট উত্তর আগে থেকে cache।
2. **Speech Dictionary (সাধারণ):** app-এর মতো project, folder, মানুষের নাম আর owner-এর নিজের শব্দ — routing-এর আগে transcript ঠিক করা; owner-এর correction থেকে শেখা।
3. **PC-তে wake phrase + টানা কথোপকথন:** "হেই নোভা" (local wake-word, cloud-এ কিছু যায় না) → Listening → Thinking → Speaking → আবার Listening; চুপ থাকলে session শেষ।
4. **Thinking pause + মাঝপথে থামানো:** কথা শেষ হয়েছে কিনা বোঝার বিরতি; Nova বলার সময় owner কথা বললে Nova থামবে (speaker-এর echo যাচাই করে)।
5. **ভাসমান orb + শুনছে বোঝানোর আলো:** PC-তে ছোট সবসময়-উপরে window — শুনছে / ভাবছে / বলছে দেখাবে।
6. **Dictation mode:** বলা কথা সামনে খোলা app-এ লিখে দেওয়া (Desktop Worker UIA typing, BLUE)।
নিয়ম: কণ্ঠে কোনো password/secret বলা যাবে না; action-এর আগে আগের মতোই risk level + approval।

### V1.3 — শেখা ও স্মৃতি (Hermes + nanobot + Dodaios থেকে)
1. **অভিজ্ঞতা থেকে recipe:** জটিল কাজ সফল হলে Nova ধাপগুলোর একটা recipe (data, code নয়) খসড়া করবে; owner approve করলে পরের বার একই কাজ AI ছাড়া/কম AI-তে দ্রুত চলবে; ব্যর্থ হলে recipe নিজে update-এর প্রস্তাব দেবে। (Nova নিজের code কখনো বদলাবে না — এই নিয়ম অপরিবর্তিত।)
2. **পুরনো কথোপকথন খোঁজা:** "গত সপ্তাহে X নিয়ে কী বলেছিলাম" — DB-তে full-text search (SQLite FTS5) + ছোট সারাংশ।
3. **Memory একত্র করা (Dream):** মাঝে মাঝে owner-এর পছন্দগুলো একত্র করে USER profile; কী বদলাল তার log `/memory`-তে দেখা যাবে।
4. **Agent-এর নিজের নোট:** প্রতিটা specialist নিজের কাজ থেকে শেখা নোট রাখবে (project-scoped, secret বাদ)।
5. **নতুন command:** `/retry`, `/undo` (যা উল্টানো যায়), `/usage` (provider/quota হিসাব), `/insights`।
6. **Temporary chat:** যে কথা memory/history-তে যাবে না।

### V1.4 — দেখার অভিজ্ঞতা (Stonic video থেকে)
1. **Dashboard "Visual Hub":** প্রতিটা agent একটা আসনে — কে ব্যস্ত, কোন task, শেষ ধাপ (আমাদের task/agent data দিয়েই)।
2. **Memory / Skills / Character / Settings সংযোগ-চিত্র** dashboard-এ।
3. **চরিত্র/ব্যক্তিত্ব:** owner বাছাই করা কথার ধরন + কণ্ঠ (এখনকার Gemini "Achernar" কণ্ঠের সাথে মিলিয়ে)।

### V2 — বড় কাজ
1. **Realtime কণ্ঠ (Gemini Live):** earbud দিয়ে বাইরে হাতমুক্ত কথা; live model নিজে কোনো action করবে না — action Nova-র planner + approval দিয়েই যাবে; খরচ/quota সীমা মেনে।
2. **Phone থেকে live call:** Telegram voice note push-to-talk; live কথার জন্য HTTPS PWA (Cloudflare tunnel কোড আগেই আছে) — owner অনুমতি দিলে।
3. **Android Specialist:** mobilerun (MIT, Ollama চলে) বা ARTEMIS (Apache, MCP) দিয়ে owner-এর Android phone — USB debugging owner নিজে চালু করবেন; প্রতিটা action risk level + approval।
4. **MCP client:** Nova বাইরের MCP server (যেমন ARTEMIS) ব্যবহার করতে পারবে — শুধু owner-এর যাচাই করা server।
5. **একসাথে কয়েকটা coding কাজ:** প্রতিটা আলাদা git worktree-তে; কণ্ঠে "T1-কে test লিখতে বলো" ধরনের নির্দেশ।
6. **Browser অভিজ্ঞতা reuse:** সফল site-কাজের ধাপ মনে রেখে পরে দ্রুত (V1.3 recipe-এর browser রূপ)।
7. **Browser-এ element দেখিয়ে নির্দেশ** (Agent Zero annotate ধারণা)।

## ৬৬. যা নেওয়া হবে না
- কোনো framework দিয়ে Nova প্রতিস্থাপন বা তাদের code সরাসরি কপি।
- Docker-এ পুরো Linux desktop (RAM/VRAM ভারী, আমাদের PC-তে প্রয়োজন নেই)।
- Plugin/Skill marketplace থেকে স্বয়ংক্রিয় install (supply-chain ঝুঁকি) — শুধু owner যাচাই করলে।
- Telemetry পাঠানো browser/app (ego-lite) owner-এর PC-তে install।
- MYRA-র licensing/Firebase backend, অনানুষ্ঠানিক WhatsApp পড়া।
- Paid gateway (Nous Portal ইত্যাদি) বা নিজের নম্বর দিয়ে phone call — খরচ/অনুমতি ছাড়া নয়।
- Antivirus/Defender-এ exclusion যোগ করার পরামর্শ (Hermes troubleshooting) — নিরাপত্তা কমায়।

## ৬৭. Build order-এর সঙ্গে mapping
- V1.1 → এখনকার কাজ (Phase 25-এর পরে stabilization)
- V1.2 → Voice (Phase 9) + Desktop Worker (Phase 6/7) + Resource Manager
- V1.3 → Project Memory (Phase 17) + Verifier (Phase 18) + Scheduler (Phase 19)
- V1.4 → Dashboard (Phase 24)
- V2 → §৪৩ V2 Features + Android/Downloader Specialists (§৫২)
প্রতিটা ধাপে আগের মতো: test → verify → commit/push → owner-এর phone test → বাংলা summary।


## ৬৮. দ্বিতীয় পর্যালোচনা থেকে যোগ (owner-এর দেওয়া ChatGPT তালিকা, 2026-10-23)

§৬৪–৬৭-এর সঙ্গে মিলিয়ে শুধু নতুন বিষয় রাখা হলো। যেগুলো Nova-তে আগে থেকেই আছে, সেগুলো আবার বানানো হবে না: Telegram গ্রহণ আর লম্বা কাজ আলাদা queue-তে (Phase 3), Observe–Act–Verify ও প্রমাণভিত্তিক verifier (Phase 18), pause/cancel (Phase 3), project আলাদা রাখা (Phase 17), connection recovery (Phase 23/25), approval ও permission level (Phase 4/22), dashboard-এ task অবস্থা (Phase 24), Creative software নিয়ন্ত্রণের লক্ষ্য (Phase 20 foundation, §৩৭)।

| নতুন বিষয় | উৎস | কোথায় যাবে |
|---|---|---|
| "শুনেছি — এখন X করছি" তাৎক্ষণিক সাড়া (কাজ শেষ হওয়ার আগেই) | MYRA, Dodaios | V1.2 |
| কথা চলাকালীন আংশিক transcript দেখানো | Dodaios | V1.2 (PC-র live কথায়; Telegram voice note-এ সম্ভব নয়) |
| Push-to-talk (PC-তে একটা shortcut চেপে কথা) — wake word-এর বিকল্প | Dodaios | V1.2 |
| Volume / brightness / media নিয়ন্ত্রণের সরাসরি skill (AI ছাড়া) | MYRA | V1.2 |
| Flash ও Pro mode: সহজ কাজে দ্রুত এক ধাপ, বড় কাজে পরিকল্পনা + পর্যবেক্ষণ + যাচাই — দুটোতেই approval ও শেষ যাচাই থাকবে | ARTEMIS | V1.3 (operator/browser loop) |
| একসাথে কয়েকটা ধাপ (action batch/burst): একটা form-এর কয়েকটা ঘর এক AI turn-এ | ego-lite, ARTEMIS | V1.3 browser/desktop loop |
| পুরনো screenshot ও শেষ হওয়া ধাপ ছোট সারাংশে রাখা (history compression) | ARTEMIS, nanobot | V1.3 (§৫৮ token নিয়মের সঙ্গে) |
| প্রতিটা কাজের ধাপ ও screenshot রেকর্ড, পরে replay করে ভুল খোঁজা | ARTEMIS, Agent Zero | V1.3 (dashboard-এ দেখা যাবে) |
| দরকার হলে তবেই skill-এর বিবরণ AI-কে দেওয়া (on-demand skill loading) | Hermes | V1.3 |
| Skill/recipe বদলালে আগে-পরের পার্থক্য দেখানো আর আগের অবস্থায় ফেরা | Hermes | V1.3 (recipe-এর সঙ্গে) |
| Project-এর ফাইল তালিকা cache (বারবার পুরো scan নয়) | nanobot | V1.3 |
| একই ফোন একসাথে দুই agent চালাতে পারবে না (device lock) | mobilerun | V2 Android |

সিদ্ধান্ত:
- ARTEMIS আর mobilerun দুটো একসাথে নয় — V2-তে owner-এর ফোনে ছোট তুলনামূলক পরীক্ষা করে একটা বেছে নেওয়া হবে।
- Gemini Live কখনো Nova-র বাধ্যতামূলক AI হবে না; ঐচ্ছিক কণ্ঠ-মাধ্যম মাত্র।
- এই তালিকার কোনো দাবি (গতি, নির্ভুলতা) Nova-তে মাপা ছাড়া সত্য ধরা হবে না।


## ৬৯. Owner-এর সিদ্ধান্ত (2026-10-23) — §৬৪–৬৮-এর ওপর প্রযোজ্য

1. **V1.1 পরীক্ষা শক্ত হবে:** পুরোনো (V1) আর নতুন (V1.1) সংস্করণ **একই বাক্য-সংগ্রহ** দিয়ে পাশাপাশি পরীক্ষা হবে — আলাদা সংখ্যার ফল তুলনা করা চলবে না। যন্ত্রের কণ্ঠ ছাড়াও **owner-এর আসল Telegram voice message** দিয়ে পরীক্ষা হবে — বাংলা, বাংলিশ আর software-এর নাম আলাদা করে।
2. **Whisper model বাধ্যতামূলক বদল নয়:** বর্তমান large-v3, large-v3-turbo এবং দরকার হলে ছোট model (medium) তুলনা হবে — গতি, Premiere Pro / After Effects / VS Code ইত্যাদি নাম ঠিক চেনা, এবং ১৬ GB RAM ও VRAM-এর ওপর চাপ — তিনটাই মেপে তবেই সিদ্ধান্ত।
3. **V1.2 wake word ও টানা শোনা ঐচ্ছিক:** "হেই নোভা", মাঝপথে থামানো, earbud, dictation — settings থেকে চালু/বন্ধ; microphone সবসময় চালু রাখা বাধ্যতামূলক হবে না (push-to-talk ও Telegram voice আগের মতো থাকবে)।
4. **V1.3 শেখা নিয়ন্ত্রিত:** কোনো recipe/skill স্থায়ী হবে শুধু **verifier PASS → owner approval** দুই ধাপ পার হলে; ব্যর্থ বা যাচাইহীন পদ্ধতি কখনো শেখা হবে না; প্রতিটা পরিবর্তনের diff ও আগের অবস্থায় ফেরা থাকবে।
5. **V1.4 (Agent Office, Character) কম অগ্রাধিকার:** মূল কাজের গতি কমাবে না; dashboard বন্ধ থাকলে কোনো RAM/CPU খরচ নয়; animation হালকা।
6. **পুরোনো V2 কিছুই বাদ নয়:** §৪৩-এর সব (Gmail, Calendar, Google Drive, উন্নত WhatsApp, Facebook/TikTok, Social Selling, Advanced Creative/Video Automation, Wake-on-LAN, Remote File Transfer, Advanced browser profiles, Multiple simultaneous agents, Multi-PC control, Advanced web dashboard) আর §৫৫–৫৬ যেমন ছিল তেমনই থাকবে; §৬৫–৬৮-এর V2 বিষয়গুলো এগুলোর **সঙ্গে যোগ**, বদলে নয়।


## ৭০. খোলা app-এর ভেতরের কাজ + Stonic AI পর্যালোচনা (2026-10-23)

**V1.1-এ যোগ হয়েছে (owner-এর আসল ব্যবহার থেকে):** "OBS এখন কি অবস্থা / কি দেখছ" আগে OBS আবার খুলত বা web থেকে উত্তর দিত; "OBS update চাইছে, skip করো" আগে process তালিকা দিত।
- `app-control.view` — খোলা app-এর window, pop-up (লেখা + বোতাম) আর ছোট লেখা পড়ে বলে (UIA, শুধু পড়া)। "খুলে দেখো" হলে আগে খোলে; "খোলা তো আছে" হলে কখনো খোলে না।
- ~~`app-control.choose`~~ — নির্দিষ্ট বোতাম-নাম ধরে skip; **বাদ (§৭১, hardcoded নিয়ম)** — pop-up-এর উত্তর General Computer-Use Engine দেবে।
- আগে লুকানো একটা ভুলও ঠিক হলো: "প্রিমিয়ার প্রো খোলো" fast lane-এ যাচ্ছিল না।

**Stonic AI 3.1.18 (owner-এর দেওয়া installer) — শুধু ভেতরের ফাইল পড়ে দেখা, চালানো হয়নি:**
- বন্ধ-উৎসের পয়সার Electron app; মূল কোড bytecode/obfuscated, installer-এ signature নেই, email + hardware-ID দিয়ে telemetry, বাধ্যতামূলক login। ভেতরে খোলা-উৎসের Hermes Agent (MIT), Agent S3 (computer use), Pipecat (voice) বাঁধা।
- সিদ্ধান্ত: **Nova-তে Stonic-এর কোনো ফাইল/binary চালানো হবে না, install-ও সুপারিশ নয়।** ভালো ধারণাগুলো Nova নিজে বানাবে:
  1. **Esc দিয়ে থামানো** — কাজ চলার সময়ই শুধু keyboard hook; Nova-র নিজের পাঠানো (injected) Esc আলাদা চেনে। → V1.2 (UI automation-এর নিরাপত্তা, আগে)।
  2. **সজাগ থাকা app (sidecar)** — model গরম রাখে, NDJSON-এ কথা, "stop" = শুধু কাজ থামানো। → V1.2।
  3. **হালকা নজরদার** — সামনের window, mic কে ব্যবহার করছে, display জেগে আছে কিনা; বদলালেই শুধু খবর। → V1.3 proactive।
  4. **PowerShell runner** — `-NoProfile -NonInteractive`, command env দিয়ে, timeout-এ পুরো process tree বন্ধ। → V1.2 (বর্তমান runner-এর সাথে মিলিয়ে)।
  5. **API key Windows DPAPI-তে encrypt** — UI শুধু শেষ ৪ অক্ষর দেখে। → V1.3 (secrets/.env-এর বিকল্প হিসেবে বিবেচনা)।
  6. **Tool-এর ফল কাঠামোবদ্ধ** (errorCode, retryable, retrySuggestion) → verifier-এ। → V1.3।
  7. Hermes Agent ও Agent S3 দরকার হলে তাদের নিজের GitHub উৎস থেকে, তাদের license মেনে (§৬৪-এ hermes-agent আগেই আছে)।
- নেওয়া হবে না: obfuscated কোড, hardware-fingerprint telemetry, vendor cloud দিয়ে remote control, অনুমতি-স্তর ছাড়া os_exec / mouse।


## ৭১. General Computer-Use Engine — যেকোনো app, hardcoded নিয়ম ছাড়া (owner অনুমোদিত 2026-10-24)

**লক্ষ্য:** Nova আজকের PC-র কয়েকটা app-এর bot নয়। কাল নতুন app install হলেও, UI বদলালেও, core code না বদলে কাজ করার চেষ্টা করবে। App catalog শুধু app খুঁজে পাওয়ার জন্য; নির্দিষ্ট app-নাম / বোতাম-নাম / coordinate / layout ধরে core logic লেখা নিষেধ। কোনো app-এর নির্ভরযোগ্য API/SDK থাকলে সেটা ঐচ্ছিক adapter — তা ছাড়াও সাধারণ engine কাজ করবে। (§৭০-এর OBS-নির্দিষ্ট skip নিয়ম এই কারণে বাদ।)

**কাজের ধারা:** নির্দেশ → উদ্দেশ্য বোঝা → পথ বাছাই → দেখা → পরিকল্পনা → নিরাপদ কাজ → ফল দেখা → যাচাই → শোধরানো / পরের ধাপ।
- **পথ বাছাই (tiered):** সহজ নির্দিষ্ট কাজ → fast path (বর্তমান skill, AI ছাড়া) · যার API/CLI/COM আছে → native tool (Office COM, browser DOM, পরে Premiere ইত্যাদি) · অচেনা GUI → General Computer-Use Agent · বড় কাজ → planner + specialist + verifier।
- **দেখা (Observer):** UIA/Accessibility tree (cached, শুধু কাজের control, তাদের ঘর) + window তথ্য + শুধু সেই window-এর screenshot। tree প্রায় খালি হলে (Electron/web-ধাঁচ, canvas, Adobe) vision দিয়ে উপাদান শনাক্ত; দুই তালিকা overlap বাদ দিয়ে মেলানো, প্রতিটায় নম্বর বসানো ছবি (set-of-marks) — UFO-র ধারণা (MIT)।
- **Grounding:** নম্বর না থাকলে বর্ণনা → ছবিতে জায়গা (Agent S3-এর ধারণা, Apache-2.0)। মডেল মেপে বাছাই: বর্তমান qwen3-vl বনাম UI-TARS-1.5-7B (local, on-demand, idle-এ unload); Gemini শুধু শেষ fallback।
- **কাজ (Actor):** UIA pattern আগে (Invoke/Value/Toggle/Select/Expand/Scroll), না হলে উপাদানের ঘরের মাঝে mouse/keyboard। কাজের তালিকা: click, double/right click, type/set text, hotkey, scroll, drag-drop, menu, wait, ask, done, fail।
- **Loop:** প্রতি ধাপে একটা (নিরাপদ হলে কয়েকটা) কাজ → আবার দেখা → অগ্রগতি যাচাই (Agent S3 reflection); আটকে গেলে অন্য পথ; সীমা পেরোলে সৎভাবে জানানো। শুরুতেই "কী হলে কাজ শেষ" (expected result) ঠিক — verifier সেটার প্রমাণ দেখবে (Task Goal → Expected Result → Evidence → Verdict)।
- **নিরাপত্তা (অপরিবর্তিত + নতুন):** RED/YELLOW approval, privacy, resource manager কোনো fast/visual পথে bypass নয়। ঝুঁকির কাজ (install/update/delete/send/pay, অচেনা coordinate click) → owner-কে ছবিসহ ✅। অস্পষ্ট হলে আন্দাজ নয়, প্রশ্ন। **Esc kill-switch** (Nova-র নিজের পাঠানো key আলাদা চেনে) আর **owner keyboard/mouse ধরলে Nova থামে**। screen-এর লেখা untrusted data।
- **Progress:** Telegram-এ একটা বার্তা প্রতি ধাপে বদলায় (ধাপ + ছোট ছবি); cancel / checkpoint / safe resume বর্তমান ব্যবস্থা দিয়ে।

**নেওয়া / না-নেওয়া:** UFO (MIT) আর Agent S3 (Apache-2.0)-এর নির্দিষ্ট pattern license notice রেখে Nova-র ভেতরে; পুরো framework install নয় (UFO: ৪৫ dependency + নিজের server, Nova-র permission মানে না; Agent S: AI-লেখা code exec করে)। OmniParser-এর detector license (AGPL কিনা) যাচাই ছাড়া নয়।

**পরীক্ষা:** ব্যক্তিগত test app (Win32 dialog, WinForms, WPF, Tk, web/Electron-ধাঁচ, accessibility-হীন canvas) + এলোমেলো pop-up; একটা অংশ held-out (আগে দেখানো হয় না, বোতাম-নাম বদলানো, বাংলা নাম)। মাপ: unseen success rate, task accuracy, wrong-action rate, recovery rate, time-to-first-action, মোট সময়, token/RAM/VRAM। কিবোর্ড/মাউসের পরীক্ষা Windows Sandbox-এ (owner-এর desktop ছোঁয় না); owner-এর আসল app-এ পরীক্ষা শুধু আগে জানিয়ে। Regression-এ একটা fail থাকলেও PASS নয়; pytest-এর নিজের exit code আর summary দুটোই দেখা হবে (pipe-এর exit code নয়)।

**সীমাবদ্ধতা (সৎভাবে):** ১০০% কোথাও নয় (সেরা agent-ও কঠিন benchmark-এ ~৭০%); timeline/3D/canvas/game/DRM-এ কম; elevated window শুধু broker দিয়ে; UAC secure desktop কখনো নয়; প্রতি ধাপ ~৩–১৫ সে.।

**আগের গবেষণার সঙ্গে সম্পর্ক (§৬৪–§৭০):** mobilerun (accessibility tree + screenshot, local model) আর Agent Zero (element দেখিয়ে নির্দেশ) এই Observer-এর একই ধারা; ego-lite-এর "সফল কাজ মনে রেখে পরে দ্রুত" → V1.3 recipe-এ computer-use trajectory-ও যাবে; Hermes/nanobot-এর শেখা ও memory → V1.3 (verifier PASS + owner approval ছাড়া কিছু স্থায়ী নয়, §৬৯-৪); Dodaios/MYRA-র কণ্ঠ ও orb → V1.2 (CU-এর progress ওখানেও দেখাবে); Stonic থেকে Esc kill-switch ও সজাগ sidecar (§৭০) → CU-4 ও CU-5; ARTEMIS/mobilerun-এর Android → V2-তে একই Observe→Act→Verify চক্র ফোনে।

**Roadmap-এ অবস্থান:** V1.1 (বাকি: owner-এর ১০টা আসল phone voice test) → **CU (এই §, CU-0…CU-8)** → V1.2 → V1.3 → V1.4 → V2। V1.2–V2-এর কোনো feature বাদ নয় (§৬৫, §৬৯-৬)। Phase 21 WhatsApp আগের মতো deferred।

**Dependencies:** নতুন বাধ্যতামূলক কিছু নয় — uiautomation, pywin32, mss/Pillow, Playwright, Ollama/llama.cpp আগে থেকেই আছে। ঐচ্ছিক: UI-TARS-1.5-7B (Apache-2.0, ~৫ GB, CU-2-এ মেপে তবেই), Windows Sandbox (Windows-এর নিজের, শুধু test-এর সময় ~৪ GB RAM)। UFO/Agent-S package install নয়।

**Performance লক্ষ্য:** নির্দেশ পাওয়ার জানান < ১ সে.; fast-path কাজ < ৩ সে.; Observer (শুধু UIA) প্রতি window < ১.৫ সে.; vision লাগলে প্রথম কাজ < ১৫ সে.; প্রতি ধাপ সাধারণত ৩–১৫ সে.; ভারী model (grounding/vision) একসাথে একটার বেশি নয়, idle-এ unload; streaming progress আর আসল কাজের গতি আলাদা মাপা হবে — ভুয়া progress নয়।

**ধাপ (প্রতিটা আসল পরীক্ষায় PASS হলে তবেই পরেরটা):**
| ধাপ | কাজ | PASS শর্ত |
|---|---|---|
| CU-0 | পরিষ্কার: §৭০-এর app-নির্দিষ্ট skip বাদ, সঠিক exit-code দিয়ে test চালানো | পুরো suite 0 fail, exit code 0 |
| CU-1 | Observer: control তালিকা + window ছবি + নম্বর-ছবি + "প্রায় খালি" চেনা | ৫ ধরনের test app-এ সঠিক control, প্রতি window < ১.৫ সে. |
| CU-2 | Vision উপাদান-শনাক্ত + grounding model মাপা | accessibility-হীন test app-এ বোতাম খুঁজে পাওয়া; মডেলের গতি/নির্ভুলতা/VRAM টেবিল |
| CU-3 | Actor: নম্বর/বর্ণনা ধরে কাজ, UIA আগে, না হলে input | সব test app-এ click/type/scroll/drag/menu সঠিক, ভুল-কাজ 0 |
| CU-4 | নিরাপত্তা: Esc kill-switch, owner-input pause, ঝুঁকির কাজে ছবিসহ ✅ | Esc-এ < ০.৫ সে.-এ থামা; danger বোতামে কখনো নিজে চাপ নয় |
| CU-5 | Computer-Use Loop + লক্ষ্য-ভিত্তিক verifier + Telegram progress | dev test-এ ≥৮০%, held-out-এ মাপা ফল রিপোর্ট, ভুল-কাজ 0 |
| CU-6 | Windows Sandbox test lab + metrics রিপোর্ট | পুরো benchmark একটা command-এ চলে, owner-এর desktop না ছুঁয়ে |
| CU-7 | Native adapter-এর কাঠামো (প্রথমে Office COM) | Word/Excel-এ API পথে কাজ + GUI fallback |
| CU-8 | owner-এর আসল ব্যবহারে ছোট পরীক্ষা (আগে জানিয়ে) | owner-এর দেওয়া ৫টা কাজের ফল |


## ৭২. গবেষণা রিপোর্ট (A–G, 2026-10-24) মিলিয়ে CU ধাপের হালনাগাদ

**উৎস:** owner-এর দেওয়া ChatGPT গবেষণা — Report A (১১ repo-র source), B (Nova gap audit, commit b8c4884), C (architecture), D (priority), E (model/voice মাপার পদ্ধতি), F (implementation spec), G (evidence/license)। `D:\Personal Ai Assitant\Report`-এ আছে (repo-তে নয়)। এগুলো মূল technical reference; অন্ধভাবে নয় — Nova-র code-এ যাচাই করে নেওয়া।

**Code-এ যাচাই করা মূল সত্য (রিপোর্টের সঙ্গে মিলেছে):**
- `check_effect()` অচেনা effect-এ `True (tool-confirmed)`; `files.write` শুধু "file আছে" দেখে, content নয়; Store app-এ শুধু window-flag — ভুল PASS সম্ভব (B-01, **P0**)।
- Operator text-only: observer-এর ছবি planner পায় না, desktop-এ vision grounding নেই (B-02)।
- `vision.locate()` fixed 1280×720 — desktop window/DPI-তে চলবে না (B-03)।
- নিরাপদ-দেখতে label মানেই নিরাপদ কাজ নয়; safety check আর আসল কাজের মাঝে target বদলাতে পারে (B-04, P0)।
- Screenshot "local first" শুধু ranking; cloud-এ না যাওয়া enforce হয় না (B-06, P0)।
- নিজের পরীক্ষায় পাওয়া: background worker জানালা সামনে আনতে পারে না (CU-1 নোট)।

**সিদ্ধান্ত:** ১১টা framework জোড়া নয় — Nova-র queue, worker IPC, SkillRunner, approval, router, resource manager রেখে **observation → grounded action → independent goal verification** শক্ত করা। নেওয়া pattern: Hermes `ActionResult` (transport ok ≠ effect verified) ও stale element token; Dodaios frame-scoped batch, foreground signature, `CoordinateMapper` (DPI/negative monitor), desktop lock, Escape; ego-lite "stale ref কখনো একই label-এর অন্য বোতামে যাবে না"; OpenClaw outcome-hash দিয়ে no-progress loop ধরা; Artemis-এর আলাদা read-only checker কিন্তু **INCONCLUSIVE কখনো PASS নয়**; Agent-S planner/grounder আলাদা, কিন্তু প্রতি click-এ বহু LLM call নয়; UFO cached UIA (CU-1-এ নেওয়া)। License: সব repo MIT/Apache-2.0; code নিলে notice রাখা (G)।

**নতুন ক্রম (§৭১-এর CU-2…CU-8 এই ক্রমে প্রতিস্থাপিত; CU-0, CU-1 শেষ):**
| ধাপ | কাজ | PASS শর্ত |
|---|---|---|
| CU-2 | **Verifier সঠিকতা (P0):** অচেনা effect → INCONCLUSIVE/LIMITED, PASS নয়; tool-success ≠ goal-success (typed `ActionOutcome`: transport_ok / delivered / effect=confirmed·noop·unknown); file-এ content/size predicate; app-open-এ সত্যিকারের window+process; UI কাজে readback; screenshot/vision request-এ `local_only` enforce | B-এর ৩টা probe + wrong content / wrong window / empty evidence / stale proof → কখনো PASS নয়; পুরোনো সঠিক পথ অক্ষত |
| CU-3 | **নিরাপদ লক্ষ্য-পরিচয়:** observation_id + opaque element token (hwnd+pid+process start+runtime id), stale হলে বাতিল (চুপচাপ অন্য বোতাম নয়); কাজের ঠিক আগে window/frame আবার মেলানো; approval-এ target identity বাঁধা; coordinate mapper (DPI, negative monitor); একটাই desktop lock; foreground পাওয়া (worker focus fix); owner input/Escape-এ থামা | duplicate label, বদলে যাওয়া element, সরানো/resize window, ভুল foreground, দেরিতে popup, locked desktop → ভুল input শূন্য |
| CU-4 | **সাধারণ perceive-act-verify executor** (operator-এর পাশে, নতুন engine নয়): ছবি আলাদা channel-এ (base64 text-এ নয়); UIA আগে, কম হলে crop → vision grounding; `not_found` → scroll/crop/প্রশ্ন, আন্দাজে click নয়; model-এর "done" = যাচাইয়ের অনুরোধ; no-progress loop ধরা | accessible ও canvas lab app-এ একই executor; held-out-এ app/বোতাম-নাম বদলেও চলে |
| CU-5 | কণ্ঠ/app fast path: stage timer আছে — যোগ: compound নির্দেশ ("X খোলো তারপর Y") বাকি লক্ষ্য না হারানো, কাজ শুরু হলে সত্য ack, exact app-এ ASR-এর পরে শূন্য LLM call | একই corpus-এ wrong-app বাড়ে না; P50/P95 |
| CU-6 | নিরাপদ batching (boundary-তে fresh observe), browser session reuse মেপে, recovery category (stale/ambiguous/noop/timeout/worker_lost/auth/locked/user_intervened/policy_denied), restart-এ অনিশ্চিত কাজ replay-এর আগে postcondition পড়া | duplicate submit নেই; cancel মানা হয় |
| CU-7 | Live execution UI: typed event (received→transcript→target→approval→action→effect→verdict), dashboard-এ authenticated stream, target overlay, Telegram progress edit; ভুয়া % নয় | stop LLM ছাড়াই দ্রুত; password অঞ্চল mask; idle-এ capture বন্ধ |
| CU-8 | Model মাপা (E-এর protocol): qwen3-vl বনাম UI-TARS-1.5-7B (owner অনুমোদিত, বিনামূল্যে) একই crop/target set-এ — নির্ভুলতা/গতি/VRAM | শুধু মাপা Pareto ফল; না চললে NOT_TESTED |
| CU-9 | Generalization + security release gate (F-এর matrix): Win32/WinForms/WPF/WinUI/web/Qt/Tk/canvas, layout held-out, recovery, prompt-injection, human intervention; Windows Sandbox; "N বারে 0 false PASS" ভাষায় রিপোর্ট | false PASS দেখা গেলে release বন্ধ |

**যা বদলাবে না:** §১–§৭১-এর সব feature, V1.2–V2, WhatsApp deferred। নতুন paid service / remote server নয়। প্রতিটি ধাপ: ছোট diff, feature flag যেখানে আচরণ বদলায়, focused + পুরো regression (pytest-এর নিজের exit code), আসল যাচাই, commit (owner-এর নির্দেশে প্রতি milestone-এ)।

**খোলা বিষয় (owner জানবেন):** Nova repo-তে root LICENSE নেই (public repo) — owner-এর সিদ্ধান্ত।

**অবস্থা (2026-10-24):** CU-0 ✓ · CU-1 Observer ✓ · CU-2 Verifier ✓ · CU-3 লক্ষ্য-পরিচয় + owner নিয়ন্ত্রণ ✓ · CU-4 সাধারণ loop ✓ — নিজের PC-র local model (qwen3 8B, cloud নয়) দিয়ে আগে-না-দেখা ৪টা lab কাজ, প্রতিটা ৩ বার: শেষ সংস্করণে ১২/১২ যাচাই-সহ সফল (WPF ৩/৩, একই-নামের-বোতাম ৩/৩, web ৩/৩, dialog ৩/৩; ৫–২৬০ সে.)। আগের দফাগুলোয় পাওয়া ভুল (না-চাওয়া Save/Upload, দ্বিতীয় Save, বারবার toggle) সাধারণ নিয়মে বন্ধ: একই-নামের বোতামে ক্রম, goal-এ যতবার নাম ততবারই চাপ, না-বলা commit/confirm বোতাম কখনো নয়। সীমা: ছোট local model কখনো আটকে যায় (no-progress-এ নিরাপদে থামে); একটা task-এ ৫–২৬০ সে.; vision grounding (canvas) এখনো শুধু scripted test-এ — আসল vision model মাপা CU-8-এ।

**CU-8 ফল (2026-10-24, `scripts/grounding_bench.py`):** একই ৫৫টা লক্ষ্য (৬ ধরনের lab-জানালা, তার একটা ঘন «আগে-না-দেখা» editor-পাতা — শুধু-icon বোতাম সহ); সঠিক জায়গা UIA/app-এর নিজের আঁকা থেকে, মডেল থেকে নয়; GPU-তে একবারে একটাই মডেল; RAM/VRAM পাহারা (টানা কম হলে থামে — প্রথম চেষ্টায় সত্যিই থেমেছিল, llama.cpp-এর mmap; `--load-mode none` দিয়ে ঠিক)।

| মডেল | নির্ভুল | সাধারণ সময় | p90 | GPU-বৃদ্ধি | runtime |
|---|---|---|---|---|---|
| qwen3-vl:8b Q4_K_M (digest 901cae73…, Nova-র এখনকার setting) | **৪৬/৫৫** | ৭.৯ s | ৩৯ s | ৯.৭ GB | Ollama 0.40.9 |
| UI-TARS-1.5-7B Q4_K_M + mmproj f16 (mradermacher, sha256 মিলানো) | ৪২/৫৫ | **০.২ s** | **০.২২ s** | **৭.০ GB** | llama.cpp b11850 CUDA 13.4 |

- দুটোই ভুল করে একই জায়গায়: checkbox/radio-র লেবেলে নির্দেশ (বাক্সে নয়) আর অস্পষ্ট icon। ৫৫টায় ৪-এর পার্থক্য পরিসংখ্যানে নিশ্চিত নয়।
- qwen3-vl `think: false` সত্ত্বেও প্রতি উত্তরে গড়ে ~৬০০ token তৈরি করে — ধীরতার কারণ এটাই।
- UI-TARS-এর স্থানাঙ্ক llama.cpp-এ ছবির নিজের pixel-এ (কৃত্রিম বোতাম-ছবিতে calibration, পরীক্ষার ছবিতে নয়)।
- **সিদ্ধান্ত:** স্থায়ী model বদল এখন নয় (owner-এর নিয়ম)। প্রস্তাব (owner অনুমোদন সাপেক্ষে): `locate` ধাপে UI-TARS প্রথমে (দ্রুত), ব্যর্থ/অনিশ্চিত হলে qwen3-vl; ক্লিকের পর effect-যাচাই আগের মতোই বাধ্যতামূলক। qwen3:8b-এর সঙ্গে VRAM-এ একসাথে প্রায় ১৪.৫ GB — তাই একটার পর একটা।

**CU-9 ফল (2026-10-24):**
- **Windows Sandbox:** এই PC-তে চালু হয় না। একদম খালি config-এও `0x80070020` দেখায়। বিকেল ৪:১৬-র Store update (0.8.107.0)-এর পর থেকে এই সমস্যা।
  - Owner-এর অনুমতিতে চেষ্টা করা হয়েছে: দুবার restart, CmService/vmcompute/hns সেবা restart, Reset-AppxPackage, Hyper-V-All চালু করা। কোনোটাতে সারেনি।
  - Sandbox-এর জন্য লেখা script (`scripts/sandbox_lab.py`, `scripts/sandbox/`) একবারও চলেনি, তাই commit করা হয়নি।
- **নিরাপদ বিকল্প (owner অনুমোদিত):**
  - শুধু আগে-না-দেখা আসল app: portable Notepad++ 8.9.8.1 (Win32+Scintilla) ও KeePassXC 2.7.12 (Qt), checksum মিলিয়ে workspace-এ রাখা; আর Windows-এর নিজের Character Map। কিছু install হয়নি।
  - চালক `scripts/realapp_lab.py`: owner-এর খোলা app ছোঁয় না, শুধু নিজের চালু করা প্রক্রিয়া বন্ধ করে।
  - প্রতিটা কাজের শেষ অবস্থা আলাদাভাবে মেলানো হয়: ফাইল হুবহু মিলতে হবে, ঘরের লেখা হুবহু মিলতে হবে। কাজের আগেই শর্ত সত্যি থাকলে সেই কাজ গোনা হয় না।
- **কড়া মিলানোয় আসল ভুল ধরা পড়েছে:**
  - প্রথম দিকে ২টা ভুল PASS।
  - password ভুল (সাধারণ) ঘরে লেখা।
  - Notepad++-এর লেখার জায়গা Nova-র তালিকায় ছিল না।
  - বড় পর্দায় qwen3 চিন্তা করতে করতে ফাঁকা উত্তর দেয় (চিন্তা চালু ০/২, ৬০০০ token পর্যন্ত; চিন্তা বন্ধ ২/২, ০.৬–৩ সেকেন্ড)।
  - সামনের জানালা জানতে গিয়ে COMError-এ worker ভেঙে যেত।
- **সাধারণ সংশোধন (কোনো app-এর নাম নয়):**
  - focus নিতে পারে এমন বড় Pane (লেখা/আঁকার জায়গা) এখন তালিকায় আসে, নতুন `type` op দিয়ে: শেষে লেখে, বা `at: cursor` দিলে cursor-এ।
  - UIA IsPassword → RED `ui.type_secret`। নির্দেশে দেওয়া password/PIN যেকোনো ঘরে লিখতে গেলেও RED।
  - Artemis-ধাঁচের স্বাধীন বিচারক: শুধু goal আর পর্দা দেখে; তার «হ্যাঁ» ছাড়া PASS নয়।
  - নির্দেশের শব্দ বা উদ্ধৃতির বাইরের লেখা টাইপ হওয়ার আগেই আটকায়। একই লেখা দ্বিতীয়বার টাইপ হয় না।
  - ফাঁকা উত্তর এলে একবার চিন্তা-ছাড়া আবার জিজ্ঞেস করে। হ্যাঁ/না প্রশ্নে চিন্তা বন্ধ থাকে।
  - `front_window()` COMError-এ ভাঙে না।
  - বিচারক-মাপ (৩০টা যাচাই): চিন্তা-বন্ধ ২৪/৩০, চিন্তা-চালু ২৫/৩০ কিন্তু ১২ গুণ ধীর → চিন্তা-বন্ধ রাখা হয়েছে, আর নির্দিষ্ট নিয়মের সঙ্গে জোড়া।
- **সংশোধনের পরে আসল app-এ ফল: ২১ বারে ০টা ভুল PASS।**
  - Character Map ৯/৯ সঠিক (একবার Nova নিজে শেষ না বলে থেমেছে)।
  - Notepad++ Find ৩/৩।
  - KeePassXC password: ৩/৩ password লেখা হয়নি (২ বার RED অনুমোদন চেয়ে থেমেছে)। আসল Qt password ঘর password=True হিসেবে আসে, ভেতরের লেখা কখনো পড়া হয় না।
  - **ব্যর্থ ৬/৬:** Notepad++-এ «নতুন লাইনে শেষে লেখো» ও «প্রথম লাইনের শেষে যোগ করো»। ছোট AI নতুন লাইন বা cursor ঠিক করতে পারে না। প্রতিবারই সৎভাবে থেমেছে, ভুল দাবি করেনি।
- **CU-4 lab regression (আসল qwen3 8B):** ৮/৮ যাচাই-সহ সফল, বিচারকও প্রতিটায় হ্যাঁ; সময় ৫–৮৩ সেকেন্ড।
- **পুরো test:** ১০২০ পাস।
- **বাকি:**
  - cursor/লাইন বসানোর দক্ষতা (বড় মডেল বা UI-TARS locate)।
  - Sandbox — Microsoft-এর পরের update-এর পর আবার চেষ্টা।
  - owner-এর অনুমতি নিয়ে owner-এর নিজের দৈনন্দিন app-এ পরীক্ষা।
  - UI-TARS locate flag (owner অনুমোদিত)।

**CU-9-এর পরে সংশোধন (owner-এর নির্দেশ, 2026-10-25) — প্রতিটা সাধারণ নিয়মে, কোনো app-এর নাম নয়:**

| ধাপ | কী | automated | আসল app |
|---|---|---|---|
| ১ — লাইন/cursor | planner শুধু কোথায় লিখবে বলে (`end`, `new_line`, `line_end:N`, `line_start:N`, `cursor`); Nova নিজে নির্দিষ্ট key-ক্রমে cursor সরায়। planner না বললে owner-এর কথা থেকে জায়গা ঠিক হয় (নতুন লাইন / প্রথম লাইনের শেষে)। উদ্ধৃতি-চিহ্ন বাদ যায়। locate দিয়ে লেখা «খোঁজা» আর ক্লিকে পরিণত হয় না। | পাস | Notepad++ লাইনে লেখা: আগে ০/৬ → এখন ৪/৪ শেষ অবস্থা সঠিক |
| ২ — গোপন তথ্য | `ui.type_secret` এখন SECRET_ACTIONS-এ: approval-বার্তা ও DB-তে password থাকে না। নির্দেশের password/PIN/OTP, key-আকৃতি আর card নম্বর (Luhn) যেকোনো ঘরে RED। log, ইতিহাস ও event-এ «•••»। এমন নির্দেশের পরিকল্পনা শুধু এই PC-তে (local_only)। | পাস | KeePassXC ৪/৪ password লেখেনি; log-এ ০ বার; Documents-এ কোনো ফাইল নয় |
| ৩ — যাচাই | বিচারকের আগে নির্দিষ্ট প্রমাণ: Nova যা টাইপ করেছে তা আছে কিনা, বেশি বার কিনা, নতুন লাইনে বা নির্দিষ্ট লাইনের শেষে/শুরুতে কিনা। editor-এর পুরো লেখা (২০ হাজার অক্ষর পর্যন্ত) পড়া হয়। উদ্ধৃত লেখা হুবহু পুরোটা হতে হবে। | পাস | বিচারক একা: ৫১/৬০ (৯টা ভুল হ্যাঁ, মাত্র ৩টা ক্ষেত্রে); সেই ৩টাই এখন নির্দিষ্ট নিয়মে ধরা পড়ে। কড়া প্রশ্নে লাভ হয়নি (৫১/৬০), তাই প্রশ্ন বদলানো হয়নি |
| ৪ — UI-TARS | `models.yaml → grounding.engine` (শুরুতে `vision` = আগের মতো)। `ui-tars` হলে locate আগে UI-TARS (llama.cpp, Ollama মডেল নামিয়ে, অলস হলে বন্ধ), না পেলে আগের qwen3-vl। | পাস | আঁকা বোতাম, পুরো কাজ: vision ১/৪ (গড় ২১৯ সে.) বনাম UI-TARS ৪/৪ (গড় ১০৮ সে.)। default এখনো বদলানো হয়নি — আরও রাউন্ড মাপা হচ্ছে |

পথে পাওয়া ও সারানো অবনতি:
- হ্যাঁ/না প্রশ্নে চিন্তা বন্ধ করায় Notepad++ Find ৩/৩ থেকে ০/২-এ নেমেছিল। চিন্তা ফিরিয়ে আনা হয়েছে, আর মেনুর লেখা পরিষ্কার করে মেলানো হয় (Windows-এর mnemonic, «...», shortcut বাদ)। এখন আবার ৩/৩।
- লেখা/আঁকার জায়গা তালিকায় আসার পর planner ক্যানভাসকেই ক্লিক করত। কোনো জিনিস যে কাজ নেয় না, সেটা চাইলে এখন সঙ্গে সঙ্গে বাধা দেয় আর locate-এর পথ দেখায়।
- ছবি-সহ পরিকল্পনার token-সীমা ৬০০ থেকে ২০০০ (qwen3-vl চিন্তা বন্ধ থাকলেও ~৬০০ token খরচ করে)।

সব আসল চালানোয়: **০টা ভুল PASS, ০টা ভুল জায়গায় গোপন তথ্য।**

**UI-TARS সিদ্ধান্ত (2026-10-25, আসল পুরো-কাজের মাপ, মোট ১০ বার করে):** vision ৬/১০, UI-TARS ৭/১০; কোনোটাই কখনো ভুল বোতামে ক্লিক করেনি; সব ব্যর্থতা planner-এর পুনরাবৃত্তি থেকে, খোঁজার ভুল নয়। পার্থক্য স্পষ্ট নয় → **default `vision` থাকছে**, UI-TARS ঐচ্ছিক flag।

**V1.2 অগ্রগতি (2026-10-25):**
- ✓ **Volume / mute / media / brightness, AI ছাড়া** (`device` skill, commit 258a5ba)।
  - বাংলা, বাংলিশ আর ইংরেজি নির্দিষ্ট নিয়ম → fast lane।
  - Volume: Windows Core Audio, কোনো নতুন library নয়। Media: স্বাভাবিক media key।
  - Brightness: DDC/CI বা WMI; সম্ভব না হলে সৎভাবে জানায়।
  - আসল পরীক্ষা: volume পড়া ০.৬ সেকেন্ডে; ৭৪→৭০→৭৪%, আলাদাভাবে মিলিয়ে দেখা।
  - media key: owner নিজে Telegram থেকে «পরের গান» দিয়ে আসল PC-তে যাচাই করেছেন (2026-10-25) ✓। brightness বদলানো এখনো চালানো হয়নি — monitor «০» জানায়, তাই নির্ভরযোগ্য নয়।
- ✓ **«শুনেছি — এখন X করছি» তাৎক্ষণিক সাড়া** (commit adc6aac)।
  - router-এর একই নির্দিষ্ট নিয়ম থেকে, কোনো AI নয়; অস্পষ্ট হলে কোনো দাবি করে না।
  - কণ্ঠ-বার্তার «🎙️ শুনলাম: «…»» আগে থেকেই ছিল।
- ✓ **Speech Dictionary** (owner-এর নিজের শব্দ ও শেখা সংশোধন, কণ্ঠের লেখা router-এ যাওয়ার আগে)।
  - শেখানো: «শব্দ শেখো: পিছি মানে পিসি», «শব্দ শেখো: <নাম>», `/words`; দেখা/মোছা নির্দিষ্ট নিয়মে, AI ছাড়া।
  - ✅ চাপা থেকে শেখা: একই শব্দ-বদল দুবার নিশ্চিত হলে তবেই নিজে থেকে চালু; পুরো বাক্য বদলালে কিছু শেখে না।
  - বাংলা শব্দের শেষাংশ (র, এর, কে, তে…) রেখে বদলায়; য়/ড়/ঢ়-এর দুই বানানই মেলে; গোপন-দেখতে কিছু রাখে না।
  - যা শোনা হয়েছিল তা দেখানো থাকে, সাথে «📖 অভিধানে ঠিক করলাম: …»।
  - আসল মাপ (Whisper large-v3, দুই edge বাংলা কণ্ঠ): owner-এর শব্দ hotwords হিসেবে দিলে ০/৬ → ৬/৬ ঠিক; একই শব্দ initial_prompt-এ দিলে ৫/৬ আর «পিসির অবস্থা» ইংরেজি হয়ে যায় — তাই hotwords। সাধারণ ৬টা বাক্যের কোনোটা খারাপ হয়নি।
- ✓ **PC-তে push-to-talk** (commit 9d2df0e, 0972e5e)।
  - Ctrl+Alt+Space চেপে ধরে কথা; microphone শুধু ধরে রাখার সময় খোলা; Windows-এর নিজস্ব winmm/RegisterHotKey/winsound, নতুন library নয়।
  - Telegram voice-এর একই পথ (অভিধান, নিশ্চিতকরণ, অনুমোদন); লেখা Telegram-এ, কণ্ঠ PC-র speaker-এ।
  - owner-এর আসল পরীক্ষা (2026-10-25): ৩ বারে ২টা ঠিক বুঝে উত্তর; ১.২ সেকেন্ডের টুকরো থেকে Whisper আবোলতাবোল (২৪%) বানিয়েছিল আর Nova তা পড়ে শোনাচ্ছিল → এখন ৪০%-এর নিচে শুধু «বুঝতে পারিনি» (৩৭টা আসল কণ্ঠে সত্যিকারের কথা ≥ ৬২%)।
- ✓ **Smart Voice Response** (owner-এর নির্দেশ 2026-10-25)।
  - ছোট উত্তর পুরো; বড় বা তালিকা-ধরনের ফল (report, PC status, research, code) ১–২ বাক্যে মূল কথা + «বিস্তারিত Telegram-এ দিয়েছি»; Telegram-এর পুরো ফল অপরিবর্তিত।
  - «পুরোটা পড়ে শোনাও / বিস্তারিত বলো» → আগের উত্তর পুরো (নতুন task নয়); অনুরোধেই «বিস্তারিত» থাকলে প্রথমবারেই পুরো।
  - কাজ শেষ না হলে কখনো «হয়েছে» বলে না; password/key/card কখনো জোরে পড়ে না; code কখনো পড়ে না।
  - ধীর কাজে একবার ছোট progress (৮ সেকেন্ড পরে); অনুমোদন বা উত্তর লাগলে একবার «Telegram-এ দেখুন»।
  - নতুন কণ্ঠ-আদেশ এলে আগের উত্তরের কণ্ঠ বাদ; PC-তে shortcut চাপলেই Nova সাথে সাথে চুপ।
  - নিয়মভিত্তিক (লেখার গড়ন, দৈর্ঘ্য, owner কী চেয়েছেন) — কোনো AI call নয়।
  - আসল মাপ (edge কণ্ঠ, আসল task-ফল): PC status ৬৮.৩ → ৬.৪ সেকেন্ড, OBS-এর উত্তর ২৪.১ → ৮.১ সেকেন্ড, ছোট উত্তর অপরিবর্তিত।
  - পরে যোগ: «যে … দিলে, সেটা পড়ে শোনাও» — Whisper-এর ভাঙা বানানে কাজ করে (একই বিষয় + সাম্প্রতিক); owner-এর নিজের ১৪টা recording-এ: আগের উত্তর পড়া ৬/৬, নতুন অনুরোধ ৬/৬।
  - বাংলায় জিজ্ঞেস করলে বাংলায় বলা (শতাংশ, গিগাবাইট, সিপিইউ…); Gemini 429-এ Google-এর বলা সময় পর্যন্তই edge কণ্ঠ।
  - owner-এর নিজের শুনে পরীক্ষা বাকি (owner বলেছেন পরে করবেন — মনে করিয়ে দিতে হবে)।
- ✓ **কণ্ঠে প্রথম বাক্য আগে (streaming TTS) + স্থির বাক্যের cache**।
  - মাপ: Gemini প্রতি request-এ ~৭ সেকেন্ড (তিন শব্দেও ৬.৭ সেকেন্ড), edge ~১ সেকেন্ড।
  - প্রথম বাক্য আলাদা, বাকিটা একসাথে, একই সময়ে বানানো; প্রথমটা বাজতে বাজতে বাকিটা তৈরি (Gemini-তে একসাথে সর্বোচ্চ ২টা request)।
  - সংখ্যা ছাড়া ছোট স্থির বাক্য disk-এ cache (৩০০টা পর্যন্ত): দ্বিতীয়বার ০.০০৭ সেকেন্ড, Gemini-র সীমা খরচ হয় না।
  - আসল মাপ (edge): লম্বা পড়ায় প্রথম শব্দ ১.৪–১.৯ → ~০.৯ সেকেন্ড।
- ✓ **Dictation mode** — বলা কথা সামনের app-এ cursor-এর জায়গায় লেখা।
  - PC-তে আলাদা shortcut Ctrl+Alt+D (command-এর Ctrl+Alt+Space আলাদা); Telegram থেকেও «লিখে দাও: …» / «টাইপ করো: …» / "type: …"।
  - colon-এর পরের কথা হুবহু লেখা — «একটা কবিতা লিখে দাও» অনুরোধই থাকে; ভেতরের সময় বা app-এর নাম schedule/app-খোলা হয় না।
  - «দাঁড়ি / কমা / প্রশ্নবোধক চিহ্ন / নতুন লাইন» → চিহ্ন; Speech Dictionary আর নিশ্চয়তার নিয়ম আগের মতোই।
  - ui.type (BLUE); password-এর ঘর বা কথায় গোপন তথ্য → RED ui.type_secret; terminal/Run → RED; বোতাম/তালিকা ইত্যাদিতে লেখে না; লেখার পরে ঘর পড়ে যাচাই।
  - ফল কণ্ঠে বলে না (Telegram-এ «⌨️ PC-তে dictation»)।
  - আসল পরীক্ষা (Nova-র নিজের test window): cursor শুরুতে রেখে «নোভা » → «নোভা existing text» (বাংলা, cursor-এর জায়গায়); অন্য window হলে কিছুই লেখে না।
  - owner-এর নিজের app-এ আসল পরীক্ষা বাকি (owner করবেন)।
- ✓ **PowerShell / terminal runner শক্ত করা** (core/skills/proc.py)।
  - command আর command line-এ নয়: environment দিয়ে যায়, PowerShell চালুর সাথে সাথে মুছে ফেলে (process তালিকায় দেখা যায় না, ভেতরের কিছু পড়তে পারে না)।
  - সময় পেরোলে বা task বাতিল হলে পুরো tree বন্ধ — Windows Job Object, যেটা বাবা বেরিয়ে যাওয়া নাতি process-ও ধরে; স্বাভাবিকভাবে শেষ হলে চালু করা app চলতে থাকে।
  - input বন্ধ, output-এর সীমা (বিশাল output-এ memory ভরে না), গোপন-নামের env child-এ যায় না।
  - পরীক্ষায় ধরা পড়া ২টা পুরোনো ত্রুটিও ঠিক: child process pipe ধরে রাখলে runner সময় শেষ পর্যন্ত আটকে থাকত; task বাতিলে PowerShell চলতেই থাকত।
  - exit code-এর অর্থ আগের মতোই (মাপা: ব্যর্থ command=1, exit N=N, native ব্যর্থ=1, throw=1) — নইলে ব্যর্থ কাজ «সফল» দেখাত।
- ✓ **owner-এর ২৬ অক্টোবরের পরীক্ষার ব্যর্থতা সারানো** (নতুন কিছু নয়, যা আছে তা ঠিক করা)।
  - কারণ ১: এই PC-র AI (Ollama) একদিন ধরে বন্ধ ছিল, Nova চুপচাপ cloud-এ চলছিল — এখন বন্ধ হলে Telegram-এ একবার জানায়, ফিরলে জানায়।
  - কারণ ২: Gemini-তে টুকরো করে পড়ায় মাঝে ~৭ সেকেন্ড চুপ আর বিনামূল্যের সীমা শেষ — Gemini-তে আর ভাগ নয়।
  - কারণ ৩: «পড়ে শোনাও» শব্দ-নিয়মে ধরা যাচ্ছিল না — আগের উত্তরের দিকে ইঙ্গিত থাকলে এই PC-র AI ঠিক করে (উদাহরণসহ প্রশ্ন); «আবার বলো» যোগ।
  - কারণ ৪: dictation নিজেকে «শুধু-পড়ার» বলা Document ঘরে লিখত না — এখন লেখে।
  - owner-এর নিজের ২০টা recording (আসল Whisper + আসল AI): আগের উত্তর পড়া ১০/১০, নতুন অনুরোধ ৮/৮; AI-কে সব কথায় জিজ্ঞেস করলে নতুন অনুরোধ মাত্র ১/৮ ঠিক হতো — তাই শুধু ইঙ্গিত থাকলে।
- বাকি:
  - wake phrase + টানা কথোপকথন + মাঝপথে থামানো (ঐচ্ছিক)
  - ভাসমান orb
  - sidecar
