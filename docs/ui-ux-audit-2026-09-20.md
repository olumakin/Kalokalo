# UI/UX audit — seamless user journeys

Date: 2026-09-20. Status: all findings open; documentation only.

## Scope and evidence

Source-based review of Matchday, Model Diagnostics, Backtest, and Ledger: onboarding, navigation, controls, result interpretation, loading/failure states, filtering, and result entry. No running app was available during the review, and Streamlit was not installed locally. Rendered mobile layouts, keyboard behavior, screen-reader announcements, contrast, and actual interaction timings were not verified. Accessibility concerns below are validation targets, not confirmed conformance failures.

This records all 12 findings from the conversational UI/UX audit, plus its accessibility review targets, proposed journey, and acceptance checks. It complements the [20 engine/data/architecture findings](audit-2026-09-20.md) and [frontend/backend migration proposal](frontend-backend-migration.md). No implementation is changed or approved by adding this document.

The central usability problem is that users must understand and configure the engine before seeing the product's value. Prioritize trustworthy state, simple task completion, and consistent behavior before visual polish. P1 means fix first for trust/task completion; P2 means next for consistency/comprehension. The engine audit's P0 release blockers still take precedence over publishing recommendations.

## P1 — Trust and task completion

### UX01 — Setup overwhelms first-time users

**Evidence:** `app.py:199–294`. The sidebar exposes historical sources, random seeds, blending, model targets, API keys, and ledger recording before the primary action, “Run pipeline.”

**Impact:** Users must make technical decisions before seeing a forecast; the first-run empty state sends them back into this configuration.

**Recommendation:** Make the main journey competition/date selection followed by available forecasts. Move ingestion, credentials, model controls, and storage configuration into an operator-only Settings area. Use “View forecasts” for viewing existing results and “Update analysis” for a new calculation; do not imply these are the same operation. Keep synthetic data outside the production journey.

**Acceptance:** A first-time user can locate a match and understand availability without changing a model parameter, source strategy, or API key. Missing real data has an actionable unavailable state.

### UX02 — Changed controls can appear beside old results

**Evidence:** `app.py:386–411,455–460`; `pages/2_Backtest.py:21–67`. Outputs persist in session state independently of subsequent input edits. There is no prominent result-to-input snapshot or changed-settings indicator.

**Impact:** Users can believe that existing results reflect newly selected history, thresholds, or model settings.

**Recommendation:** Bind every result to an immutable run and show “Results from…” with timestamp and an input summary. Distinguish display-only filters from analysis settings. After analysis settings change, show “Settings changed — update analysis” and label the prior results explicitly. Preserve old results as history, not as apparently current output.

**Acceptance:** Changing any analysis input never silently relabels old output. A failed update leaves the prior run clearly identified; viewing another page preserves the selected run's identity.

### UX03 — Status wording overstates readiness

**Evidence:** `app.py:276–289,393–409`. “Free feed active” is stated before a successful fetch; an existing API key produces configured status without verifying retrieval; “Not yet run” local storage status receives success styling.

**Impact:** Green or active-looking status can mean “not checked” rather than successful service operation. Users cannot readily distinguish configuration from availability.

**Recommendation:** Use Not checked, Checking, Available, and Unavailable states with actual last-success timestamps and coverage. Distinguish “Credentials configured” from “Data retrieved.” Surface run-specific save status near the result; keep cumulative technical error counts in operator diagnostics.

**Acceptance:** No success state is displayed before the corresponding check succeeds. A partial or failed fetch states which competitions are unavailable. Save failures cannot appear as a successfully recorded run.

### UX04 — Forecast confidence and recommendation wording conflict

**Evidence:** `app.py:48,530–582`. Cards carry “FORECAST ONLY” yet reveal a bankroll recommendation in an expander. Every draw probability below 27.5% is called “MODERATE TIE CHANCE,” regardless of how low it is.

**Impact:** Confidence and intended use are ambiguous. Research-only presentation can be interpreted as actionable advice.

**Recommendation:** Show numeric probabilities, data origin, and eligibility status. Remove qualitative confidence labels unless supported by validated thresholds. Keep research output free of production recommendation language. Any future eligible recommendation needs the backend evidence gate described in audit A06; changing a badge cannot establish eligibility.

**Acceptance:** Very low draw probabilities do not receive a moderate-confidence label. Demo, stale, failed-fit, and unapproved research results never display production recommendation language.

### UX05 — Long-running work lacks useful progress and recovery

**Evidence:** `app.py:298–357`; `pages/2_Backtest.py:51–65`. Spinners name broad stages, but no durable progress, cancellation, or stage-specific recovery control is supplied.

**Impact:** Users cannot distinguish slow work from a stuck run or understand what will happen after navigation or failure.

**Recommendation:** Show completed/current stages, elapsed time, and an explicit terminal state. Provide Retry for the failed operation while preserving inputs. Add cancellation where the worker can safely support it; do not advertise a cancel action that does not stop work. Use progress counts when available and avoid invented completion percentages or time estimates. Recovery must not substitute sample data or another model.

**Acceptance:** Each run reaches a visible completed, failed, or cancelled state. Retry cannot duplicate a saved logical run. Navigation and refresh can recover durable job status once the backend migration is implemented.

### UX06 — Result recording invites accidental or ambiguous submissions

**Evidence:** `pages/3_Ledger.py:64–106`. The selector uses internal match IDs, goals default to 1–1, optional closing odds use zero as a sentinel, and saving immediately reruns the screen. No visible correction workflow is provided.

**Impact:** A user can save a draw without deliberately entering a result, select the wrong fixture, or be unsure what was saved. The success message is not retained explicitly across reruns.

**Recommendation:** Identify matches by full team names, competition, and date. Require deliberate score entry, explain optional closing odds in plain language, and preview the selected match/result before submission. Retain a persistent confirmation and offer an audited correction action. Apply audit A11's prediction-versus-position distinction to all financial fields.

**Acceptance:** An untouched form cannot save 1–1. Confirmation names the match and final score after refresh. Correction preserves the prior event and records the new one; a repeated save does not duplicate settlement.

## P2 — Consistency and comprehension

### UX07 — Filters and summary totals behave inconsistently

**Evidence:** `app.py:455–465`; `pages/3_Ledger.py:29–47`. Clearing Matchday competitions shows no matches; clearing Ledger leagues shows all records. Ledger summary totals use the full dataset rather than filtered rows.

**Impact:** Identical gestures produce opposite outcomes; visible totals seem unrelated to the table.

**Recommendation:** Adopt explicit All competitions and Clear selection actions; an empty selection shows no rows everywhere. Make page summary totals follow active filters. If global totals are useful, label them “All records” separately. Export exactly the filtered dataset and show the exported record count.

**Acceptance:** Clearing, selecting, resetting, and exporting work consistently on all screens; summary counts reconcile to their labelled dataset.

### UX08 — Match browsing lacks basic discovery controls

**Evidence:** `app.py:500–535`. Cards provide sorting and a display-count slider capped at ten. There is no team search, date filter, or card pagination. Dates omit kickoff time and timezone.

**Impact:** Users struggle to find a particular fixture, and the number displayed can be confused with total available coverage.

**Recommendation:** Add team search, competition/date filters, “Showing X of Y,” and Load more or pagination. Show local kickoff time and timezone only where the source provides them; otherwise say “Kickoff time unavailable.” Keep all results reachable without requiring a raw-data table.

**Acceptance:** A user can find a fixture beyond the first ten cards. Filtering updates both counts and results; date-only observations never gain invented kickoff times.

### UX09 — Probability presentation is unbalanced

**Evidence:** `app.py:551–575`. The most-likely score omits its probability while the alternative score shows one. A progress bar encodes only home-win probability beneath three outcome percentages.

**Impact:** The headline score can appear more certain than it is. The bar can be mistaken for confidence or completion rather than a single outcome probability.

**Recommendation:** Display the headline score's probability and clarify that “most likely” does not mean likely in absolute terms. Use a labelled Home/Draw/Away probability distribution with equivalent text. Show expected goals as a model estimate, explained on demand. Keep percentage rounding consistent and avoid apparent contradictory totals.

**Acceptance:** Every displayed score probability is traceable to the result; users can identify all three outcome probabilities without relying on color or interpreting an unlabeled progress bar.

### UX10 — Diagnostics explanations can reverse interpretation

**Evidence:** `pages/1_Model_Diagnostics.py:36–40,66–70`. The scatterplot describes top-right teams as defending well despite higher defence values meaning greater weakness. Sparse teams are described as pinned at zero although the implementation uses shrinkage.

**Impact:** Users may interpret the model's strongest and weakest teams backwards or misunderstand what limited history means.

**Recommendation:** Correct the chart caption and sparse-team explanation. Use “Attack strength” and “Defensive weakness — lower is better,” readable team names, and plain-language interpretation before Greek symbols. Put mathematical details in an expandable technical section.

**Acceptance:** Chart descriptions match axis direction and the fitted model's actual behavior. A user can explain which region represents strong attack and low defensive weakness without reading source code.

### UX11 — Navigation and empty states create dead ends

**Evidence:** `pages/1_Model_Diagnostics.py:13–15`; `pages/3_Ledger.py:18–20`; `pages/2_Backtest.py:24–34,47–49`; `app.py:442–444`. Screens instruct users to visit Matchday without a direct action. Backtest prominently offers demo generation when history is missing.

**Impact:** Users must remember instructions and navigate manually. Data unavailability routes them toward synthetic output instead of the intended production task.

**Recommendation:** Provide contextual navigation such as “Open Matches,” “Choose saved analysis,” or an operator-only “Review data connection.” Distinguish no saved analysis, no matching fixtures, no data coverage, and provider failure. Preserve the user's selected run and filters when returning from detail screens.

**Acceptance:** Every empty state explains what happened and offers a relevant next action when one exists. No recovery action silently creates synthetic production results.

### UX12 — Terminology and information hierarchy vary across pages

**Evidence:** `app.py:201–294,423–424,498–499,530–582`; page titles and metrics in `pages/1_Model_Diagnostics.py`, `pages/2_Backtest.py`, and `pages/3_Ledger.py`.

**Impact:** Users translate “tie,” “draw,” “deadlock,” internal storage names, Greek symbols, and analysis jargon while trying to complete basic tasks.

**Recommendation:** Use “draw” consistently. Organize user navigation around Matches, Performance, and History; expose Advanced diagnostics and Settings separately. Explain xG, EV, and CLV where encountered, and separate user-facing availability/save status from technical service diagnostics. Do not imply a predicted stake is an executed bet.

**Acceptance:** Navigation labels describe user tasks; equivalent concepts use the same wording across screens, tables, and exports. Primary content is understandable without expanding technical explanations.

## Accessibility and mobile validation backlog

These items are **unverified review targets**, not additional confirmed defects or a WCAG compliance assessment. Use [WCAG 2.2](https://www.w3.org/TR/WCAG22/) and its [reflow guidance](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html) for rendered testing.

- [ ] Check small badge/caption text, contrast, text resizing, and readability against actual backgrounds.
- [ ] Verify dense card headers, columns, controls, and wide tables at 320 CSS pixels and with browser zoom; essential actions must remain reachable.
- [ ] Complete core journeys by keyboard, with visible focus and logical focus order.
- [ ] Verify loading, error, and save messages are announced appropriately by assistive technology.
- [ ] Make explanations available to touch, keyboard, and screen-reader users; do not rely only on hover titles.
- [ ] Give charts equivalent textual values and labels; do not communicate status or eligibility through color alone.
- [ ] Check touch target sizes, long team names, large result sets, and reduced-motion preferences against the rendered implementation.

Record browser, viewport, assistive technology, steps, and observed results when these checks are eventually run. Do not mark them passed based on source inspection.

## Proposed seamless journey

1. Open Matches and immediately understand data availability, coverage, and freshness.
2. Filter competition/date or search for a team without configuring the model.
3. Inspect clearly labelled probabilities, source/run status, and match details.
4. Open supporting performance or diagnostics for that same analysis.
5. Revisit the saved run in History without losing context.
6. Authorized users record a result through deliberate entry, preview, persistent confirmation, and audited correction.

If a required source fails, show an understandable unavailable state with recovery actions. Preserve traceable historical results as historical; do not substitute them for a requested fresh run. Technical setup should not sit between the user and every forecast.

## Delivery order and cross-audit dependencies

First close the engine audit's P0 blockers. UX trust fixes can be developed alongside backend integrity work, but must not advertise guarantees before the backend enforces them.

| Work group | Findings | Dependencies |
|---|---|---|
| Honest run and eligibility state | UX02–UX05 | A02, A04, A06, A08, A12, A14; durable jobs and publication policy |
| Safe history and result entry | UX06–UX07 | A11–A12, A15; authoritative event/position accounting |
| Simple onboarding and navigation | UX01, UX08, UX11–UX12 | Explicit availability APIs and selected-run identity |
| Clear forecasts and explanations | UX09–UX10 | A01, A05, A17–A18; corrected model output and semantics |
| Rendered accessibility verification | Checklist above | Actual running UI; repeat for changed controls and layouts |

This supplements stage 4 of the [migration proposal](frontend-backend-migration.md). It does not require waiting for a complete frontend rewrite to correct misleading text, filter semantics, or stale-result labeling.

## Overall acceptance scenarios

- A new user finds a match without touching model settings or credentials.
- Editing inputs never makes prior results appear current; changing pages retains the selected run.
- Empty, failed, stale, running, and successful states are visibly distinct and provide appropriate next steps.
- Data failure produces no fabricated fixtures, silent source change, or misleading success status.
- Filters, summary counts, visible rows, and exported records agree.
- Headline scores and three-way probabilities convey uncertainty accurately.
- Result recording requires deliberate input, survives refresh with clear confirmation, and supports audited correction.
- Core tasks work on mobile, keyboard-only, and with tested assistive technology.

No usability testing with participants or runtime validation was performed in the source review. These scenarios are future acceptance requirements, not claimed test results. All UX01–UX12 findings remain open until changes and corresponding evidence are recorded.
