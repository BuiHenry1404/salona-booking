# Booking Nail-Toc UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the Nail & Hair Salon booking prototypes (`ui-mockup.html` and `ui-mockup-streaming.html`) with the Soft Warm Rose & Sage design system and make them fully interactive.

**Architecture:** 
- Add a CSS design system based on warm-rose, sage, and ivory colors, tailored for high-contrast and readability.
- Implement a tabs-based interface in `ui-mockup.html` containing a "Showcase Grid" (5 phone mockups) and a "Live Sandbox" (2 synced interactive phone shells simulating Client-Admin interactions via local JS state).
- Enhance the simulated chat flow in `ui-mockup-streaming.html` with custom user inputs, connection state toggle (network drop simulation), and real-time Socket.IO logs.

**Tech Stack:** HTML5, Vanilla CSS, Vanilla JavaScript.

## Global Constraints
- Minimal font size: 18px / 19px for readability.
- Touch target minimum: 56px height for buttons.
- Contrast ratio: minimum 4.5:1 (actually targeting 5.2:1+).
- Do not use Git commands (as requested by user).

---

### Task 1: Update Styling & Core Tab Layout in `ui-mockup.html`

**Files:**
- Modify: `/home/henryb1/Desktop/HenryB1/projects/agentbox-eco-system/personal-project/fastapi-agent-template/docs/superpowers/specs/2026-08-06-booking-nail-toc/ui-mockup.html`

- [ ] **Step 1: Replace CSS variables and styles**
  Update the style block in `ui-mockup.html` to define the Soft Warm Rose & Sage colors, responsive container, tab switcher, and CSS phone wrappers.
  
- [ ] **Step 2: Add HTML structure for Tabs**
  Add a Tab navigation header at the top of the body, wrap the current 5 screens in a `#showcase-tab` block, and create an empty container `#sandbox-tab` for the interactive sandbox.

- [ ] **Step 3: Add basic tab switching JavaScript**
  Implement logic to toggle display between Showcase Grid and Live Sandbox.

- [ ] **Step 4: Verify visually**
  Open the file in a browser or review the markup. Make sure layout switches properly between Tab 1 and Tab 2.

---

### Task 2: Implement "Live Sandbox" HTML & Client Interactions in `ui-mockup.html`

**Files:**
- Modify: `/home/henryb1/Desktop/HenryB1/projects/agentbox-eco-system/personal-project/fastapi-agent-template/docs/superpowers/specs/2026-08-06-booking-nail-toc/ui-mockup.html`

- [ ] **Step 1: Write Sandbox HTML Structure**
  Create `#sandbox-tab` content showing two phone mockups side-by-side:
  - Phone Left: Client App (starts at Login screen, has Chat screen, has My Bookings screen)
  - Phone Right: Admin App (Dashboard and Clients list)

- [ ] **Step 2: Implement Client Navigation & Actions**
  - Add JS handlers for Login: Clicking "Đăng nhập" transitions the screen to Client Chat.
  - Add JS handlers for Chat inputs: Typing a message or hitting the mic icon adds user message to chat, then triggers simulated typing for AI response with spinner -> checkmark -> message text.
  - Implement bottom bar navigation on Client phone to switch between "Đặt lịch" (Chat) and "Lịch của tôi" (Bookings).
  - Implement inline booking cancellation (clicking "Hủy lịch này" shows inline panel, clicking "Có, hủy" cancels booking and updates list).

- [ ] **Step 3: Verify Client-side interactions**
  Verify that logging in, chat inputs, screen switching, and inline cancellation function correctly.

---

### Task 3: Implement Admin Dashboard & Synced Real-Time Simulation in `ui-mockup.html`

**Files:**
- Modify: `/home/henryb1/Desktop/HenryB1/projects/agentbox-eco-system/personal-project/fastapi-agent-template/docs/superpowers/specs/2026-08-06-booking-nail-toc/ui-mockup.html`

- [ ] **Step 1: Implement Admin State Transitions**
  - Add JS handlers for toggling Busy state: Clicking "Tôi đang bận" displays quick duration selections (15m, 30m, 1h, 2h).
  - Clicking a duration starts a real-time interval countdown.
  - Clicking "Tôi rảnh rồi" stops the countdown and resets status.

- [ ] **Step 2: Sync Client & Admin State**
  - When Admin busy status changes, automatically update the status card on the Client chat screen.
  - When Client completes a booking (e.g. clicking "Đúng rồi" in confirmation), add a card to the Admin dashboard with a pulsing "MỚI" badge and yellow background.
  - When Client cancels a booking, remove it from the Admin dashboard list.

- [ ] **Step 3: Verify synchronization**
  Test end-to-end flow: Set Admin to Busy -> check Client Chat status bar -> Book a slot -> check Admin Dashboard -> Cancel slot -> check Admin Dashboard.

---

### Task 4: Redesign and Upgrade `ui-mockup-streaming.html`

**Files:**
- Modify: `/home/henryb1/Desktop/HenryB1/projects/agentbox-eco-system/personal-project/fastapi-agent-template/docs/superpowers/specs/2026-08-06-booking-nail-toc/ui-mockup-streaming.html`

- [ ] **Step 1: Update design variables & style block**
  Apply the Soft Warm Rose & Sage design system to `ui-mockup-streaming.html`.

- [ ] **Step 2: Enhance Simulator panel on the right**
  - Implement a text input to let the user type custom messages.
  - Add a "Mất mạng" checkbox toggle.
  - Implement a logs panel to display JSON event payloads as events (like `turn_started`, `tool_started`, `token`) occur.

- [ ] **Step 3: Update Chat script**
  Rewrite script in `ui-mockup-streaming.html` to execute simulation dynamically:
  - If "Mất mạng" is active during tool execution or token stream, show red text: "Mất mạng, đang thử lại..." and stop typing.
  - Type custom user messages and trigger tool responses (`get_shop_status`, `find_free_slots`, etc.) matching their descriptions.

- [ ] **Step 4: Verify streaming simulation**
  Run simulation, toggle network drop, type custom text, check Socket.IO logs.
