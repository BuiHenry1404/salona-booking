# Task: Design LLM Orchestrator and Multi-Agent Architecture for Salona Booking

## 1. Role and Objective

Act as a Senior AI Agent Architect and Backend Engineer with expertise in Python, FastAPI, LangChain, LangGraph, MongoDB, Azure OpenAI, multi-agent orchestration, and production-grade agentic systems.

Your task is to inspect the existing Salona Booking repository and produce:

1. An architecture specification.
2. An implementation plan broken into small, reviewable tasks.
3. A test matrix covering normal behavior, edge cases, failure recovery, and safety.
4. A list of architecture decisions, risks, assumptions, and genuinely blocking questions.

**This is a design-only task. Do not implement application code.**

Inspect the actual codebase before designing the solution. Do not assume the current implementation exactly matches the description below.

---

## 2. Product Context

Salona Booking is a Vietnamese conversational assistant for a small nail and hair salon.

Typical customers may:
- Send short messages with typos or colloquial Vietnamese.
- Use relative dates and times such as "mai", "chiều mai", or "3 giờ".
- Change their minds or provide incomplete information.
- Reply ambiguously to a previous question.
- Postpone a decision, for example: "Để chú hỏi con rồi nhắn lại."
- Ask about appointment availability, shop opening hours, or shop status.
- Make small talk, greet the assistant, or express thanks.
- Ask about services that the chatbot cannot explain from its available information.

### Product constraints

- The chatbot does not have a service catalog for consultation.
- The chatbot does not provide price viewing.
- When customers ask about specific services or information that the system cannot support, it should politely guide them to contact the shop owner.
- Booking, cancellation, and rescheduling must follow a controlled confirmation workflow.
- The system must not claim that an operation succeeded without verified evidence.
- When uncertain about a business-critical decision, ask for clarification rather than guessing.

### Existing technology stack

Keep the existing stack:
- Python
- FastAPI
- LangChain
- LangGraph
- MongoDB
- Azure OpenAI

Do not propose replacing these technologies.

Inspect the repository to determine its actual graph structure, state models, checkpointing, persistence, tools, middleware, APIs, tests, and project conventions.

---

## 3. Target Architecture: Exactly Four Agents

The target architecture contains **exactly four agents in total: one LLM Orchestrator and three specialist agents**.

Do not add a fifth agent. Infrastructure components, services, tools, middleware, and code-based routers do not count as agents.

### Agent 1: Orchestrator Agent

This is an LLM-based planning and orchestration component.

Its responsibilities are limited to:

1. Understanding the customer's goal and relevant context.
2. Identifying required tasks.
3. Producing a structured execution plan.
4. Selecting the appropriate specialist agents and allowed actions.
5. Defining task dependencies and identifying independent tasks.
6. Evaluating specialist results against the user's goal.
7. Deciding whether to replan, request clarification, or finish.
8. Replanning when the current plan cannot satisfy the request.

The Orchestrator must NOT:
- Handle greetings and ordinary social conversation as its primary response responsibility.
- Directly generate ordinary small-talk replies for the customer.
- Directly execute arbitrary tools or code.
- Write to the database.
- Commit bookings, cancellations, or rescheduling operations.
- Decide that customer consent is valid without code-controlled validation.
- Override authoritative application state.
- Invent task results or claim unverified success.
- Plan indefinitely or repeatedly execute equivalent tasks without progress.

The Orchestrator is a planner and coordinator, not the business-operation executor.

### Agent 2: Appointment Agent

Responsibilities:
- Handle appointment-related tasks.
- Interpret booking, cancellation, and rescheduling requests.
- Identify missing appointment information.
- Find available slots.
- Retrieve the customer's appointments.
- Propose suitable alternatives when a preferred time is unavailable.
- Return structured results that the Orchestrator can evaluate.

Inspect the current appointment tools and services before deciding how responsibilities should be distributed.

Safety constraints:
- Do not bypass the proposal and confirmation workflow.
- Do not treat an LLM interpretation as authorization to commit a booking.
- Do not directly write to the database outside the existing or redesigned code-controlled business-operation path.
- Revalidate availability and business constraints before committing a booking.
- Never invent available slots.

### Agent 3: Shop Information Agent

Responsibilities:
- Handle questions about shop opening hours.
- Retrieve shop operating status.
- Answer questions about available shop information stored in trusted data sources.
- Return structured results for the Orchestrator to combine with other task results.

Do not invent shop information.

Evaluate whether this agent needs its own LLM agent loop or whether some operations should be exposed as deterministic tools. The target architecture still contains the four agents above, but individual turns do not need to invoke every agent.

### Agent 4: Customer Conversation Agent

Responsibilities:
- Handle greetings, thanks, small talk, and ordinary social conversation.
- Produce natural, polite Vietnamese replies appropriate for the salon's customers.
- Handle conversational acknowledgements that are not part of a pending business-critical workflow.
- Politely guide customers to contact the shop owner when they ask about unsupported service details, prices, or other unavailable information.

This agent must NOT:
- Make up service descriptions, prices, or shop policies.
- Create or modify bookings.
- Decide that a pending booking proposal has been approved.
- Ignore pending questions, proposals, or other authoritative conversation state.
- Handle a message as ordinary small talk when it is actually a response to a pending appointment workflow.

### Agent boundary principle

Use the four agents for distinct reasoning and communication responsibilities.

Do not create additional agents for:
- Conversation memory.
- State resolution.
- Confirmation.
- Booking commits.
- Guardrails.
- Rate limiting.
- Generic database access.

These responsibilities belong to code, services, middleware, or existing LangGraph state management.

---

## 4. Routing and State Resolution

Design a code-based State Resolver or equivalent mechanism that runs before deciding whether the Orchestrator should be invoked.

It is infrastructure, not an additional agent.

Its purpose is to resolve trusted conversation and business state, including:
- Whether a booking proposal is pending.
- Whether the customer is answering a previous question.
- Whether a confirmation button or other structured event was received.
- Whether a task or plan is already executing.
- Whether a conversation is waiting for missing information.
- Whether a customer message can safely be treated as ordinary conversation.

Examples:

| Customer message | Expected behavior |
|---|---|
| "Chào cháu" | Route to Customer Conversation Agent if no business workflow requires attention |
| "Cảm ơn nhé" | Route to Customer Conversation Agent if no pending business action takes priority |
| "Mai chú muốn đặt lịch" | Invoke Orchestrator to plan appointment tasks |
| "Tiệm mấy giờ đóng cửa?" | Invoke Orchestrator to plan the shop-information task |
| "Đặt lịch lúc 3 giờ, tiệm đóng cửa lúc mấy giờ?" | Plan the relevant appointment and shop-information tasks |
| "16 giờ đi cháu" while answering a pending time question | Resolve against trusted conversation state before routing |
| "Để chú hỏi con rồi nhắn lại" while a proposal is pending | Preserve the proposal state; do not treat the message as approval |
| "Được rồi" while a specific proposal is awaiting approval | Validate the message against the correct pending proposal and confirmation policy |

Do not route based only on the surface wording of the latest message when trusted state indicates a pending workflow.

Define how explicit structured events, buttons, and known deterministic actions bypass unnecessary LLM planning without bypassing business validation.

---

## 5. LLM Orchestration and ExecutionPlan

### 5.1 Structured plan schema

Design a validated execution-plan schema using the project's conventions, preferably Pydantic models.

Include the necessary fields for:
- Plan ID and version.
- User goal or normalized intent.
- Task IDs.
- Target agent.
- Allowlisted action.
- Typed task arguments.
- Dependencies.
- Task status.
- Retry eligibility and limits.
- Structured task outputs.
- Completion criteria.
- Clarification requirements.
- Replanning reason.
- Stopping conditions.

Distinguish clearly between:
- An LLM-proposed plan.
- A code-validated plan.
- A running plan.
- A completed plan.

An LLM-generated plan must not be executable until validated by code.

Do not allow arbitrary tool names, arbitrary code, unrestricted database queries, or unvalidated arguments.

### 5.2 Planning and execution lifecycle

Specify this lifecycle:

1. Receive a customer turn.
2. Resolve trusted conversation and business state.
3. Determine whether to route directly to Customer Conversation Agent or invoke Orchestrator.
4. Orchestrator generates a structured plan.
5. Code validates the plan and authorizes its tasks.
6. Execute eligible tasks through specialist agents.
7. Collect structured task results.
8. Evaluate whether the user's goal has been satisfied.
9. Replan, clarify, or finish.
10. Generate a final customer-facing response.
11. Persist the required execution and conversation state.

Define graph states and transitions explicitly.

### 5.3 Replanning

Replanning must be based on verified task results and authoritative state, not solely on the Orchestrator's previous natural-language reasoning.

Define:
- What counts as an incomplete task.
- What counts as a failed task.
- What counts as new information.
- What counts as genuine progress.
- When a plan should be revised.
- When clarification is required.
- When execution must stop.
- How repeated or equivalent plans are detected.

The Orchestrator must not repeatedly call the same specialist with effectively identical inputs when no relevant state has changed.

---

## 6. Conversation Progress Limits and Rate Limiting

The system must avoid prolonged conversations or internal agent loops that consume resources without making progress toward the customer's goal.

Design two separate controls.

### 6.1 Technical rate limit

Protect the system from excessive requests.

Evaluate existing rate-limiting mechanisms and specify appropriate limits by user, conversation, or endpoint.

Do not confuse technical rate limiting with business-workflow completion.

### 6.2 Execution and conversation progress limits

Track the progress of the active goal across graph execution and relevant conversation turns.

Potential state fields include:
- `planning_iterations`
- `replanning_count`
- `consecutive_no_progress`
- `completed_task_ids`
- `unresolved_requirements`
- `last_progress_marker`

These are examples, not mandatory field names. Reuse existing state structures where appropriate.

Define progress based on observable changes, such as:
- A required field being resolved.
- A task being completed.
- A verified result becoming available.
- A dependency being satisfied.
- A proposal being created.
- A previously blocked task becoming executable.

Do not count every customer message as progress.

Do not count ordinary greetings or thanks as failed booking attempts.

Do not penalize customers for deliberately postponing or abandoning a booking.

Distinguish at least:
1. Internal planning-loop limits.
2. Specialist execution and retry limits.
3. Consecutive no-progress detection.
4. Cross-turn goal-progress limits.
5. Technical request rate limits.

When a limit is reached:
- Stop repeated planning or execution.
- Preserve authoritative state.
- Do not create or commit a booking without satisfying the normal safety rules.
- Return a clear, polite response.
- Offer a concrete next step, such as providing missing information, selecting another time, retrying later, or contacting the shop owner.
- Avoid falsely claiming that the system has permanently blocked the customer.

Use configurable limits and explain the rationale for initial defaults. Do not invent supposedly optimal thresholds without evidence from the codebase or tests.

---

## 7. Specialist Communication Contracts

Define typed input and output contracts for all three specialist agents.

Specify:
- Required context.
- Task arguments.
- Allowed tools and actions.
- Structured result schema.
- Success and failure representations.
- Missing-information behavior.
- Retryability.
- Completion criteria.

Specialists must return structured results that the orchestration layer can validate and persist.

Avoid hidden dependencies on another specialist's conversation history.

Do not use unstructured natural-language messages as the authoritative representation of task arguments, dependencies, task status, booking identifiers, or results.

The Orchestrator may interpret structured results and decide what to do next, but code must validate the resulting plan before execution.

Do not require a separate agent-to-agent conversation when a structured task result or direct code-level dependency is sufficient.

---

## 8. State and Persistence

Inspect existing LangGraph state, MongoDB checkpointing, conversation history, and business persistence before proposing changes.

Keep the following concepts separate:
- Conversation history.
- Graph checkpoint state.
- Execution plan.
- Task execution ledger.
- Authoritative appointment state.
- Proposal and confirmation state.
- Relevant historical-event context.

Define which state is authoritative and where it lives.

Requirements:
- Recover safely after a graph or process restart.
- Avoid repeating completed side effects.
- Avoid using LLM-generated summaries as authoritative business state.
- Revalidate stale availability results.
- Handle expired and superseded proposals.
- Prevent stale buttons from confirming a different proposal.
- Handle duplicate and concurrent user requests.
- Preserve pending business workflows even when the customer sends a greeting or small-talk message.

Do not create a Conversation Context Agent.

Use code-based context retrieval, structured state, existing persistence, or bounded summarization when appropriate.

---

## 9. Booking Safety and Human-in-the-Loop

Preserve this invariant:

**The LLM plans and interprets; code validates permissions, consent, business rules, and writes.**

For booking, cancellation, and rescheduling:
- Keep the existing proposal/confirmation mechanism where possible.
- Validate proposal ownership and status.
- Validate expiry and relevant state versions.
- Bind approval to the correct proposal.
- Revalidate slot availability at commit time.
- Ensure commits are safe under retries and concurrency.
- Make business operations idempotent where appropriate.
- Handle process failure after a successful database write.
- Require clarification when consent or the intended operation is ambiguous.

Inspect the actual implementation and document any missing safeguards.

Do not assume that LangGraph `interrupt()` alone provides transactional safety.

Do not let the Orchestrator or specialist agents bypass the code-controlled booking write path.

---

## 10. Guardrails and Security

Review and specify guardrails at these boundaries:

1. Input validation.
2. State resolution.
3. Plan validation.
4. Authorization and policy enforcement.
5. Specialist tool execution.
6. Task-result validation.
7. Booking and proposal operations.
8. Final response validation.

Treat customer messages, retrieved context, and model outputs as untrusted inputs where appropriate.

Prevent prompt injection from:
- Expanding tool permissions.
- Bypassing confirmation.
- Altering trusted business state.
- Executing unauthorized tasks.
- Fabricating task results.
- Circumventing progress limits.

Use deterministic code for authorization, state transitions, and business-critical validation. Do not rely on regex alone for complex intent or consent interpretation.

Inspect existing middleware and reuse working protections where possible.

---

## 11. Failure Handling and Recovery

Specify expected behavior for:
- Invalid or malformed plans.
- Unauthorized actions.
- Invalid task arguments.
- Missing required context.
- Specialist failures.
- Tool timeouts.
- Invalid structured outputs.
- Failed task dependencies.
- Partial plan completion.
- Replanning loops.
- No-progress limits being reached.
- Process restarts.
- Duplicate turns.
- Concurrent requests.
- Stale availability.
- Expired proposals.
- Ambiguous consent.
- Failure after a business operation succeeds but before graph state is persisted.

Distinguish retryable from non-retryable failures.

Retries must not duplicate business operations.

---

## 12. Observability

Define structured logs and trace fields for:
- Conversation ID and turn ID.
- Plan ID and version.
- Task ID.
- Agent and action.
- Task dependencies.
- Task status.
- Retry count.
- Replanning count.
- No-progress count.
- Failure classification.
- Proposal ID where applicable.
- Final outcome.

Avoid unnecessarily logging sensitive customer data or full conversation messages.

Ensure that it is possible to explain why the Orchestrator chose an agent, why a plan was revised, why execution stopped, and why a request was classified as making no progress.

---

## 13. Architecture Decisions to Evaluate

Evaluate the following decisions against the actual repository:

1. How much of the current Analyzer/Router should remain after introducing the LLM Orchestrator?
2. Which customer messages can bypass the Orchestrator safely?
3. Should Customer Conversation Agent be invoked directly by State Resolver for simple social messages?
4. Should Shop Information Agent use a full agent loop or a narrower set of tools?
5. How should independent tasks and dependent tasks be executed?
6. Should planning and result evaluation use one Orchestrator node or separate graph nodes?
7. How should plans and task results be persisted for recovery?
8. How should no-progress detection work across multiple turns?
9. How should execution limits differ from technical rate limits?
10. Which existing middleware and services can be reused without duplicating responsibilities?

For each decision, document:
- Options.
- Trade-offs.
- Recommended choice.
- Rationale.
- Conditions that would justify revisiting the decision.

Do not assume that more agents or more LLM calls automatically improve reasoning quality.

---

## 14. Required Deliverables

Inspect existing documentation conventions before choosing file paths.

### A. Architecture Specification

Create or update a specification document covering:
- Verified current architecture.
- Current problems and architectural gaps.
- Goals and non-goals.
- Exactly four agent roles and boundaries.
- Proposed architecture and Mermaid diagrams.
- State Resolver and routing rules.
- ExecutionPlan schema.
- Specialist input/output contracts.
- Graph lifecycle and state transitions.
- Replanning and completion criteria.
- Progress limits and rate limiting.
- Persistence and recovery.
- Booking confirmation and write safety.
- Guardrails and security.
- Observability.
- Testing strategy.
- Architecture decisions and trade-offs.
- Risks and unresolved questions.

### B. Implementation Plan

Create an implementation plan detailed enough for a coding agent to execute without inventing major architectural decisions.

Break work into small, sequential, independently reviewable tasks.

For each task include:
- Objective.
- Expected files/modules to change, based on repository inspection.
- Dependencies.
- Concrete steps.
- Acceptance criteria.
- Tests to add or modify.
- Verification commands based on actual project tooling.
- Risks and rollback considerations.

Consider phases such as:
1. Baseline behavior and tests.
2. Typed execution-plan and task contracts.
3. State resolution and routing.
4. Plan validation and policy engine.
5. Orchestrator and bounded replanning.
6. Specialist contract alignment.
7. Progress tracking and rate limiting.
8. Persistence and recovery.
9. Booking-safety hardening.
10. Observability and end-to-end testing.
11. Documentation and cleanup.

Adjust the phases based on the repository. Do not create unnecessary tasks for capabilities that already exist.

### C. Test Matrix

Map each key requirement and failure scenario to specific test cases.

Include:
- Social messages routed to Customer Conversation Agent.
- Pending appointment state taking priority over surface-level small talk.
- Compound requests involving appointment and shop information.
- Plan validation and unauthorized actions.
- Task dependencies and parallel independent tasks.
- Replanning after a preferred slot is unavailable.
- Ambiguous dates, times, and consent.
- Replanning-loop limits.
- Consecutive no-progress detection.
- Technical rate limiting.
- Social messages not incrementing booking no-progress counters.
- Customer postponement not being treated as a failed booking attempt.
- Process recovery and duplicate requests.
- Concurrent attempts to book the same slot.
- Expired proposals and stale buttons.
- Prompt injection.
- Idempotent booking writes.

### D. Open Questions and Final Report

Resolve questions that can be answered by repository inspection.

Separate blocking questions from non-blocking assumptions.

At completion, report:
1. Files created or updated.
2. Recommended architecture.
3. Key decisions and trade-offs.
4. Existing risks found in the codebase.
5. Blocking questions requiring my decision.
6. Confirmation that no application code was implemented.

---

## 15. Workflow

If Superpowers is installed:
1. Read the actual brainstorming and writing-plans skill instructions.
2. Follow the brainstorming/specification workflow.
3. Inspect the codebase before proposing architecture.
4. Present the design for approval if the workflow requires a checkpoint.
5. After the design is approved, create the implementation plan.
6. Review the spec and plan together for contradictions, omissions, and implementation ambiguity.

Do not skip repository inspection.

Do not start implementing application code during this task.

---

## 16. Strict Constraints

- Exactly four agents in the target architecture, including the Orchestrator.
- Do not add a Service Consultation Agent.
- Do not add a Conversation Context Agent.
- The product has no service catalog or price-viewing functionality.
- Unsupported service questions should guide customers to contact the shop owner.
- The Orchestrator plans and coordinates; it does not directly execute business writes.
- Customer Conversation Agent handles ordinary social conversation but must respect pending business state.
- Progress limits and technical rate limits are separate mechanisms.
- No-progress detection must not punish ordinary social conversation or deliberate postponement.
- Preserve existing safeguards unless a documented decision explicitly changes them.
- Do not replace the existing technology stack.
- Do not invent file paths, APIs, schemas, test commands, or existing behavior.
- Do not introduce infrastructure or dependencies without a documented need.
- Do not modify application source code or implement the architecture.
- Do not run destructive commands.
- Prefer typed contracts and deterministic validation for business-critical behavior.
- Keep the design appropriate to the repository's actual scale.
- Do not optimize for latency as a primary goal.

## 17. Definition of Done

The task is complete only when:
- The current implementation has been verified against the repository.
- The architecture specifies exactly four agents with non-overlapping responsibilities.
- Routing and pending-state behavior are explicit.
- ExecutionPlan, task contracts, and replanning behavior are defined.
- No-progress detection and technical rate limiting are clearly separated.
- Booking safety, confirmation, recovery, and concurrency are addressed.
- The implementation plan includes concrete files, steps, acceptance criteria, and tests.
- The specification and plan are consistent.
- No application code has been implemented.