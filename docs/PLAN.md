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

