# Graph Report - agent-command-center/backend/apps  (2026-05-29)

## Corpus Check
- Corpus is ~8,095 words - fits in a single context window. You may not need a graph.

## Summary
- 422 nodes · 519 edges · 74 communities detected
- Extraction: 66% EXTRACTED · 34% INFERRED · 0% AMBIGUOUS · INFERRED: 178 edges (avg confidence: 0.5)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Thread & Project Model Tests|Thread & Project Model Tests]]
- [[_COMMUNITY_Account Domain|Account Domain]]
- [[_COMMUNITY_Message & Policy Permission|Message & Policy Permission]]
- [[_COMMUNITY_Approvals Domain|Approvals Domain]]
- [[_COMMUNITY_Django App Configs|Django App Configs]]
- [[_COMMUNITY_Admin Registrations|Admin Registrations]]
- [[_COMMUNITY_Tier 2 Adapter Framework|Tier 2 Adapter Framework]]
- [[_COMMUNITY_Audit Events|Audit Events]]
- [[_COMMUNITY_Consumer Tests & Ollama Mock|Consumer Tests & Ollama Mock]]
- [[_COMMUNITY_Skills Domain|Skills Domain]]
- [[_COMMUNITY_Slash Parser Tests|Slash Parser Tests]]
- [[_COMMUNITY_Host & Project API|Host & Project API]]
- [[_COMMUNITY_Initial Migrations|Initial Migrations]]
- [[_COMMUNITY_WebSocket Consumer|WebSocket Consumer]]
- [[_COMMUNITY_Thread API Tests|Thread API Tests]]
- [[_COMMUNITY_Account API Tests|Account API Tests]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 52|Community 52]]
- [[_COMMUNITY_Community 53|Community 53]]
- [[_COMMUNITY_Community 54|Community 54]]
- [[_COMMUNITY_Community 55|Community 55]]
- [[_COMMUNITY_Community 56|Community 56]]
- [[_COMMUNITY_Community 57|Community 57]]
- [[_COMMUNITY_Community 58|Community 58]]
- [[_COMMUNITY_Community 59|Community 59]]
- [[_COMMUNITY_Community 60|Community 60]]
- [[_COMMUNITY_Community 61|Community 61]]
- [[_COMMUNITY_Community 62|Community 62]]
- [[_COMMUNITY_Community 63|Community 63]]
- [[_COMMUNITY_Community 64|Community 64]]
- [[_COMMUNITY_Community 65|Community 65]]
- [[_COMMUNITY_Community 66|Community 66]]
- [[_COMMUNITY_Community 67|Community 67]]
- [[_COMMUNITY_Community 68|Community 68]]
- [[_COMMUNITY_Community 69|Community 69]]
- [[_COMMUNITY_Community 70|Community 70]]
- [[_COMMUNITY_Community 71|Community 71]]
- [[_COMMUNITY_Community 72|Community 72]]
- [[_COMMUNITY_Community 73|Community 73]]

## God Nodes (most connected - your core abstractions)
1. `Account` - 35 edges
2. `Thread` - 34 edges
3. `Message` - 22 edges
4. `Host` - 19 edges
5. `PolicyProfile` - 19 edges
6. `Meta` - 17 edges
7. `Project` - 14 edges
8. `ApprovalRequest` - 12 edges
9. `AuditEvent` - 12 edges
10. `TestThreadModel` - 12 edges

## Surprising Connections (you probably didn't know these)
- `AuditEventAdmin` --uses--> `AuditEvent`  [INFERRED]
  apps\audit\admin.py → apps\audit\models.py
- `AccountAdmin` --uses--> `Account`  [INFERRED]
  apps\accounts\admin.py → apps\accounts\models.py
- `TestAccountAPI` --uses--> `Account`  [INFERRED]
  apps\accounts\tests\test_api.py → apps\accounts\models.py
- `TestApprovalRequestAPI` --uses--> `Account`  [INFERRED]
  apps\approvals\tests\test_api.py → apps\accounts\models.py
- `TestApprovalRequestModel` --uses--> `Account`  [INFERRED]
  apps\approvals\tests\test_models.py → apps\accounts\models.py

## Communities

### Community 0 - "Thread & Project Model Tests"
Cohesion: 0.07
Nodes (18): PolicyProfileAdmin, ProjectAdmin, PolicyProfile, Project, SensitivityChoices, PolicyProfileSerializer, TestProjectAPI, GIVEN the SDK runtime mode WHEN a Thread is created THEN it persists. (+10 more)

### Community 1 - "Account Domain"
Cohesion: 0.08
Nodes (17): AccountAdmin, HostAdmin, Account, Host, OsChoices, StatusChoices, AccountSerializer, HostSerializer (+9 more)

### Community 2 - "Message & Policy Permission"
Cohesion: 0.1
Nodes (14): MessageAdmin, MessageInline, ThreadAdmin, Message, RoleChoices, RuntimeModeChoices, Thread, PolicyPermission (+6 more)

### Community 3 - "Approvals Domain"
Cohesion: 0.08
Nodes (11): ApprovalRequestAdmin, ApprovalRequest, RequestTypeChoices, RiskChoices, ApprovalRequestSerializer, TestApprovalRequestAPI, GIVEN a thread WHEN an approval is requested THEN it is pending., GIVEN an approval WHEN it expires THEN status is expired. (+3 more)

### Community 4 - "Django App Configs"
Cohesion: 0.08
Nodes (12): AppConfig, AccountsConfig, AdaptersConfig, ApprovalsConfig, AuditConfig, HostsConfig, PoliciesConfig, ProjectsConfig (+4 more)

### Community 5 - "Admin Registrations"
Cohesion: 0.1
Nodes (13): AdapterCapabilityAdmin, AuditEventAdmin, SlashCommandAdmin, Tier2ProviderAdmin, AdapterCapability, EventTypeChoices, Meta, SlashCommand (+5 more)

### Community 6 - "Tier 2 Adapter Framework"
Cohesion: 0.1
Nodes (15): get_adapter(), NormalizedEvent, Raised when no Tier 2 adapter is registered for a provider., A provider-agnostic streaming event from a Tier 2 adapter., Yield NormalizedEvents for the given thread and message history.          Implem, Class decorator registering an adapter under its `provider` string., Resolve a provider string to an adapter instance.      Lazily imports `apps.tier, register_adapter() (+7 more)

### Community 7 - "Audit Events"
Cohesion: 0.09
Nodes (7): AuditEvent, TestAuditEventAPI, GIVEN an action WHEN audited THEN redacted payload is stored., GIVEN a system event WHEN no thread is involved THEN thread is null., TestAuditEventModel, TestAuditSignals, TestCleanupOldAuditEvents

### Community 8 - "Consumer Tests & Ollama Mock"
Cohesion: 0.12
Nodes (3): _FakeOllamaClient, _FakeOllamaResponse, TestThreadConsumer

### Community 9 - "Skills Domain"
Cohesion: 0.14
Nodes (5): SkillAdmin, Skill, SkillSerializer, TestSkillAPI, SkillViewSet

### Community 10 - "Slash Parser Tests"
Cohesion: 0.12
Nodes (0): 

### Community 11 - "Host & Project API"
Cohesion: 0.11
Nodes (5): AuditEventSerializer, ProjectSerializer, TestHostAPI, AuditEventViewSet, ProjectViewSet

### Community 12 - "Initial Migrations"
Cohesion: 0.17
Nodes (1): Migration

### Community 13 - "WebSocket Consumer"
Cohesion: 0.24
Nodes (5): AsyncJsonWebsocketConsumer, _build_history(), _get_thread(), _persist_message(), ThreadConsumer

### Community 14 - "Thread API Tests"
Cohesion: 0.22
Nodes (1): TestThreadAPI

### Community 15 - "Account API Tests"
Cohesion: 0.25
Nodes (1): TestAccountAPI

### Community 16 - "Community 16"
Cohesion: 0.25
Nodes (0): 

### Community 17 - "Community 17"
Cohesion: 0.29
Nodes (1): TestPolicyProfileAPI

### Community 18 - "Community 18"
Cohesion: 0.67
Nodes (0): 

### Community 19 - "Community 19"
Cohesion: 1.0
Nodes (0): 

### Community 20 - "Community 20"
Cohesion: 1.0
Nodes (0): 

### Community 21 - "Community 21"
Cohesion: 1.0
Nodes (0): 

### Community 22 - "Community 22"
Cohesion: 1.0
Nodes (0): 

### Community 23 - "Community 23"
Cohesion: 1.0
Nodes (0): 

### Community 24 - "Community 24"
Cohesion: 1.0
Nodes (0): 

### Community 25 - "Community 25"
Cohesion: 1.0
Nodes (0): 

### Community 26 - "Community 26"
Cohesion: 1.0
Nodes (0): 

### Community 27 - "Community 27"
Cohesion: 1.0
Nodes (0): 

### Community 28 - "Community 28"
Cohesion: 1.0
Nodes (1): Migration

### Community 29 - "Community 29"
Cohesion: 1.0
Nodes (0): 

### Community 30 - "Community 30"
Cohesion: 1.0
Nodes (0): 

### Community 31 - "Community 31"
Cohesion: 1.0
Nodes (0): 

### Community 32 - "Community 32"
Cohesion: 1.0
Nodes (0): 

### Community 33 - "Community 33"
Cohesion: 1.0
Nodes (0): 

### Community 34 - "Community 34"
Cohesion: 1.0
Nodes (0): 

### Community 35 - "Community 35"
Cohesion: 1.0
Nodes (0): 

### Community 36 - "Community 36"
Cohesion: 1.0
Nodes (0): 

### Community 37 - "Community 37"
Cohesion: 1.0
Nodes (0): 

### Community 38 - "Community 38"
Cohesion: 1.0
Nodes (0): 

### Community 39 - "Community 39"
Cohesion: 1.0
Nodes (0): 

### Community 40 - "Community 40"
Cohesion: 1.0
Nodes (0): 

### Community 41 - "Community 41"
Cohesion: 1.0
Nodes (0): 

### Community 42 - "Community 42"
Cohesion: 1.0
Nodes (0): 

### Community 43 - "Community 43"
Cohesion: 1.0
Nodes (0): 

### Community 44 - "Community 44"
Cohesion: 1.0
Nodes (0): 

### Community 45 - "Community 45"
Cohesion: 1.0
Nodes (0): 

### Community 46 - "Community 46"
Cohesion: 1.0
Nodes (0): 

### Community 47 - "Community 47"
Cohesion: 1.0
Nodes (0): 

### Community 48 - "Community 48"
Cohesion: 1.0
Nodes (0): 

### Community 49 - "Community 49"
Cohesion: 1.0
Nodes (0): 

### Community 50 - "Community 50"
Cohesion: 1.0
Nodes (0): 

### Community 51 - "Community 51"
Cohesion: 1.0
Nodes (0): 

### Community 52 - "Community 52"
Cohesion: 1.0
Nodes (0): 

### Community 53 - "Community 53"
Cohesion: 1.0
Nodes (0): 

### Community 54 - "Community 54"
Cohesion: 1.0
Nodes (0): 

### Community 55 - "Community 55"
Cohesion: 1.0
Nodes (0): 

### Community 56 - "Community 56"
Cohesion: 1.0
Nodes (0): 

### Community 57 - "Community 57"
Cohesion: 1.0
Nodes (0): 

### Community 58 - "Community 58"
Cohesion: 1.0
Nodes (0): 

### Community 59 - "Community 59"
Cohesion: 1.0
Nodes (0): 

### Community 60 - "Community 60"
Cohesion: 1.0
Nodes (0): 

### Community 61 - "Community 61"
Cohesion: 1.0
Nodes (0): 

### Community 62 - "Community 62"
Cohesion: 1.0
Nodes (0): 

### Community 63 - "Community 63"
Cohesion: 1.0
Nodes (0): 

### Community 64 - "Community 64"
Cohesion: 1.0
Nodes (0): 

### Community 65 - "Community 65"
Cohesion: 1.0
Nodes (0): 

### Community 66 - "Community 66"
Cohesion: 1.0
Nodes (0): 

### Community 67 - "Community 67"
Cohesion: 1.0
Nodes (0): 

### Community 68 - "Community 68"
Cohesion: 1.0
Nodes (0): 

### Community 69 - "Community 69"
Cohesion: 1.0
Nodes (0): 

### Community 70 - "Community 70"
Cohesion: 1.0
Nodes (0): 

### Community 71 - "Community 71"
Cohesion: 1.0
Nodes (0): 

### Community 72 - "Community 72"
Cohesion: 1.0
Nodes (0): 

### Community 73 - "Community 73"
Cohesion: 1.0
Nodes (0): 

## Knowledge Gaps
- **12 isolated node(s):** `RequestTypeChoices`, `RiskChoices`, `EventTypeChoices`, `OsChoices`, `RuntimeModeChoices` (+7 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 19`** (2 nodes): `tasks.py`, `expire_old_approval_requests()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 20`** (2 nodes): `tasks.py`, `cleanup_old_audit_events()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 21`** (2 nodes): `tasks.py`, `check_host_heartbeats()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 22`** (2 nodes): `parser.py`, `parse()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 23`** (2 nodes): `handle()`, `account.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 24`** (2 nodes): `model.py`, `handle()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 25`** (2 nodes): `stop.py`, `handle()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 26`** (2 nodes): `__init__.py`, `get_handler()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 27`** (2 nodes): `signals.py`, `thread_post_save_broadcast()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 28`** (2 nodes): `Migration`, `0002_rename_content_message_redacted_content_and_more.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 29`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 30`** (1 nodes): `urls.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 31`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 32`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 33`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 34`** (1 nodes): `urls.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 35`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 36`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 37`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 38`** (1 nodes): `urls.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 39`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 40`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 41`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 42`** (1 nodes): `urls.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 43`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 44`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 45`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 46`** (1 nodes): `urls.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 47`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 48`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 49`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 50`** (1 nodes): `urls.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 51`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 52`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 53`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 54`** (1 nodes): `urls.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 55`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 56`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 57`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 58`** (1 nodes): `urls.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 59`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 60`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 61`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 62`** (1 nodes): `urls.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 63`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 64`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 65`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 66`** (1 nodes): `urls.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 67`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 68`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 69`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 70`** (1 nodes): `urls.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 71`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 72`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 73`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Thread` connect `Message & Policy Permission` to `Thread & Project Model Tests`, `Account Domain`, `Approvals Domain`, `Audit Events`, `Consumer Tests & Ollama Mock`, `WebSocket Consumer`, `Thread API Tests`?**
  _High betweenness centrality (0.147) - this node is a cross-community bridge._
- **Why does `Account` connect `Account Domain` to `Thread & Project Model Tests`, `Message & Policy Permission`, `Approvals Domain`, `Audit Events`, `Consumer Tests & Ollama Mock`, `Thread API Tests`, `Account API Tests`?**
  _High betweenness centrality (0.125) - this node is a cross-community bridge._
- **Why does `ThreadConsumer` connect `WebSocket Consumer` to `Message & Policy Permission`, `Tier 2 Adapter Framework`?**
  _High betweenness centrality (0.105) - this node is a cross-community bridge._
- **Are the 33 inferred relationships involving `Account` (e.g. with `AccountAdmin` and `AccountSerializer`) actually correct?**
  _`Account` has 33 INFERRED edges - model-reasoned connections that need verification._
- **Are the 32 inferred relationships involving `Thread` (e.g. with `TestApprovalRequestAPI` and `TestApprovalRequestModel`) actually correct?**
  _`Thread` has 32 INFERRED edges - model-reasoned connections that need verification._
- **Are the 20 inferred relationships involving `Message` (e.g. with `TestAuditSignals` and `MessageInline`) actually correct?**
  _`Message` has 20 INFERRED edges - model-reasoned connections that need verification._
- **Are the 17 inferred relationships involving `Host` (e.g. with `TestAccountModel` and `GIVEN valid account data WHEN created THEN it persists with UUID pk.`) actually correct?**
  _`Host` has 17 INFERRED edges - model-reasoned connections that need verification._