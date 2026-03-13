# ACME Software — Product Guide

## Introduction

ACME Software helps engineering and product teams plan, track, and ship
work faster. This guide covers core workflows and best practices.

---

## Core Concepts

### Projects
A **project** is a collection of tasks organised around a goal or deliverable.
Every project has:
- A name and description
- An owner (project manager)
- A start date and deadline
- A status: Active, On Hold, Completed, or Archived

### Tasks
A **task** is the smallest unit of work. Tasks live inside projects and include:
- Title and description
- Assignee (one or more team members)
- Priority: Low, Medium, High, or Critical
- Due date
- Labels / tags
- Attachments
- Sub-tasks

### Milestones
A **milestone** marks a key checkpoint in your project.
Milestones are shown on the Gantt chart and Timeline views.

---

## Views

| View       | Best for                             |
|------------|--------------------------------------|
| List       | Detailed task management             |
| Board      | Kanban-style workflow (Scrum/Kanban) |
| Gantt      | Timeline and dependency planning     |
| Calendar   | Deadline overview                    |
| Dashboard  | High-level progress and KPIs         |

---

## Permissions & Roles

| Role          | Permissions                                  |
|---------------|----------------------------------------------|
| Owner         | Full control including billing               |
| Admin         | Manage members, projects, and settings       |
| Member        | Create and edit tasks; view all projects     |
| Viewer        | Read-only access                             |

---

## Keyboard Shortcuts

| Action            | Shortcut         |
|-------------------|------------------|
| New task          | N                |
| Search            | /                |
| Quick assign      | A                |
| Set due date      | D                |
| Change priority   | P                |
| Open command bar  | Cmd/Ctrl + K     |

---

## Notifications

Notifications are sent for:
- Task assignments
- Due date reminders (24 hours and 1 hour before)
- Comments and @mentions
- Status changes on watched tasks

You can configure notification preferences under
**Profile → Notifications**.

---

## API & Webhooks

ACME provides a REST API for custom integrations.

**Base URL:** `https://api.acme-software.example.com/v1`

**Authentication:** Bearer token (generate in Settings → API → New Token)

Example — list all projects:
```
GET /v1/projects
Authorization: Bearer YOUR_TOKEN
```

Webhooks can be configured in Settings → Webhooks to receive real-time
events when tasks are created, updated, or completed.

---

## Limits by Plan

| Feature              | Free  | Pro      | Enterprise |
|----------------------|-------|----------|------------|
| Projects             | 3     | ∞        | ∞          |
| Members              | 5     | ∞        | ∞          |
| Storage              | 1 GB  | 50 GB    | Custom     |
| API requests/month   | 1,000 | 100,000  | Custom     |
| Automations          | 5     | 250      | Custom     |
| File attachment size | 5 MB  | 100 MB   | 500 MB     |

---

## Best Practices

1. **Use labels consistently** — agree on a label taxonomy before inviting
   your team.
2. **Break large tasks into sub-tasks** — keeps tasks under 1–2 days of work.
3. **Set milestone due dates** — gives the team visible targets.
4. **Review the dashboard weekly** — catch blockers early.
5. **Archive completed projects** — keeps your workspace tidy.

---

## Support

For help, visit [support.acme-software.example.com](https://support.acme-software.example.com)
or email **support@acme-software.example.com**.
