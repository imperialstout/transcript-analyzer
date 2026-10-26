July 8, 2026
Workspace: SI RCA
Meeting type: Daily Slack Summary


This canvas was generated using AI, which can produce inaccurate or harmful responses. Review for accuracy and safety before using.
Channels Scanned: #siemens-rca-pda-internal · #siemens-si-sherpax-rca-arm-one-delivery-team · #siemens-si-sherpax-rca-program-alignment · #@WPFCESVSP,U02P99W4V6Z,U02SWMWKWAY,U03A4LZ0UFK · DM: Gunnar Ulle · DM: Colin O'Neill · DM: Evangelos Skiadopoulos · DM: Jitu Rathod · #@U03A4LZ0UFK,U077WGPKQKS · #temp-siemens-uat-for-262-sb-testing · #siemens-arm-agent-moonshot · #siemens-si-project-escalation-fy27 · #siemens-rca-arm-program-leads · #siemens-siesales-org · #@WS1HCD75Z,U03A4LZ0UFK · #@WMG1FDL77,WPFCESVSP,U087E2MGTQU · #siemens-amer-pro-serv-leadership · #@WPFCESVSP,U03A4LZ0UFK
Channels with no activity on July 8: siemens-rca-pda-internal, siemens-si-sherpax-rca-program-alignment, group DMs (Hemanta/Gunnar/Imad/Brad/Wojciech, Hinduja/Hemanta/Brad, Brad/Eskiadopoulos/Baruah, Gulle/Brad/Challmann/Jimenez Davila), siemens-amer-pro-serv-leadership, Brad/Gunnar/Hemanta group DM, C0B2540759S, C0B2SCPRNAU, siemens-si-project-escalation-fy27, C047YLHTDG8

Pod: Implementation / Sprint Planning (PDA)
Reporters: @Hinduja Goli (Hinduja Goli), @Nadeen Khalid (Nadeen Khalid)
Major Accomplishments
Hinduja prepared an out-of-band developer story point allocation table for Sprint 3 (Column A: developers, B: SPs allocated, C: capacity, D: remaining capacity) after discovering she lacked edit access to the Impl. Team Sprint Planning tracker.
Nadeen confirmed she moved the sprint planning sheet to Excel and will re-share updated link with the IMPL team channel.
Javiera Jimenez Davila clarified the user story assignment process: stories assigned to implementation leads (not developers) are ready for IMP; handshake has not yet occurred; proposed leveraging the existing Ravi 1:1 to perform the DocGen handshake if needed.
Blockers / Concerns
Sprint planning tracker edit access issue caused Hinduja to work outside the tracker — risk of version fragmentation.
Too many fragmented communications around story assignment are creating confusion among implementation leads.
49 open Sprint 2 user stories flagged by Ignacio at Steerco — required closure or shift by EOD July 8.
Items Worth a Follow-Up
to share updated Excel sprint planning link with the IMPL team channel so Hinduja can add capacity data.
DocGen story handshake: Javiera/Ravi 1:1 proposed as the venue — confirm or schedule separately.
Confirm Sprint 2 closure status — 49 open stories needed to be closed or shifted by EOD July 8.

Pod: Solution Systems / Product & Pricing (RC CH / SolSys)
Reporters: (Colin O'Neill), (Jitu Rathod)
Major Accomplishments
Jitu coordinated a leads call and reached alignment on several near-term actions: effort/aligned view for Use Case 1 feasibility by EOD July 8; workshop plan by Friday for use-case-centric progress; Colin and Imad to demo Product & Pricing to Sven for HLD sign-off; data load template to be published; release management considerations being mapped.
Colin is actively pushing Siemens to define requirements at a granular level and questioning the purpose of requested fields before building — good scope discipline.
RCA POC SWE opportunity (Siemens DISW - RCA POC SWE) confirmed as approved/closed.
Colin and team targeting closure of open pricing design questions by end of next week.
Blockers / Concerns
HLD work for Product & Pricing is not coming to the Design Board for review — Jitu raising this to the team.
Sven acknowledged knowing only ~5% of what SolSys actually needs — significant design knowledge gap on the Siemens side.
CHF-only discount requirements for material and labor seen as tech debt / northstar-conflicting by Colin.
Backlog management overhead (BACKLOGMASTER spreadsheet, Jitu's story point requests) pulling team away from design work; stories described as too waterfall-oriented.
Org confusion: Colin unsure which org people are currently working from.
Items Worth a Follow-Up
Colin/Imad to demo Product & Pricing to Sven — confirm scheduling and prep.
Jitu to escalate HLD Product & Pricing review gap to the team this evening (July 8).
Clarify active development org — was asked directly by Colin.
RCA POC SWE opp closed — staffing alignment still pending; Brad's guidance: continue SI RCA role until fully aligned.
Third Technician Use Case: not ready for Design Board this week; track for next available slot.

Pod: Program Leadership / EAB
Reporters: (Brad Gross), (Gunnar Ulle)
Major Accomplishments
EAB meeting cancelled July 8; Brad posted update in program leads channel.
Brad shared Steerco meeting summary notes with Colin and Jitu to align on program status.
Gunnar shared the Diego Cattaneo Foundational Sales Workshop summary (July 7) — key strategic update on DI Software/Automation, StarQuote vs. Salesforce RCA positioning, and long-term convergence expectations.
Brad shared notes from his last call with Ines Costa (June 30 Strategic Alignment summary).
Blockers / Concerns
Demo of Siemens Quote Agent was a significant failure — sandbox issues prevented half the demo from working. Agentforce demo reliability is a recurring and now systemic concern.
Steerco described as "UGLY": Anna Walter challenged readiness for May 2027 while simultaneously acknowledging she "doesn't know all they need for May." This contradiction creates planning paralysis for the team.
Meeting overhead flagged: Evangelos and Lisa generating too many meeting requests; Brad and Jitu imploring them to slow down.
Items Worth a Follow-Up
Accelerate solutioning even under uncertainty — make and document assumptions rather than waiting for full requirements.
Follow up on Diego workshop: Salesforce/Gunnar team to receive DI process + capability map by end of August, then assess CPQ/RCA coverage gaps.
Agentforce demo reliability: consider whether sandbox strategy needs to change before next stakeholder demo.

Cross-Pod / Program Items
Steerco – July 8 (Status: 🟡 Amber)
Full meeting notes: 07-08 Steerco SI RCA – STEP Team Integration, Risk Management, and Release Planning
Key decisions and actions from the Steerco:
STEP Team Integration: Diego to ping Christoph Hallman today; Subham to cover during Christoph's PTO; Friday handover call to be organized; "red telephone" regular connection to be established to avoid ticket delays.
Cati (Change Management) Extension: No decision yet; must be decided before August or capability is lost. Pending Parag's return from PTO.
Monthly Release Cycle: Services switching to monthly releases; risk of premature feature exposure. Need deployment controls and process to hide not-yet-released functionality. Adrian Rohrbach to document tooling needs.
Encoway Integration: Decisions were made without sufficient Salesforce involvement, causing downstream conflicts on price handling and rule conflicts with configurator/RCA. Earlier sync points needed with STEP MDM.
Sprint 2 Closure: 49 open user stories flagged; must close or shift by EOD July 8.
Scope Visibility (1.1 vs 2.0): Anna requested a work package / scope / gaps mapping by EOD Friday July 10 — Evangelos committed to deliver.
Capacity Prioritization: No overarching prioritization framework across Service, BX, and SolSys — leadership guidance requested. Current approach: 2 FTEs impl + 1 FTE design for service; ~70% of recent sprints on service topics.
July 26 Release: Bug fixing ongoing for UAT/QA; scope finalization underway.
Training: French SIB training ongoing; how-to video on quote configuration in development with GBS.
Diego Cattaneo – Foundational Sales Workshop (July 7 Update)
Summary canvas: Summary from Diego Cattaneo Update Foundational Sales Workshop July 7th
Salesforce accepted as long-term CPQ platform — not in question.
Urgent need to scale DI software business (perpetual, hybrid, SaaS) via DI automation; mixed offerings need to be quotable together.
StarQuote used as quoting tool; RCA/billing/entitlements/rev rec are missing.
Revenue recognition in SAP S/4HANA adds complexity (Switzerland go-live May 2027 roadmap misalignment is a political/governance issue).
Zuora effectively off the table; Salesforce RCA being evaluated as the alternative over ~3 years.
Diego skeptical of "Salesforce can't do pricing" claim from StarQuote team — wants to understand the real reasoning.
Next Steps: Diego/business to conduct DI software+automation process review this month; capability map by end of August; Gunnar's team to then assess Salesforce CPQ/RCA coverage vs. gaps.

Overall Daily Summary
July 8 was a heavy and turbulent day for the program, dominated by a difficult Steerco and continued friction around design knowledge gaps, capacity constraints, and execution pace. The most impactful work was the leads alignment call that Jitu coordinated, which produced near-term actions on Use Case 1 feasibility, the Product & Pricing HLD demo to Sven, and a workshop plan targeting the end of the week. Sprint 3 planning also advanced, though tracker access issues required Hinduja to work outside the primary tool, and the sprint planning sheet was moved to Excel mid-day.
The biggest active risks center on the Siemens knowledge gap (Sven's "5% of SolSys needs" statement) and Anna's demand for May 2027 readiness while simultaneously not being able to define what that requires. This creates a planning paradox where the team cannot commit to a realistic roadmap without requirements that Siemens itself cannot yet supply. On top of this, the Agentforce demo was a sandbox-driven car crash, which adds reputational pressure at a moment when stakeholder confidence is already fragile.
At the program governance level, three time-sensitive decisions are pending: (1) Sprint 2 story closure/shift by EOD July 8, (2) the 1.1 vs. 2.0 scope mapping that Evangelos committed to deliver by Friday July 10, and (3) the Cati change management extension decision needed before August. The Encoway integration conflict and the undefined cross-topic capacity prioritization framework are structural risks that continue without an owner or resolution timeline.
The Diego Cattaneo workshop summary provides a welcome strategic anchor — Salesforce is confirmed as the long-term CPQ platform for DI, and a clear process/capability mapping exercise is now owned by the business side (due end of August), giving the Salesforce team a defined trigger for their own gap assessment.
Key Risks to Watch:
:red_circle: Siemens design knowledge gap — Sven knows ~5% of SolSys requirements; without upstream design clarity, implementation velocity will remain blocked.
:red_circle: Steerco credibility & pace — Anna's contradiction (unprepared for May, but can't define May requirements) risks program moat; scope mapping due Friday July 10 is critical.
:large_orange_circle: Agentforce demo reliability — sandbox failures in front of Siemens stakeholders are systemic and damaging; needs a mitigation strategy before next demo.
:large_orange_circle: Sprint 2 story closure — 49 open stories as of this morning; unclear if EOD target was met; follow-up required.
:large_orange_circle: HLD Product & Pricing not going to Design Board — Jitu escalating; if unresolved, downstream implementation risks increase.
:large_orange_circle: Cati extension decision — capability loss by August if not decided; decision holder (Parag) is on PTO; needs a fallback owner.
:yellow_circle: Capacity prioritization framework undefined — no agreed model across Service, BX, SolSys; risk of misaligned sprint loading.
:yellow_circle: Encoway/Salesforce/RCA design conflicts — pricing and rule conflicts need a joint design review with STEP MDM; no forum exists yet.
:yellow_circle: Backlog management overhead — multiple trackers (BACKLOGMASTER, Jira stories, sprint planning Excel) creating noise and pulling architects away from design.
