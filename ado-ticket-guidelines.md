# ADO Ticket Documentation Guidelines

## Data Modeling Team — Standard Operating Procedure

---

## Guiding Principle

Document to **inform and unblock**, not to cover yourself. Every comment should help the next person who reads this ticket understand what happened, what's needed, and what's in the way.

---

## 1. Log All Communications

Every meaningful exchange about a ticket gets logged as a comment, regardless of channel:

- **Email** — Paste or summarize the key points and tag relevant people. Include date and participants.
- **Teams/Slack** — Summarize the outcome of the conversation. Copy critical decisions verbatim if needed.
- **Meetings** — Add a comment after any meeting where the ticket was discussed. Include: what was decided, who owns the next step, and any deadlines agreed to.

> **Format:** `[Channel] [Date] — [Participants]: [Summary of outcome/decision]`
>
> Example: `[Teams Call] 2026-03-10 — Scott, Dana, Mike: Agreed to defer column mapping until source system cutover is confirmed. Target date moved to 3/20.`

**Why:** If it's not on the ticket, it didn't happen. This protects the team and keeps stakeholders informed without them having to chase you.

---

## 2. Log All Work

Every unit of work performed against a ticket gets a comment:

- What you did (briefly)
- What the result was (success, partial, failed, needs review)
- Any follow-up needed

Keep it concise. A few sentences is fine — this is a breadcrumb trail, not a novel.

> Example: `Completed initial column mapping for GL_ACCOUNT_DIM. 42 of 58 fields mapped. Remaining 16 require SME input from Finance — see comment thread below.`

---

## 3. Status Changes Always Get Context

When you change a ticket's status, **always** add a comment explaining why:

| Status Change | Required Comment |
|---|---|
| → **In Progress** | What you're starting and expected approach |
| → **Blocked** | What is blocking, who owns the blocker, and what's needed to unblock |
| → **In Review** | What's ready for review and where to find it (PR link, notebook, etc.) |
| → **Done** | What was delivered and where it lives |
| → **Closed (won't do)** | Why it's being closed and who approved the decision |

### Blocked Tickets — Extra Requirements

When setting a ticket to **Blocked**:

1. State the blocking reason clearly
2. Tag the person or team responsible for the blocker
3. Create a **child ticket** or **linked ticket** assigned to the blocking party (see Section 5)
4. Set a follow-up date and check back if no response

> Example: `BLOCKED — Waiting on Infinium team to provide updated extract schema for AP tables. Child ticket ADO-4521 created and assigned to @JohnD. Following up by 3/15 if no response.`

---

## 4. Stakeholder Requests Get Acknowledged

When a stakeholder submits a request or asks a question on a ticket:

- **Acknowledge within 1 business day** — even if the answer is "we're looking into it"
- Provide an estimated timeline when possible
- If the request changes scope, note it explicitly

> Example: `Acknowledged — Finance team requesting 3 additional calculated fields. Estimating 2 additional days. Will update scope on the ticket and adjust sprint accordingly.`

---

## 5. Cross-Team Dependencies Get Their Own Tickets

When work is required from another team to complete your ticket:

- Create a **child ticket** under your parent ticket
- Assign it to the appropriate person/team
- Link it back to your ticket with a **Blocked By / Blocks** relationship
- Add a comment on your ticket referencing the child ticket number

**Do not** rely on a Teams message or email alone to hand off work to another team. The ticket is the system of record.

> Example: `Created child ticket ADO-4521 (assigned to Platform team) for Databricks cluster config changes required before we can deploy the new streaming job. This ticket is blocked until ADO-4521 is resolved.`

---

## 6. Decision Log

When a meaningful decision is made on a ticket — scope change, approach change, deprioritization, stakeholder sign-off — log it with:

- **What** was decided
- **Who** decided it (or agreed to it)
- **Why** (one sentence is fine)

> Example: `DECISION: Dropping SCD Type 2 for VENDOR_DIM in favor of Type 1 per conversation with Dana (Finance). Historical tracking not required for this entity. Reduces delivery estimate by 3 days.`

---

## 7. Keep It Simple

- **Don't over-document** — a few clear sentences beats a wall of text nobody reads
- **Don't duplicate** — if it's in a PR comment or a linked doc, reference it with a link rather than copying it into the ticket
- **Do be specific** — names, dates, ticket numbers, links. Vague comments help nobody
- **Do write for future-you** — if you read this ticket in 6 months, would you understand what happened?

---

## Quick Reference Checklist

- [ ] All emails, Teams chats, and meeting outcomes logged on the ticket
- [ ] All work performed has a brief comment
- [ ] Every status change has a reason
- [ ] Blocked tickets identify the blocker, the owner, and have a child/linked ticket
- [ ] Stakeholder requests acknowledged within 1 business day
- [ ] Cross-team work has its own assigned ticket, not just a message
- [ ] Decisions logged with what/who/why
