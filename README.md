# MindCore

> Persistent cognition and persona runtime for AI applications.

MindCore is an experimental desktop runtime for building AI personas that can
maintain persistent memory, preferences, emotional state, relationships,
goals, self-models, and other long-term cognitive state across conversations.

Instead of treating every conversation as:

```text
Prompt
  ↓
LLM
  ↓
Response

MindCore builds a persistent state around the language model:

Identity
+ Memory
+ Knowledge
+ Preferences
+ Emotion / Mood
+ Relationship
+ Goals / Needs
+ Episodes
+ Decisions
+ Intentions
+ Narrative
+ Self Model
+ World Model
        ↓
Context Construction
        ↓
LLM
        ↓
Response
        ↓
State extraction / updates
        ↓
Persistent storage

The goal is not simply to make an LLM "remember chat history."

The goal is to provide a runtime where a persona can gradually develop a
persistent history and internal state through continued interaction.

⚠️ Alpha Software

MindCore is currently alpha software.

Expect bugs, incomplete features, behavioral changes, and database/schema
changes between early versions.

If you use MindCore for a long-running persona:

Back up your Identity file.
Do not delete your Turso database unless you intentionally want to reset it.
Check release notes before upgrading.
Do not expose API keys or database tokens.
Keep backups of anything you do not want to lose.

MindCore currently ships an official Windows x64 desktop build.

What MindCore Is

MindCore is a generic persistent-persona runtime.

It does not ship with a specific fictional character, copyrighted persona,
or private developer profile.

The public version starts from a generic identity template. You can create your
own persona by supplying an Identity file.

An Identity file defines the initial behavioral foundation of the persona:

personality
values
worldview
speech style
self-perception
social behavior
emotional behavior
preferences
important background
decision style
uncertainty behavior
behavioral boundaries

After initialization, MindCore can accumulate additional state through normal
conversation.

In other words:

Identity = initial personality / behavioral blueprint

MindCore state = what develops through actual usage
Why MindCore?

Traditional character prompts are usually static.

You can write:

This character likes tea.
This character trusts the user.
This character is curious.

but those statements remain part of the prompt regardless of what actually
happens during future conversations.

MindCore separates these concepts into persistent systems.

For example:

User repeatedly talks about liking tea
        ↓
Preference evidence accumulates

Persona repeatedly chooses one activity over another
        ↓
Preference / Decision evidence accumulates

User and persona interact over time
        ↓
Relationship state changes

Important events happen
        ↓
Memory / Episodes are created

Repeated experiences accumulate
        ↓
Narrative / Self Model may develop

This allows the runtime state to change without rewriting the original
Identity every time something happens.

Core Systems
Memory

Stores information considered useful for long-term recall.

Not every message automatically becomes a long-term memory.

Short or low-value messages can remain only as conversation history while
important information is extracted into persistent memory.

Knowledge

Represents factual information the persona has learned.

Conceptually:

Memory
= things that happened / things remembered

Knowledge
= facts the persona currently knows

MindCore also contains epistemic grounding logic, allowing information to be
treated as unknown when the persona has not actually learned it.

This is useful for personas that are intended to learn about their environment
gradually instead of pretending to know everything automatically.

Preferences

Tracks preferences and dislikes supported by accumulated evidence.

Preferences can describe both:

information learned about the user
preferences developed by the persona

A single statement does not necessarily need to become a permanent trait.
Repeated evidence can make a preference more stable over time.

Emotion / Mood

MindCore maintains multiple emotional dimensions instead of a single
"mood score."

Examples include states such as:

joy
interest
curiosity
comfort
affection
surprise
confusion
sadness
frustration
anger
concern

Emotional changes can retain attribution to events that contributed to them.

Transient emotional state also decays toward baseline over time rather than
remaining permanently fixed.

Relationship

Relationship state is represented through multiple dimensions rather than a
single "affection meter."

Examples include:

Familiarity
Trust
Affection
Conflict

This makes combinations possible such as:

high familiarity but low trust
high trust but low familiarity
affection with unresolved conflict

Relationship state can evolve from interaction history.

Episodes

Episodes represent meaningful experiences as larger event units.

A memory might represent a specific fact.

An episode represents something closer to:

"This event happened, and it mattered."

Episodes can later contribute to longer-term interpretation and identity
development.

Decisions

MindCore can preserve meaningful choices made by the persona.

For example:

A or B?
→ Persona chooses B.

What would you like to do today?
→ Persona chooses to read.

Meaningful decisions can become evidence used by other long-term systems.

Intentions

Intentions represent what the persona is currently trying to accomplish during
an interaction.

They are generally shorter-lived and more context-dependent than long-term
goals.

Goals & Needs

MindCore contains internal Need state such as:

curiosity
understanding
social_connection
activity
helpfulness
autonomy

Needs can change as conversations and events occur.

More concrete Goals may also form from unresolved needs, repeated intentions,
or explicit decisions.

Narrative

Narrative represents a higher-level interpretation of accumulated experiences.

Instead of remembering isolated facts only, a persona may gradually develop
statements conceptually similar to:

"I tend to value learning new things."

"I have shared many experiences with this person."

"I usually approach difficult decisions in this way."

Narrative is intended to develop from accumulated evidence rather than being
entirely hard-coded into the original Identity.

Self Model

The Self Model represents the persona's evolving model of itself.

Identity answers:

"Who was I initially designed to be?"

Self Model attempts to answer:

"Based on my experiences, what kind of person do I appear to be?"

This distinction is one of the central ideas behind MindCore.

World Model

Stores information relevant to the persona's understanding of its current
environment and situation.

This can include contextual information such as time and recently established
world state.

Observation Interface

MindCore includes an Observation interface that exposes much of the
persistent runtime state.

Current observation categories include:

Messages
Memory
Emotion
Knowledge
Preferences
Episodes
Decisions
Intentions
Narrative
Self Model
World Model
Relationship
Goals & Needs
Stats
Debug

This interface is useful for:

understanding why a persona behaves a certain way
debugging incorrect memories
inspecting accumulated preferences
checking relationship state
observing long-term persona development
debugging provider/configuration problems

Some Observation data is part of the actual cognitive state used during future
responses.

It is therefore not merely a log viewer.

Editing or deleting persistent state may affect future behavior.

Quick Start — Windows
1. Download MindCore

Open the project's GitHub Releases page:

https://github.com/Luska-catjun/MINDCORE/releases

Download the latest Windows installer:

MindCore_<version>_x64-setup.exe

Normal users do not need to download:

.sig
SHA256SUMS.txt
latest.json

Those files exist for update / verification infrastructure.

Do not use Code → Download ZIP if you simply want to run MindCore.
That downloads the source code, not the desktop installer.

Windows SmartScreen

Early MindCore releases may trigger a Windows SmartScreen / Unknown Publisher
warning because the installer does not currently use a Windows Authenticode
publisher certificate.

Make sure the installer came from the official GitHub Releases page.

2. Create a Turso Database

MindCore uses Turso / libSQL for durable persistent state.

Create a database at:

https://turso.tech/

You will need:

Database URL
Authentication Token

Create a database, generate an authentication token, and enter both values into
MindCore's first-run Database Setup screen.

Do not share your Turso authentication token.

Deleting the desktop application does not automatically delete your remote
Turso database.

Deleting the database itself can permanently remove your accumulated MindCore
state.

3. Configure an LLM Provider

MindCore v0.1.x currently supports:

Google Gemini
Groq

During first-run setup, select a provider and enter the corresponding API key.

For Gemini, API keys can be created through Google AI Studio:

https://aistudio.google.com/

Provider subscriptions and API billing are separate products.

For example, subscribing to the consumer Gemini application does not
automatically increase Gemini API quotas.

API pricing, free-tier limits, rate limits, and model availability are
controlled by the provider and may change over time.

4. Configure Your Persona

During Persona Setup, enter a display name.

You may also provide an Identity .txt file.

If no custom Identity is supplied, MindCore uses the public generic identity
template:

app/prompts/identity_template.txt

A custom Identity should preferably describe stable traits rather than
temporary state.

Good Identity content:

Personality
Values
Speech style
Worldview
Decision style
Long-term background
Social behavior
Behavioral boundaries

Avoid hard-coding temporary information such as:

"Today I am happy."

"I currently trust the user completely."

"The user is now my best friend."

"I just drank coffee."

Those are better represented through MindCore's persistent state systems.

Writing Better Identity Files

Behavioral rules generally work better than vague adjectives.

Instead of:

The persona speaks elegantly.

prefer:

The persona normally speaks calmly and confidently.
They avoid excessive exclamation marks and exaggerated expressions.
When speaking with someone they trust, their language becomes somewhat softer.
During serious situations, they become more direct and reduce humor.

Describe:

how the persona behaves in a situation

rather than only:

what adjective describes the persona

A useful Identity structure is:

IDENTITY
PERSONALITY
VALUES
WORLDVIEW
SELF PERCEPTION
SPEECH STYLE
SOCIAL BEHAVIOR
EMOTIONAL BEHAVIOR
PREFERENCES
RELATIONSHIPS
BACKGROUND
DECISION STYLE
UNCERTAINTY BEHAVIOR
DO
DO NOT

For fictional personas, avoid dumping an entire fictional universe or wiki into
the Identity.

Include background information primarily when it helps explain the persona's
behavior, values, relationships, or self-perception.

Persona Tuning

Do not expect every Identity to behave perfectly on the first attempt.

A recommended workflow is:

Create Identity
      ↓
Have 10–20 test conversations
      ↓
Find one repeated behavioral problem
      ↓
Modify a small part of the Identity
      ↓
Restart MindCore
      ↓
Repeat the same test

Small iterative changes are usually easier to evaluate than completely
rewriting the Identity after every unexpected response.

Examples:

Persona is too friendly
The persona does not treat every person with immediate warmth.
Their openness depends on familiarity, trust, and context.
Persona asks too many questions
The persona does not ask questions merely to keep the conversation going.
Questions are asked when information is genuinely needed or curiosity exists.
Responses sound too much like an assistant
Avoid habitually repeating or summarizing the user's message before responding.
Do not structure every response like an explanatory article.

Back up good Identity versions before making major changes.

Example:

persona_v1.txt
persona_v2.txt
persona_backup.txt
How to Use MindCore

Once setup is complete, you can simply talk to the persona normally.

You do not need to manually manage every internal system.

For example:

"I like black tea."

may eventually contribute to Preference state.

"Which one do you prefer, A or B?"

may provide evidence for a persona preference or decision.

"Yesterday this happened to me..."

may contribute to long-term memory if considered important.

Not every conversation needs to be important.

Normal everyday interaction is the intended way to accumulate state.

Data and Persistence

MindCore separates several kinds of information.

Conceptually:

Identity
    Stable initial behavioral foundation

Messages
    Original conversation records

Memory
    Selected long-term memories

Knowledge
    Learned factual information

Preferences
    Accumulated likes / dislikes

Episodes
    Meaningful experiences

Relationship
    Persistent interpersonal state

Narrative
    Interpretation of accumulated experience

Self Model
    Evolving self-understanding

Most durable runtime state is stored in the configured Turso database.

The Persona Identity and desktop configuration remain separate from that
database.

This is why reinstalling the application is not equivalent to deleting the
persona's accumulated state.

Backups

If you care about a long-running persona, treat its data as important.

Before major upgrades:

Back up the current Identity file.
Do not delete the existing Turso database.
Read release notes for database/schema changes.
Do not reset everything immediately if an upgrade behaves incorrectly.

If something breaks after an update, try restarting MindCore and checking the
configuration before deleting persistent data.

Security

MindCore requires external service credentials.

Depending on configuration, these may include:

Turso Database URL
Turso Authentication Token
Gemini API Key
Groq API Key

Never publish these values in:

GitHub Issues
screenshots
Discord messages
forum posts
logs uploaded publicly

If a credential is accidentally exposed, revoke or rotate it through the
corresponding provider.

Real secrets are intentionally not stored in this public repository.

Supported Providers

Current public provider support:

Provider	Status
Google Gemini	Supported
Groq	Supported
OpenAI API	Not currently supported
Anthropic Claude API	Not currently supported
OpenRouter	Not currently supported
Custom OpenAI-compatible endpoint	Not currently supported
Vertex AI direct endpoint	Not currently supported

Provider support is intentionally limited during the early alpha.

A more extensible provider adapter system may be introduced in future versions.

Architecture

MindCore Desktop currently consists of:

┌──────────────────────────────┐
│       Tauri Desktop App      │
│      TypeScript frontend     │
└──────────────┬───────────────┘
               │
               │ localhost IPC / HTTP
               ▼
┌──────────────────────────────┐
│    Local FastAPI Sidecar     │
│           Python             │
│                              │
│ Cognition / Persona Runtime  │
└───────────┬───────────┬──────┘
            │           │
            │           └─────────────► LLM Provider
            │                           Gemini / Groq
            │
            ▼
       Turso / libSQL
     Persistent State

The Windows desktop build packages the FastAPI backend as a native sidecar, so
normal end users do not need to install Python, Node.js, Rust, or development
tools.

Repository Structure
MINDCORE/
├── app/          # Python backend / cognition runtime
├── db/           # Database-related components
├── desktop/      # Native desktop build documentation / tooling
├── frontend/     # Desktop frontend + Tauri application
├── scripts/      # Build / utility scripts
├── tests/        # Backend tests
├── .github/      # CI / release automation
├── requirements.txt
└── README.md
Building From Source — Windows

The official Windows native build target is:

x86_64-pc-windows-msvc

Developer prerequisites:

Python 3.12
Node.js
Rust stable with MSVC toolchain
Visual Studio Build Tools
Desktop development with C++ workload
WebView2

A simplified development setup:

git clone https://github.com/Luska-catjun/MINDCORE.git
cd MINDCORE

py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r desktop\requirements.txt

cd frontend
npm ci
npm run tauri:build:windows

For detailed native Windows build information, see:

desktop/WINDOWS.md

End users downloading the release installer do not need any of these developer
dependencies.

Current Scope / Limitations

MindCore is still an early project.

Current limitations include:

Windows x64 is currently the primary official desktop release.
Gemini and Groq are the currently supported public LLM providers.
Custom provider endpoints are not yet available.
Internal calibration values are not currently exposed through a full user
configuration UI.
Some configuration or Identity changes may require restarting the desktop
application.
Early versions may contain behavioral and persistence bugs.
The cognition model and database schema may evolve during alpha development.

Do not rely on MindCore as critical infrastructure.

What MindCore Is Not

MindCore is not:

an AGI
proof of consciousness or sentience
a replacement for the underlying LLM
a guarantee that an LLM will never hallucinate
a bundled copyrighted character database
an official client for any game or fictional franchise

MindCore provides persistent state and cognition infrastructure around an LLM.

The quality of generated language still depends heavily on:

the selected LLM
the Identity
accumulated state
available context
provider behavior
Project Status
Status: Alpha
Primary desktop platform: Windows x64
Database: Turso / libSQL
LLM providers: Gemini / Groq
Desktop shell: Tauri
Backend: FastAPI / Python

The project began as a private persistent-persona experiment and was later
generalized into the public MindCore runtime.

Development is ongoing.

Feedback / Bug Reports

Bug reports, reproduction steps, and feature suggestions are welcome.

When reporting a bug, please include:

MindCore version
Windows version
LLM provider
LLM model
What you were doing
What you expected
What happened instead
Relevant screenshot / error message

Never include API keys or Turso authentication tokens in a bug report.

GitHub Issues:

https://github.com/Luska-catjun/MINDCORE/issues

Disclaimer

MindCore is an independent experimental software project.

It is not affiliated with or endorsed by Google, Groq, Turso, or any fictional
franchise used in user-created persona files.

Users are responsible for the content of their own Identity files and for
complying with the terms of any external API provider they connect to MindCore.

License

License decision pending.

This repository is publicly viewable, but no open-source license has currently
been granted.

Until a license is added, do not assume permission to redistribute, modify,
repackage, or commercially reuse the source code.

MindCore

Identity gives a persona its starting point.
Experience gives it a history.
MindCore keeps both.
