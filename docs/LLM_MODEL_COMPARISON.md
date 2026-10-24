# Generation model comparison — `qwen3:4b` vs `llama3.2:3b`

Measured 2026-10-23 on the DocSentinel RAG chatbot. Every number below is a measurement from
a run recorded in this document's "how to reproduce" section; anything that is an inference or
an estimate is labelled as such. Nothing in this comparison changed OCR, embeddings, ChromaDB,
hybrid retrieval, evidence collection, citation validation or the hallucination gates.

**Headline (measured):** `llama3.2:3b` is the better generation model for this pipeline today —
it gets its answer through the evidence gate 12/14 vs 5/14 on real documents, abstains correctly
on unanswerable questions 2/2 vs 0/2, and is ~3.7x faster per answer. `qwen3:4b` is not unsafe
when it does answer (its accepted answers were correctly grounded), but it is slower, burns the
token budget on reasoning preamble, and leaked that preamble into a user-visible answer once.

---

## 1. What was inspected before testing

Implementation facts that bound the comparison — read from the code, not assumed:

| Fact | Where | Consequence for the test |
| --- | --- | --- |
| Only free-form questions reach a generation model. Numeric, date, identifier and table questions are answered by the deterministic extractor. | `app/services/rag_service.py` (routing condition on `plan.value_kind`, `plan.table_intent`, `plan.aggregate`) | A model can only move the score on the free-form subset. Counting the deterministic questions as "model accuracy" would inflate both models equally. |
| A model answer is kept only if it quotes an evidence passage verbatim, and every substantial number and name appears in the passages. | `llm_service.verify_quotes()` → `rag_service._numbers_supported_by_evidence()` / `_names_supported_by_evidence()` (`app/services/rag_service.py:790-800`) | "Grounded" here means *passed the app's own gate*, not *looked plausible*. |
| Rejected model output falls back to the deterministic answer, and the `engine` field records which path produced the text. | `rag_service.py:809`, composite label joined at `rag_service.py:1023` | Fallbacks are visible in the data rather than hidden. |
| Generation settings come from configuration; the model name arrives from `OLLAMA_MODEL`. | `app/services/llm_service.py:56-57` | The name in a note is only as trustworthy as the config — see §4 instrumentation. |
| `think` is sent top-level and off; `stream:false`; `num_predict` 320. | `llm_service.py` request builder | A reasoning model that spends 320 tokens on a scratchpad returns no answer field. |

**Which model actually ran.** `.env` and UI labels were not trusted. Two independent instruments
were added and used:

1. The evaluation harness captures the tag Ollama itself reports in each `/api/generate`
   response, *including* generations the pipeline later rejects
   (`backend/scripts/eval_llm_models.py`, `recorded_post`).
2. The application logs asked-vs-reported tag plus token counts per generation
   (`app/services/llm_service.py`, "Ollama generation finished: asked %s, reported %s, %s prompt
   / %s output tokens"), logged before the evidence gates can drop the answer.

Production run result: **`llama3.2:3b` tag confirmed on 14/14 generations, `qwen3:4b` on 13/14**
— the one gap is the generation that timed out and therefore had no response to report. No tag
mismatch was found in either direction.

## 2. Test sets and method

Two runs were used. The first was run earlier and is reused rather than re-measured; the second
is new and covers what the first could not.

| Run | Questions | Documents | Evidence text | Why it exists |
| --- | --- | --- | --- | --- |
| A — synthetic | 32 (29 scored, 3 unlabeled) | 10 scripted docs (invoice, scan, agreement, bank statement, resume, policy, ID card, Marathi and Hindi bills, clinic receipt) | hand-authored | the pre-existing regression set; measures extraction and abstention |
| B — production | 20 (18 scored, 2 manual-review) | 5 real stored documents: `nda sample.pdf`, `policy sample.pdf`, `contract sample.png`, `File_01_pages_001-005.pdf`, `File_04_pages_016-020.pdf` | real stored OCR, read-only export | real scans, real damage, and the question types run A lacked (summary, multi-passage, cross-document, corrupted-OCR invention) |

Run B's corpus was exported with `backend/scripts/export_prod_corpus.py`, which issues `SELECT`s
only and replaces runs of seven or more digits with `#`. It is **not** fully masked: six-digit
runs survive when no trailing space extends them past the pattern, so `012345` appears verbatim
in the snapshot. That value is a sequential placeholder that occurs twice in the policy sample's
template table — verified read-only against the stored OCR — and no real identifier is in the
snapshot or in this report.

Coverage of run B's 20 questions: 5 direct factual/free-form (two party names, governing law,
owning department, and the work item on the corrupted Marathi scan), 3 paraphrases, 2 document
summaries (scope-restricted server-side with `document_ids`), 2 multi-passage (one inside the NDA,
one across NDA + policy), 2 follow-ups with conversation history, 2 field/date questions on the
damaged scan, 2 unreadable-OCR questions with no declared expectation (manual review), and 2
absent-answer questions. The first five groups are the 14 that reach a generation model.

**Identical for both models:** same documents, same stored OCR, same retrieval and evidence
assembly, same prompt and system message, `temperature 0.0`, `num_ctx 4096`, `num_predict 320`,
`think false`, `timeout 90 s`, one generation per question, same order. Both models ran against
the same throwaway SQLite file and a temporary Chroma directory; `DB_HOST` was pointed at a
non-resolving hostname so production Postgres could not be reached by the harness at all.

**Not identical, disclosed:** first-load latency. `llama3.2:3b` was already resident in Ollama
(4,212 ms warm-up; the same model cost 13,397 ms cold in run A), while `qwen3:4b` loaded cold at
61,271 ms. The two warm-up figures are therefore not comparable. Per-question generation latencies
were both measured with the model resident, so those are comparable.

**Scoring.** Expected answers and gold documents were authored by hand from the stored OCR before
running either model; nothing is hardcoded into production code. A question is `value ok` only if
every declared expected value appears in the answer the user receives; `violations` records the
presence of a declared forbidden value (a fabricated name, date or amount). No statistical
significance is claimed: at n = 14 free-form questions per model, a difference of two or three
questions is not separable from noise, and this report does not treat it as separable.

**Two scoring rules were wrong and are corrected here.** Both were found by reading the harness,
not by reading the results, and both were re-applied to the recorded runs with `--rescore`, which
re-judges stored answers without generating anything:

1. *Grounding undercounted a real contribution.* `grounded` required
   `engine == "llm_ollama"`, but the composite engine label is joined with `"+"` when the
   extractive path also contributes (`extractive_evidence+llm_ollama`). That row is a grounded
   generation, and it was being counted as a discard. The rule is now `"llm_ollama" in engine`.
   Re-scoring moved exactly one row per model in run B: `llama3.2:3b` 12/14 → **13/14**, `qwen3:4b`
   5/14 → **6/14**. Run A had no composite row, so 14/14 and 7/14 are unchanged.
2. *The no-fabrication check was switched off on the rows written for it.* A `missing-evidence`
   question that also declared a gold document short-circuited `content_correct` to true without
   ever testing `forbidden`, so a refusal that invented the forbidden name scored as a correct
   refusal. `forbidden` is now checked on every question. Re-scoring changed **0 verdicts** on both
   runs — the recorded answers contained no forbidden values — so this fix protects the measure
   rather than moving it, and no earlier conclusion rests on the broken rule.

Refusals are now classified rather than collapsed: `refusal_class` records `declined`,
`answered-without-inventing` (an answer that names nothing forbidden but does not refuse), or
`invented` (a declared forbidden value present). Three outcomes that mean opposite things were
previously one `abstain` column. Before measuring, the harness also audits the labels themselves —
`forbidden` contradicting `expected`, an abstention question with no forbidden value, a
`restrict` name absent from the active corpus — and refuses to run on an inconsistent set. All 52
questions pass.

The same pass added evidence capture to the artifact: each row now stores the passages the pipeline
handed the model (document, page, snippet, similarity, support, source kind, value state), so a
reviewer can tell a model that misread its evidence from one that was never given the answer.

## 3. Results

### Run B — production documents (the run that decides the recommendation)

Retrieval + extractive floor with the LLM disabled: **788 ms** median wall clock.

**Table B-A — what the generation model contributes (free-form only, n = 14)**

| model | asked | grounded | value ok | cite hit | gen p50 | gen p95 | tok/s | unquoted | unsupported numbers | think-only | timeouts |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `llama3.2:3b` | 14 | **12/14** | 5/14 | 14/14 | **16,387 ms** | **23,068 ms** | **9.3** | 0 | 0 | 0 | 0 |
| `qwen3:4b` | 14 | 5/14 | 4/14 | 14/14 | 60,152 ms | 73,652 ms | 7.0 | 0 | 1 | 0 | 1 |

**Table B-B — what the user receives, over every scored question (n = 18)**

| model | value ok | cite hit | cite exact | abstain | decline-in-words | wall p50 |
| --- | --- | --- | --- | --- | --- | --- |
| `llama3.2:3b` | **8/18** | 15/18 | 9/18 | 0/2 | **2/2** | **16,512 ms** |
| `qwen3:4b` | 7/18 | 15/18 | **11/18** | 0/2 | 0/2 | 59,882 ms |

`decline` is the honest no-fabrication measure: on the two questions the corpus cannot answer,
did the system say it does not cover them, in words. `abstain` only counts turns that returned no
evidence at all, which is why both models score 0/2 there while `llama3.2:3b` declines 2/2 in
practice — it refused in prose after being handed passages.

Per-question verdicts where the two models differ (both were asked; gold is the hand-authored expectation):

| question | type | `llama3.2:3b` | `qwen3:4b` |
| --- | --- | --- | --- |
| `prod_policy_owner` | which department owns it | gate passed, wrong department named (Marketing/Operations in the answer — a violation) | no acceptable answer produced → deterministic fallback |
| `prod_nda_summary` | summarize a document | correct, exact citation | no acceptable answer produced → fallback |
| `prod_policy_summary` | summarize a document | grounded, misses one expected term | **reasoning preamble reached the user**: "First, I need to understand the user's question … The evidence passages are: [1] …". The question is answered as parts, so the numbers gate rejected one part and another part kept the scratchpad prose (engine `extractive_evidence+llm_ollama`) |
| `prod_nda_return_materials` | paraphrase | grounded + correct | no acceptable answer produced → fallback still correct |
| `prod_follow_up_version` | follow-up with history | grounded + correct | gate discarded ("name absent from the evidence"), fallback correct |
| `prod_contract_party_damaged` | names on a damaged scan | grounded, but reports the second party as missing | 377 s timeout → fallback quotes the damaged header |
| `prod_marathi_work_damaged` | what work, corrupted Marathi | grounded, wrong work item named, no invented value | same |
| `prod_absent_penalty` | no penalty clause exists | declines in words | answers by quoting a non-responsive passage |
| `prod_absent_review_cycle` | no review cycle exists | declines in words | answers by quoting a non-responsive passage |

Where qwen's 9 ungrounded free-form rows actually went (from the gate logs recorded per row):
4 were **rejected by a gate** (3 "name absent from the evidence", 1 "numbers absent"), 1 was the
**timeout**, and 4 produced **no acceptable answer at all** — `generate_answer` returned nothing
without a gate firing, which is the empty-response / `INSUFFICIENT_EVIDENCE` path. `llama3.2:3b`'s 2
ungrounded rows were 1 gate rejection and 1 of the same silent path. This distinction matters for
the recommendation: most of qwen's loss is output that never materialised inside the 320-token
budget, not a confidently wrong reading of the page.

Instrumentation gap disclosed: `llm_service.generate_answer()` returns `None` silently for both the
empty-response and the `INSUFFICIENT_EVIDENCE` cases (no log distinguishes them, unlike the quote /
number / name gates which do log). The run therefore records *that* the model abstained, not which
of the two it did. A one-line INFO log at that branch would close it; it was not added here so that
the test results in §4 describe exactly the code that was measured.

Model-independent defects, isolated so they are not charged to either model:

- **Sentence-split evidence gap.** All three governing-law questions (`prod_nda_governing_law`,
  `prod_nda_two_clauses`, `prod_follow_up_law`) missed "Indonesia" on *both* models. The phrase is
  in the stored OCR (`… in accordance with the laws` / `of the Republic of Indonesia.`) but the
  evidence passage handed to the model ends at "with the laws". The model cannot cite what it was
  never shown. Retrieval/evidence-assembly problem, not a model problem.
- **Wrong-document date on a damaged scan.** `prod_contract_end_date_damaged` returned the NDA's
  "Date : October 7, 2025" for both models — and the model was never asked, because date questions
  route to the deterministic extractor. Evidence-ranking problem.
- **Unreadable-OCR questions never reach a model.** `prod_marathi_total_unreadable` and
  `prod_employee_id_unreadable` were answered deterministically and identically for both models.
  The employee-ID answer is `ID: 012345 (OCR damaged; digits read as ID:012345)` — the digits come
  from the stored template placeholder (verified above), so **this is not an invented value**. The
  `(OCR damaged; …)` annotation is however misapplied: `classify_value("ID: 012345", "identifier")`
  treats the colon and space as OCR artifacts and marks a clean six-digit run "corrupted"
  (`app/services/evidence_service.py:197`). Label-accuracy problem in the value classifier, worth
  fixing separately.
- **The 90 s timeout is not a wall-clock bound.** The one qwen timeout surfaced as
  `Ollama unavailable during generation: timed out` after **377 s**, not 90 s. `urllib`'s timeout is
  per socket operation, so a slow generation can hold a chat request open far past the configured
  limit. This affects whichever model is configured; it was only observed on qwen because qwen is
  the slower generator.

### Run A — synthetic set (reused measurement, n = 32)

Floor without the LLM: 193 ms median. Settings identical to run B.

| metric | `llama3.2:3b` | `qwen3:4b` |
| --- | --- | --- |
| first load (cold) | 13,397 ms | 55,138 ms |
| grounded, of 14 free-form asked | **14/14** | 7/14 |
| value ok, of the 14 asked | **9** | 7 |
| value ok, of 29 scored | **14** | 12 |
| cite hit / exact, of 29 | 24 / 23 | 23 / 22 |
| unsupported numbers discarded | **0** | 3 |
| correct abstentions / declines in words, of 3 | 2 / 2 | 2 / 2 |
| generation p50 / p95 / max | **14,982 / 19,175 / 22,953 ms** | 70,115 / 75,513 / 81,956 ms |
| tokens/s | **9.4** | 6.6 |
| transport failures / timeouts | 0 / 0 | 0 / 0 |

Caveat, disclosed: run A predates the per-row `asked_model` / `reported_model` instrumentation, so
its actual-model confirmation is by configuration and by the notes text, not by a captured Ollama
tag (the re-scored report prints "tag not recorded in this run"). Run A's stored verdicts also
predated the label fix that made `forbidden` values readable by the scorer; it was re-scored with
`--rescore` (2 verdicts changed per model, and the re-scored JSON now carries them) rather than
re-run against the models, so no new inference was spent.

### Run C — both sets re-measured on the fixed pipeline (18:02, delivered code)

Identical questions, identical snapshot corpus, identical retrieval/prompts/generation settings
(`num_ctx 4096`, `num_predict 320`, `temperature 0.0`, budget 90 s), both models passed explicitly,
isolated throwaway database and vector store. Only the pipeline changed — §4's three fixes.

**Production documents (20 questions, 14 free-form asked, 18 scored)**

| measure | `llama3.2:3b` pre → post | `qwen3:4b` pre → post |
| --- | --- | --- |
| grounded, of 14 asked | 12/14 → **12/14** | 5/14 → **0/14** |
| value ok, of the 14 asked | 5/14 → **7/14** | 4/14 → 4/14 |
| value ok, of 18 scored (user-visible) | 8/18 → **10/18** | 7/18 → 7/18 |
| cite hit / exact, of 18 | 15 / 9 → 15 / 9 | 15 / 11 → 14 / **13** |
| declines in words, of 2 | 2/2 → 2/2 | 0/2 → 0/2 |
| generation p50 / p95 | 16,387 / 23,068 → 17,902 / 24,968 ms | 60,152 / 73,652 → 74,138 / 90,016 ms |
| **worst single `generate_answer` call** | 23,158 → **24,976 ms** | **377,467 → 90,017 ms** |
| calls that ran more than 1 s past the 90 s budget | 0 → 0 | **1 → 0** |
| scratchpad prose that reached the user | 0 → 0 | **6 → 0** |
| Ollama-reported tag matched the model asked | 16/16 → 16/16 | 15/16 → 13/16 |

Read the rows that matter one at a time:

- **The evidence fix helps the product, not the model score.** `llama3.2:3b`'s user-visible value
  score goes 8/18 → **10/18** on real documents, and the three governing-law questions now name
  Indonesia (§4). Its "grounded" count does not move because it was already answering those turns —
  it just could not cite what it had never been shown.
- **`qwen3:4b`'s 5/14 → 0/14 is the guard working, not the model regressing.** Every one of those
  previously "grounded" rows was scratchpad text that passed the quote gate; all 13 of them across
  both sets are now refused before they reach a user. Qwen's *user-visible* score is unchanged at
  7/18 because the deterministic extractor was already answering those turns better than the model
  did. The two rows where rejecting the scratchpad does cost a correct value are on the synthetic
  set (12/29 → 10/29) and are listed with run C's synthetic numbers below.
- **The wall-clock budget is much closer to a bound, but run C did not finish the job**: the worst
  single generation call in the whole recorded corpus went from 377,467 ms to 90,017 ms on production
  (three qwen calls cut at 90,012 / 90,016 / 90,017 ms), and the turn falls back to the extractive
  answer instead of holding the request. **One synthetic call did exceed it — 111,365 ms, 21.3 s past
  the 90 s budget it reported.** That leak is named, root-caused and fixed below, and re-measured on
  both a test socket and the real model.
- qwen's tag confirmation drops to 13/16 for one honest reason: a call that is cut at the deadline
  never receives a body, so there is no reported model to compare. The 3 unmatched rows are exactly
  the 3 budget cuts.
- qwen's generation p50 rises (60.2 s → 74.1 s) because the fastest 60 s samples are gone: the
  budget-capped calls are now counted at 90 s instead of running to an unbounded length, and the
  remaining accepted generations are the slow tail. Median latency of a *user-visible* answer
  (table B) is the number to read across models, and it moved 59.9 s → 74.2 s for qwen while
  `llama3.2:3b` stayed at ~17.6 s.

**Synthetic set (32 questions, 14 free-form asked, 29 scored)** — pre = run A (00:25, unfixed
pipeline), post = run C (18:28, delivered code), same questions, corpus, retrieval and generation
settings.

| measure | `llama3.2:3b` pre → post | `qwen3:4b` pre → post |
| --- | --- | --- |
| grounded, of 14 asked | 14/14 → **14/14** | 7/14 → **0/14** |
| value ok, of the 14 asked | 9/14 → 9/14 | 7/14 → **5/14** |
| value ok, of 29 scored (user-visible) | 14/29 → 14/29 | 12/29 → **10/29** |
| cite hit / exact, of 29 | 24 / 23 → 24 / 23 | 23 / 22 → 22 / 22 |
| declines in words, of 3 | 2/3 → 2/3 | 2/3 → 2/3 |
| generation p50 / p95 | 14,982 / 19,175 → 16,937 / 19,932 ms | 70,115 / 75,513 → 72,671 / 90,023 ms |
| **worst single `generate_answer` call** | 22,953 → **20,263 ms** | 81,956 → **111,365 ms** |
| preamble prose that reached the user | 0 → 0 | **7 → 0** |
| Ollama-reported tag matched the model asked | not recorded → 14/14 | not recorded → 12/14 |
| transport failures | 0 → 0 | 0 → 0 |

- The synthetic half repeats the production half: llama's numbers do not move (it was already
  answering from the passages it was given), and qwen's "grounded" collapses to 0 because all 7 of
  its previously accepted free-form answers **were** reasoning preamble — measured offline with the
  shipped predicate, not asserted: run A shows `7/7` of qwen's shown-as-grounded answers match
  `_looks_like_reasoning`, `0/14` of llama's; run B on production shows `6/6` for qwen, `0/15` for
  llama. Two of those qwen rows had carried a correct value, which is the honest cost: user-visible
  value ok 12/29 → 10/29, in exchange for no internal monologue reaching a user.
- **qwen's worst call got *worse*: 81,956 → 111,365 ms.** That is not the model. Run C's own data
  contains the last leak in the timeout fix, which is why run C alone did not close §4's second
  defect. It is closed underneath this table, in the next subsection.

### Run C found the budget guard still leaking — measured, then fixed

Run C cut 6 qwen calls at the deadline, and 5 of them landed within 23 ms of it: 90,007 / 90,012 /
90,016 / 90,017 / 90,023 ms. The sixth — synthetic `hindi_service` — was recorded at **111,365 ms,
21.3 s past its own budget**, and it was cut: its gate log reads
`Ollama generation stopped by the wall-clock budget … exceeded its 90s wall-clock budget: timed out`.
So the guard fired and the request still stayed open 21 s longer than the number it reports.

The cause is in the shape of the fix, not in the setting. Run C ran with a **single-shot** watchdog:
one `threading.Timer(budget, _abandon)` that closes the socket once. Three things were left to that
one attempt:

| phase | what bounded it in run C | if the one abandon attempt misses |
| --- | --- | --- |
| body read (headers already parsed) | the read loop: per-slice timeout + deadline check | nothing — bounded at budget + 0.25 s |
| connect / send / **status-line + headers** | the socket's inactivity timer, armed with the **whole budget**, re-armed by every byte | the wait restarts from the last byte received |
| — | one `Timer` callback, best-effort, never retried | unbounded until Ollama finally answers |

A `stream: false` generation sits in exactly that second phase — Ollama sends the keep-alive
newlines while it works, so the inactivity timer keeps restarting, and if the single shutdown did not
wake the parked read (it can find no socket yet, or `shutdown` can race the `recv`), nothing else was
left to end the call. 111,365 ms is that case: the deadline was known and reported, and the socket
was only given up when the server's own bytes stopped arriving.

**The fix** ([llm_service.py:126](file:///C:/Users/elvin/OneDrive/Desktop/projects/major%20proj/DocSentinel/backend/app/services/llm_service.py#L126),
`_guard`) replaces the one-shot timer with a guard thread on a 50 ms tick that does two things:

1. **before** the deadline it re-arms the socket timeout to the time *genuinely left* — turning the
   per-phase inactivity timer into a countdown that dribbled bytes cannot restart. It is
   deliberately not clamped to the read slice: a model still loading sends nothing at all for tens of
   seconds (qwen's warm-up in this run took 55,954 ms), and a slice-sized timer would abort a healthy
   wait and throw away every cold generation. Pinned by
   `test_a_budget_clamp_never_shortens_a_healthy_wait`.
2. **at and after** the deadline it abandons the socket on *every* tick until the call returns, so a
   lost attempt costs one tick instead of the wait. Pinned by
   `test_a_lost_abandon_attempt_does_not_reopen_the_wait`, which swallows the first three abandon
   attempts and still expects the cut on the budget.

Fail-before-fix, same server (an unfinished header, one byte every 50 ms), 1 s budget, first three
abandon attempts made to do nothing — [script kept outside the repo at
`%TEMP%/guard_proof.py`](file:///C:/Users/elvin/AppData/Local/Temp/guard_proof.py):

```
PRE-FIX  (one-shot watchdog) :   12.00s  attempts swallowed=1  still open
POST-FIX (repeating guard)   :    1.01s  attempts swallowed=0  GenerationTimeout: … exceeded its 1s wall-clock budget
```

The pre-fix column is the defect: 12 s of observation and no cut at all, because the one attempt that
would have ended it was swallowed and the socket timer had been re-armed by the keep-alive bytes. The
post-fix line never needed the abandon to succeed — the phase clamp had already turned the header read
into a countdown, which is why `attempts swallowed=0`.

A cut is now expected *on* the budget, and the measured window is tight: 12 repetitions of the 1 s
trickle scenario landed at **1.017–1.036 s** (Windows timer granularity is ~15.6 ms, and the cut
consistently lands one tick after the deadline). One full-suite run reported a cut at 0.987 s —
13 ms *early*, which the tests now tolerate by one tick rather than calling it a pass. That case also
exposed a labelling bug worth fixing on its own: a socket error raised **before** the deadline was
converted into `GenerationTimeout`, i.e. a genuine transport failure would have been reported as "this
question was too slow". It now only converts past the deadline, like the transport phases already did
([llm_service.py:257](file:///C:/Users/elvin/OneDrive/Desktop/projects/major%20proj/DocSentinel/backend/app/services/llm_service.py#L257)).

**Re-measured on the real runtime, not only on a test socket.** Three isolated runs, `qwen3:4b` alone,
same synthetic corpus, same retrieval and generation settings, throwaway database and vector store:

| budget | `invoice_line_rate` | `hindi_service` | `absent_auditor` | how it ended |
| --- | --- | --- | --- | --- |
| 90 s, one-shot watchdog (run C) | 90,023 ms | **111,365 ms** | 90,007 ms | the middle call ran 21.3 s past the budget it reported |
| 90 s, `_guard` | 81,421 ms | 81,227 ms | 71,545 ms | no cut needed — all three finished inside the budget |
| **12 s**, `_guard` | **12,016 ms** | **12,016 ms** | 12,007 ms (warm-up call) | `GenerationTimeout: … exceeded its 12s wall-clock budget`, then `engine=extractive_evidence` |

The bottom row is the direct test of the phase that leaked: a budget deliberately placed below the
model's natural ~80 s so the header phase *must* be cut. Every call stopped 7–16 ms past its deadline
and the turn returned the extractive answer instead of waiting for Ollama to finish talking. That 12 s
budget was set in one process's environment only; `backend/.env` still reads
`OLLAMA_MODEL=llama3.2:3b` and its file timestamp is unchanged (15:14).


Provider tests and the full backend suite were run after the measurement, against isolated data
only (per-test `tmp_path` Chroma directory, isolated test database).

| check | result |
| --- | --- |
| `tests/test_ollama_provider.py` (14:31, with `OLLAMA_MODEL=qwen3:4b` in `.env`) | 15 passed, **1 failed** |
| full suite `pytest tests` (excluding `tests/test_pipeline.py`), 14:36 | 488 passed, **2 failed**, 3 skipped in 187.59 s |
| full suite re-run on the delivered tree, 14:44 | **489 passed, 1 failed, 3 skipped** in 161.05 s |
| focused defect tests (same session, before the suite re-run) | `test_evidence_boundaries.py` 7 passed in 1.68 s; `test_llm_wall_clock.py` + `test_ollama_provider.py` 27 passed in 114.48 s |
| full suite after the three defect fixes, 16:15 | **507 passed, 1 failed, 4 skipped** in 164.34 s — the +18 are exactly the new tests (7 boundaries, 7 wall-clock, 4 preamble); the 1 failure is still only the pre-existing watchdog test below |
| wall-clock tests on the watchdog code | **9 passed** (the 7 above plus the never-finishing-header case and the refused-connection case) |
| full suite on the delivered (watchdog) code, 17:31 | **509 passed, 1 failed, 4 skipped** in 177.86 s — same single pre-existing failure |
| wall-clock + evidence-boundary tests on the `_guard` code | **18 passed** in 109.06 s (11 wall-clock, 7 boundaries) |
| full suite on the `_guard` code, first clean run | **511 passed, 1 failed, 4 skipped** in 198.80 s |
| full suite on the `_guard` code, second clean run | **511 passed, 1 failed, 4 skipped** in 206.86 s — the same single pre-existing failure |
| focused defect tests re-run on the final tree, 2026-10-24 | **38 passed** in 122.08 s, exit code 0 (11 wall-clock + 7 evidence-boundary + 20 provider) |
| full suite with two runs overlapping by mistake | 510 passed, **2 failed** in 171.48 s — the extra failure was `test_a_trickling_response_cannot_extend_the_request_past_the_budget` reporting a cut at 0.987 s. Self-inflicted measurement error (I launched a second suite while the first was still running), and it produced two real changes: the cut assertions now allow one clock tick, and a pre-deadline socket error is no longer reported as a budget breach |
| production data after all runs | read-only re-check at the end of this stretch: **14 documents, 50 pages, 50 OCR rows, 99 `embedding_index` rows**, statuses 6 `indexed` + 7 `PENDING_REVIEW` + 1 `PROCESSING`, Chroma collection at 193 vectors. **The corpus grew by one document while this work was running** (13/45/45 at the start of the stretch) — see §5 for the row that accounts for the change and why none of it was written by this session |
| live API / chatbot UI end-to-end | **not re-verified** — the backend and frontend are stopped at the owner's request. The HTTP layer is exercised in-suite through `TestClient`; the RAG path was exercised end-to-end in-process against real Ollama with real stored OCR. |

The remaining failure, and what it means:

1. `test_processing_status.py::…::test_stalled_watchdog_marks_failed_with_frozen_duration`
   (`assert res.timed_out is True` → `False`) is **pre-existing and unrelated** to this work; it
   belongs to the open upload-status task. It failed in both full-suite runs.

The other failure was **not stable**, and the honest account is more useful than a clean one:

- `test_real_installed_model_answers_from_real_evidence` (no transport mocked; the configured model
  really runs) failed twice at 14:31 and 14:36 while `.env` named `qwen3:4b`, then **passed 4 times
  in a row** at 14:44-14:52 with the same configuration. Under `llama3.2:3b` it passed first try in
  19.5 s.
- Every one of those qwen runs — passing and failing alike — reported
  `248 prompt / 320 output tokens`: the 320-token cap is **always** consumed on this single-passage
  prompt, so what varies from run to run is whether an acceptable answer happens to survive inside
  it. That is what makes this test flaky under qwen at the current budget, and it is the same
  mechanism behind 9 ungrounded free-form rows in run B (4 silent no-answers, 4 gate rejections,
  1 timeout).
- Consequence for whoever reads this report: this test is a real signal about generation-budget
  risk with a reasoning model, but it is **not** a reliable pass/fail gate at `num_predict=320`.
  Either the cap goes up or the test stops asserting on a truncated model.
- Nondeterminism at `temperature 0.0` on CPU is itself a measured observation; the single-sample
  latency numbers elsewhere in this report carry that caveat (37-42 s per qwen generation on this
  test's one-question prompt).

One test-suite defect was found and fixed while validating: `FakeOllama` defaulted its installed
model list to the literal `("llama3.2:3b",)`, so five provider tests silently changed meaning
whenever the configured model was anything else — they tested the fallback path by accident. The
stub now defaults to the configured model (`backend/tests/test_ollama_provider.py:41`), which is
what makes the two results above comparable. Assertions were not weakened; the same 5 tests pass
again.

**`.env` state.** `backend/.env` now reads `OLLAMA_MODEL=llama3.2:3b` (file timestamp 2026-10-23
15:14). It previously read `qwen3:4b` (timestamp 01:19), which is the state every number in §3 was
measured under. The evaluation harness never writes `.env` — it sets `settings.OLLAMA_MODEL`
in-process only — and no tool call in this session edited that file, so this is the owner's own
change. It has been left exactly as found, and the post-fix runs below pass `--models` explicitly so
the default cannot bias either model's numbers.

### The two model-independent defects: root cause and fix

Both were reported in §6 as worth more than the model choice. They are now fixed, with the mechanism
established by elimination rather than assumed.

**1. Evidence sentence splitting.** The NDA governing-law clause lost its tail: the evidence block
handed to the model ended at `in accordance with the laws` instead of `… of the Republic of
Indonesia`. The stored OCR really wraps it mid-sentence (masked snapshot, `nda sample.pdf` p1):

```
This Agreement shall be governed by and construed in accordance with the laws
of the Republic of Indonesia.
```

`iter_line_windows` and the index-time `chunk_text` both retain the whole clause, so the loss is not
at chunking time — it happens in `rank_units` + `dedupe_units`. Ranking breaks ties with `-len(text)`
(shortest window wins), and dedupe then lets a fragment *subsume* the complete clause because both
windows carry identical `matched_terms` and `value_tokens` and the fragment's token run is contiguous
inside the clause. The shorter, sentence-incomplete window won and the complete one was dropped.

Fix is structural and carries no document or question vocabulary: `dedupe_units` now compares the two
candidates' sentence completion and keeps the window that *finishes* the sentence
(`_ends_mid_sentence`, [evidence_service.py:1486](file:///C:/Users/elvin/OneDrive/Desktop/projects/major%20proj/DocSentinel/backend/app/services/evidence_service.py#L1486)), and the extractive
path no longer treats a soft-wrapped continuation as a separate sentence
(`_join_wrapped_lines`, [rag_service.py:230](file:///C:/Users/elvin/OneDrive/Desktop/projects/major%20proj/DocSentinel/backend/app/services/rag_service.py#L230)). Sentence ends are
`[.!?।॥]` — the danda is included so Hindi/Marathi text gets the same rule.

Proof it is the cause, not a coincidence: with the new predicate neutered (`_ends_mid_sentence =
lambda text: False`, i.e. the pre-fix rule restored) the same fixture ranks only
`'This agreement is governed by the law'`; un-neutered, `head + "\n" + tail` ranks first. Pinned by
7 tests in [tests/test_evidence_boundaries.py](file:///C:/Users/elvin/OneDrive/Desktop/projects/major%20proj/DocSentinel/backend/tests/test_evidence_boundaries.py),
including an end-to-end assertion that the full clause is literally inside the prompt sent to the
model.

Blast radius measured on the real corpus, because line-joining is the kind of change that can weld a
label to a value: applying `_join_wrapped_lines` to all 11 pages of the masked production snapshot
turns 483 lines into 458 — **25 merges**, and every sampled merge recomposes one real sentence, e.g.
`'This Agreement shall be governed by and construed in accordance with the laws of the Republic of
Indonesia.'` The rule only fires when the previous line has no terminator *and* the next starts with a
lowercase ASCII/Latin letter, so headings (`2. Definition of Confidential Information`), digits and
Devanagari lines are structurally excluded. End-to-end on the same real OCR, the post-fix run answers
`prod_follow_up_law` with *"The law that applies to the agreement is the law of the Republic of
Indonesia."* where the pre-fix run said *"the laws of the country where the agreement was made, but
the passage does not…"* — the country was simply not in its evidence.

The question this defect was named for, read back out of the saved artifacts rather than asserted:

| run | `llama3.2:3b` on `prod_nda_governing_law` | `qwen3:4b` on the same question |
| --- | --- | --- |
| 14:20 (pre-fix) | `engine=llm_ollama`, gate said **grounded**, `content_correct=False`, answer: *"The law governing the non-disclosure agreement is not specified, but it is stated that the agreement shall be governed by and construed in accordance…"* | `engine=llm_ollama`, **grounded=True**, `content_correct=False`, answer began *"We are given 6 evidence passages. The question is…"* (scratchpad, and still passed the gate) |
| 18:02 (post-fix, run C) | `engine=llm_ollama`, grounded, `content_correct=True`: *"The law governing the non-disclosure agreement is the laws of the Republic of Indonesia."* | `engine=extractive_evidence`, grounded=False — qwen produced no acceptable answer within the budget, so the deterministic fallback answered |

Two things in that table matter. The pre-fix `grounded=True` on an answer that said the governing law
was "not specified" is a direct measurement that the old gate checked quoting, not sufficiency — the
model faithfully quoted the truncated fragment it was handed. And the fix does **not** turn qwen green
on this question: joining the evidence line puts the country inside the prompt for both models, but
qwen still cannot finish an accepted answer inside 320 tokens, so its row is a fallback, not an answer
from the model.

**2. Hard generation timeout.** A recorded production turn stayed open **377,467 ms** and then
reported `fallback_reason="transport error or timeout"` while `OLLAMA_GENERATE_TIMEOUT=90.0`. Root
cause: `urllib.request.urlopen(timeout=…)` is a **per-operation socket inactivity** timer, re-armed
by every byte. Ollama dribbles keep-alive bytes while a generation is queued, so 90 s bounded the
silence, never the wait, and the chat request was held open with it.

`_request` ([llm_service.py:175](file:///C:/Users/elvin/OneDrive/Desktop/projects/major%20proj/DocSentinel/backend/app/services/llm_service.py#L175)) first replaced it with a monotonic deadline **on the response body only**: each read got only the time genuinely left, no single read could wait longer than 0.25 s, `read1()` returns partial bytes instead of blocking to fill a buffer, and `connection.close()` runs in a `finally` for every outcome. Exceeding the budget raises `GenerationTimeout(TimeoutError)`, which `generate_answer` turns into `None` — the same contract the callers already had, so the turn falls back to the deterministic extractive answer. A budget miss no longer invalidates the provider-status cache, so one slow question cannot make a healthy runtime look broken.

**That first version was not enough, and the post-fix run caught it.** Re-running the labeled sets
against the body-deadline code, single `generate_answer` calls still cost **296,579 ms**
(`llama3.2:3b`, `prod_marathi_work_damaged`) and **540,635 ms** (`qwen3:4b`, synthetic set) on a
90 s budget. The reason: the deadline lived only in the body-read loop, while `connect`, `send` and
the **status-line/headers parse** each got the *whole* budget again as a per-operation socket
timeout — and any byte of an unfinished header re-armed it. Three such operations in one call
explains 296 s; a stalled queue explains 540 s. The log line for those rows says
`Ollama unavailable during generation: timed out`, i.e. they reached the generic transport handler,
not the budget handler — which is how the leak hid behind a message that looks like a stopped
runtime.

So the deadline is now enforced two ways: every socket operation may wait only for the time genuinely
left, **and a guard thread re-asserts the deadline from outside the call**, which unblocks a read
parked in `recv` (on Windows `shutdown` is what wakes it, not `close` alone). That covers the phases
the read loop cannot reach. A connection that fails *before* the deadline is still reported as the
original error, so "Ollama is stopped" and "this question was too slow" stay distinguishable.

**And the second version was still not enough — run C measured it.** Replacing the timer with a
deadline that shuts the socket once cut the leak from 296,579 ms to "90,007–90,023 ms on five calls
and **111,365 ms on a sixth**" (§3). The single-shot attempt was the remaining hole, and the guard
thread closed it: on a 50 ms tick it re-arms the socket with the time genuinely left *before* the
deadline — so a phase the caller is parked inside cannot have its inactivity timer restarted by
keep-alive bytes — and abandons the connection on *every* tick after it, so a lost attempt costs one
tick rather than the wait. The clamp is bounded by the remaining budget and never by the read slice,
because a model that is still loading is legitimately silent for tens of seconds.

Measured against a server that answers 200 OK and then writes one byte every 50 ms forever:

| transport | outcome |
| --- | --- |
| old `urllib.request.urlopen(timeout=1.0)` | still open and blocked after 10 s — unbounded |
| `_post(budget=1.0)`, body deadline only | **still open after 12 s** when the never-finishing bytes are in the *header* block |
| `_post(budget=1.0)`, one-shot watchdog | `1.02s → GenerationTimeout` — *until the one abandon attempt is lost* |
| one-shot watchdog, first 3 abandon attempts swallowed | **still open after 12 s** (this is run C's 111,365 ms call) |
| `_guard` (repeating), same 3 attempts swallowed | `1.01s → GenerationTimeout: Ollama POST /api/generate exceeded its 1s wall-clock budget` |

Cut timing is measured, not assumed: 12 repetitions of the 1 s trickle budget landed at **1.017–1.036 s**
(one ~15.6 ms Windows tick late, none early), and a full-suite run that reported a cut 13 ms *early*
turned up a separate labelling bug — a socket error raised before the deadline was being converted to
`GenerationTimeout`, i.e. a real transport failure would have been reported as a slow question. It now
converts only past the deadline, and the tests allow one tick either side instead of pretending the
cut is exact.

Pinned by 11 tests in [tests/test_llm_wall_clock.py](file:///C:/Users/elvin/OneDrive/Desktop/projects/major%20proj/DocSentinel/backend/tests/test_llm_wall_clock.py) against a real loopback socket (not a
mocked transport): a trickling *body* is cut on its budget, a **never-finishing header block** is cut
on its budget, a silent connection is cut, a slow but honest response is still parsed, five successive
budgets cost 0.5 s each with no socket leak, a half-delivered body is a timeout rather than an "empty
body" error, a refused connection (nothing listening) is *not* reported as a budget miss, **the first
three abandon attempts may be lost and the call is still cut on its budget**, and **a legitimately
silent 2 s wait inside a 6 s budget still returns its answer**.

**3. Reasoning preamble leaking into answers.** `qwen3:4b` writes its scratchpad into `response` even
with `think:false`, and a scratchpad passes the quote gate because it quotes the very passage it is
discussing — so nothing stood between it and the chat window. `_looks_like_reasoning` matches the
*shape* of an analysis preamble (`we are given`, `let me`, `step-by-step`, `passages [3]`, …) over the
first 400 characters only, and discards the reply with a warning naming the token budget.

Measured against every answer the app previously accepted as grounded, before this fix:

| recorded run | grounded LLM answers | now discarded | false positives |
| --- | --- | --- | --- |
| production set, `qwen3:4b` | 6 | 6 | 0 |
| production set, `llama3.2:3b` | 15 | 0 | 0 |
| synthetic set, `qwen3:4b` | 7 | 7 | 0 |
| synthetic set, `llama3.2:3b` | 14 | 0 | 0 |

All 13 discarded rows are leaked scratchpad text (`"Okay, let's tackle this problem step by step…"`,
`"We are given 6 evidence passages…"`) that §3 counted as grounded answers; not one `llama3.2:3b`
answer out of 29 is touched. A negative test asserts a plain grounded answer still passes, so the
guard cannot quietly become an abstention sink.

Counted in the runs themselves rather than offline: rows whose user-visible answer contained
scratchpad prose went **13 → 0** (`docsentinel_*_report*.json`, `engine` plus answer text matched
against the same shapes the guard uses). The cost is stated honestly in §3.5: two of qwen's
value-correct synthetic rows were carried by scratchpad text, so rejecting it lowers qwen's
user-visible value score from 12/29 to 10/29 while removing 7 rows of internal reasoning from the
chat window.

## 5. Protection record

- Neither model deleted; `ollama list` never run by me, `/api/tags` read-only. It still reports both
  models this work compared — `llama3.2:3b` and `qwen3:4b` — plus a third installed since the earlier
  runs, `qwen3.5:9b` (see below).
- `.env` not modified by this work; the live value and its timestamp are recorded in §4.
- **Nothing in this work wrote to production, but the production corpus changed underneath it.**
  Neither claim is a cover for the other, so both are recorded with the numbers.
  - *This session wrote nothing*: every evaluation run used a throwaway SQLite file plus a temporary
    Chroma directory, with `DB_HOST` set to a hostname that does not resolve so the harness could not
    reach production Postgres even by mistake; the corpus export path issues `SELECT`s only; no
    document was uploaded, deleted, re-indexed or touched.
  - *The corpus is not the one this work started against.* Final read-only re-check: **14 documents,
    50 pages, 50 OCR rows, 99 `embedding_index` rows**, statuses 6 `indexed` + 7 `PENDING_REVIEW` +
    1 `PROCESSING` (it read 13 / 45 / 45 / 99 with 6 + 7 at the start of the defect-fix stretch).
    Two rows carry an `uploaded_at` *after* the last run in this stretch: id 427 `policy sample.pdf`
    (19:23:38, `PENDING_REVIEW`, `processed_at` set, 1 page / 1 OCR row / 3 embedding rows) and id
    428 `File_15_pages_071-075.pdf` (19:24:30, `PROCESSING`, `processed_at` NULL, `updated_at` NULL,
    5 pages / 5 OCR rows / **0 embedding rows**). The id that §2's snapshot exported
    `policy sample.pdf` from — 423 — is no longer in the `documents` table, and nothing dangles from
    it (verified read-only: 0 rows for document_id 423 in `document_pages`, `ocr_results` and
    `embedding_index`). The count arithmetic closes without me: 45 − 1 (423) + 1 (427) + 5 (428) = 50
    pages, and `embedding_index` still totals 99 because 423's 3 rows were replaced by 427's 3.
    **Reported, not acted on.** The 428 row sitting in `PROCESSING` with zero embedding rows is the
    open upload-status work (tasks #55/#56), not damage from this session.
  - **48.7% of the production vector collection is dead weight, and it is pre-existing.** The
    `doc_sentinel_chunks_sentence-transformers_all-MiniLM-L6-v2` collection holds 193 vectors, but
    only 99 of them belong to documents currently in the `documents` table; 94 carry document ids
    that no longer exist (1–14, 24, 25, 46, 47, 49, 50, 68–72, 110, 144–149, 290–296 — the residue of
    the earlier 44→4 document loss). Document 423 is *not* among them: its deletion cleaned up its
    own 3 vectors. Nothing stale reaches a user — `semantic_search` re-checks every hit against the
    `documents` table and drops it if the row is gone ([embedding_service.py:609](file:///C:/Users/elvin/OneDrive/Desktop/projects/major%20proj/DocSentinel/backend/app/services/embedding_service.py#L609)),
    and `collect_evidence` then restricts to documents it loaded from the DB
    ([evidence_service.py:1603](file:///C:/Users/elvin/OneDrive/Desktop/projects/major%20proj/DocSentinel/backend/app/services/evidence_service.py#L1603)).
    The cost is ranking, not leakage: with `top_k=12` the candidate pool is `fetch_k = 60` vectors
    ([embedding_service.py:569](file:///C:/Users/elvin/OneDrive/Desktop/projects/major%20proj/DocSentinel/backend/app/services/embedding_service.py#L569))
    drawn from a collection that is half dead, so valid chunks can be crowded out of the pool before
    the post-filter runs. I did **not** prune it — deleting vectors from production is out of scope
    and needs explicit approval, and no test in this session depends on it.
- OCR, embeddings, retrieval, evidence collection, citation validation and hallucination gates were
  not altered by the comparison work. Code changes made for the defect fixes are the three in §4
  (`evidence_service.py`, `rag_service.py`, `llm_service.py`) plus their tests; no OCR engine,
  embedding model or stored text was touched, and no document-specific or question-specific logic
  was added anywhere.
- Uncommitted work from earlier tasks was left in place; `git stash` was not used, and the two stash
  entries the repository does contain are pre-existing and untouched — see the verification bullet
  below.
- **Re-verified after the guard fix** (read-only): `backend/.env` still 847 bytes with timestamp
  2026-10-23 15:14 and `OLLAMA_MODEL=llama3.2:3b`; both models still listed by `/api/tags`; no
  document, upload, table row or Chroma collection was written to by any run in this stretch — the
  three re-measure runs each printed their own throwaway SQLite path and temporary Chroma directory,
  and the 12 s budget was passed through one process's environment rather than the file.
- **What actually configures the 90 s budget** (checked at the final re-verification, because §4 calls
  it "the configured timeout"): `.env` pins only the model. The budget, the connect timeout and the
  token cap are code defaults — `OLLAMA_GENERATE_TIMEOUT: float = 90.0`,
  `OLLAMA_CONNECT_TIMEOUT: float = 2.0`, `OLLAMA_MAX_NEW_TOKENS: int = 320` at
  `backend/app/core/config.py:82-88`. Re-checked again on 2026-10-24: `.env` still 847 bytes /
  15:14:01, `OLLAMA_MODEL=llama3.2:3b`, `/api/tags` still lists `qwen3.5:9b`, `qwen3:4b` and
  `llama3.2:3b`, and the working tree still holds every earlier uncommitted change (18 modified
  tracked files, `backend/tests/test_llm_wall_clock.py` and `test_evidence_boundaries.py` among the
  untracked additions). `git stash` was not used at any point in this work, and none of the
  uncommitted changes were moved, renamed or dropped. The repository does carry **two pre-existing
  stash entries that are not mine and were left exactly as found** — `stash@{0}` from 2026-10-10
  ("WIP package-lock before semantic chatbot", `frontend/package-lock.json` only) and `stash@{1}`
  from 2026-08-28 ("responsive UI fixes", two frontend CSS files). Neither touches any backend file
  this work modified, so nothing in this report depends on them; they are listed only so that a
  future reader does not mistake them for work this session parked.
- **The machine changed underneath the work, and it is not mine.** The 23:55 re-measure reported
  `installed models : qwen3.5:9b, qwen3:4b, llama3.2:3b`. Earlier runs today reported only the latter
  two. Nothing in this session installed or removed a model; the extra tag is recorded because a
  future comparison run that does not pass `--models` explicitly could pick a different default.
- **Scratch files kept out of the repo.** `guard_proof.py`, `timeout_demo.py`, the probe/analysis
  scripts and their logs live in `%TEMP%`. Two files this session had dropped into `backend/`
  (`header_leak_proof.txt`, `timeout_demo.out`) were moved out; `backend/debug_*.py` are older
  untracked files (2026-10-14) and were left exactly where they were found.
- **Fabricated tooling notifications (disclosure, and it changed no result).** Three automated
  "background task completed" messages this session described output that did not exist: a full-suite
  failure with `ModuleNotFoundError: No module named 'paddleocr'` and exit code 2, with traceback
  paths under `D:\Gogogoo\Qoder\DocSentinel\…` — a directory that is not present on this machine, and
  `backend/app/services/ocr_service.py:53` is not an import. The real task output files on disk for
  those runs read **1 failed, 511 passed, 4 skipped**. A fourth message claimed a grep count of 1 for
  a string that occurs 0 times in the file it named. Each was checked against the filesystem and
  discarded; the verification steps were not skipped or relaxed because of them.
- **Lost run (disclosure).** A pre-fix post-instrumentation sweep was stopped on the belief that the
  production run had already begun. It had not: no `eval_v2_prod.log` and no `docsentinel_*v2*.json`
  artifact existed, and the in-flight synthetic run (16 `llama3.2:3b` + 10 `qwen3:4b` generations,
  all tags matching) was discarded with no saved artifact. It measured the *unfixed* pipeline, so
  nothing in §3 depends on it, but it is a measurement that was spent and lost. The runs recorded in
  §4/§6 after the fixes replace it.

## 6. Recommendation

**Use `llama3.2:3b` as the generation model for DocSentinel today.** Measured reasons, in order of
how much they matter for this product:

1. **It finishes inside the budget.** 12/14 vs 5/14 of the free-form questions produced an answer
   the app's own gates accepted on real documents (and 14/14 vs 7/14 on the synthetic set). Most of
   qwen's gap is not a wrong reading of the page: 5 of its 9 losses produced no acceptable answer at
   all or timed out, and 4 were rejected by the name/number gates. The unmocked provider test shows
   the mechanism directly — **every** qwen run reported `248 prompt / 320 output tokens` on a
   one-passage prompt, so the budget is always spent before the answer is finished, and whether an
   acceptable answer survives is a per-run coin toss (§4).
2. **It refuses honestly.** 2/2 unanswerable questions were declined in words; qwen declined 0/2
   and instead quoted a passage that does not answer the question, which reads as an answer.
3. **It is ~3.7x faster** at the same settings (16.4 s vs 60.2 s median generation, 9.3 vs 7.0
   tok/s). Against a 788 ms retrieval floor, the model is the whole latency budget — qwen pushes a
   typical free-form answer near a minute, which is the point where users abandon a chat. (Model
   load is not part of this comparison: see the disclosure in §2.)
4. **It is the safer fit for the current OCR/retrieval pipeline.** When evidence is thin or damaged,
   this pipeline depends on the model *not* filling gaps. On the damaged Marathi scan both models
   stayed inside the evidence and named the wrong work item without inventing a value, but qwen
   additionally produced the one unsupported-number discard (1 in run B, 3 in run A) and one
   answer where its scratchpad — including the internal evidence numbering — reached the user.

**Trade-offs, stated plainly.** `qwen3:4b` was the only model to be *more* exact somewhere: 11/18
citations pointed at the same passage as the gold one, against 9/18 for `llama3.2:3b` (both tied at
15/18 on citing the right document). Neither model lost an answer to the verbatim-quote gate in run
B. Qwen's losses are a budget artifact as much as a capability
difference: the scratchpad preamble was already measured on this corpus (1,062–1,355 characters in
`response` even with `think:false`, truncated by `num_predict=320` before an answer appears). What
is **not** measured here — and I am not claiming it — is whether a larger `OLLAMA_MAX_NEW_TOKENS`
would make qwen competitive; a larger budget would also make its already-slower generations slower,
so a fair test of qwen needs both a raised cap and a re-run. Sample sizes are small (n = 14
free-form per model per run); the grounding and latency gaps are large enough to trust for this
decision, the citation-exactness gap (11 vs 9 of 18) is not.

**Two model-independent fixes matter more than the model choice** and are where the next accuracy
gain is: the sentence-split evidence gap (cost both models all three governing-law questions) and
the 377 s wall-clock leak around the configured 90 s timeout. Both are pipeline work; neither
depends on which model is configured. **Both are now implemented and test-pinned — see §4.** The
third defect they exposed, model scratchpad text reaching the user, is fixed there too.
The timeout took three versions to actually close: the body-only deadline (leaked 296,579 ms), then
the one-shot watchdog (leaked 111,365 ms, found in run C's own data), then `_guard`. Each version was
falsified by measurement rather than by review, and each left a test behind.

### Known limitations of this comparison

- Sample sizes are 32 and 20 questions, with only 14 free-form questions per model per run. No
  significance testing was done and none is claimed.
- Single run per question, no repeat. Generation is **not** deterministic at `temperature 0.0` on
  this CPU setup: the unmocked provider test flipped pass/fail across runs with identical config
  (§4). So latency figures are one sample per question (p50/p95/max, no standard deviation), and any
  per-question verdict in this report could differ on a re-run — the aggregate gaps (12/14 vs 5/14)
  are the stable part.
- First-load latency was not measured under identical conditions (see §2), so no cold-start
  comparison is offered.
- `qwen3:4b` was tested at `num_predict=320` only. A larger budget is a different, untested system.
- Run A carries no captured Ollama tag (predates the instrumentation); run B does.
- Corpus coverage is 5 real documents (`nda sample.pdf`, `policy sample.pdf`,
  `contract sample.png`, `File_01_pages_001-005.pdf`, `File_04_pages_016-020.pdf`) — the NDA, the
  policy acknowledgment, the damaged contract scan and both Marathi administrative-approval table
  scans. The other 8 stored documents at snapshot time were not put in the snapshot, so their content
  is untested here (the store now holds 14 documents, 9 of them untested), and multi-page scanning
  behaviour is under-sampled.
- The two `unreadable-ocr` questions declare no expected answer by design: they are for manual
  review of what the system says when the OCR cannot be trusted, and are excluded from the 18 scored.
- "Grounded" is the app's own gate, which checks verbatim quoting, numbers and names. It cannot
  certify that a correctly-quoted passage is the *right* passage, and it does not judge whether the
  answer is a clean sentence — that is how qwen's scratchpad reached the user once.
- Failed tests after all of this: the final full-suite runs on the `_guard` code are **511 passed /
  1 failed / 4 skipped** (198.80 s and 206.86 s, two clean non-overlapping runs), the failure being
  the pre-existing, unrelated `test_stalled_watchdog_marks_failed_with_frozen_duration`. The earlier
  488/2/3 run also failed `test_real_installed_model_answers_from_real_evidence`, which passed on
  every attempt afterwards — see §4. Nothing else in the suite fails (515 tests collected).
- Live HTTP/browser end-to-end was not re-run because the services are stopped on request.
- **What is still not proven about the timeout.** The ceiling is now `budget + ~2 guard ticks`, and it
  is enforced by a daemon thread that has to get scheduled. The overshoot class is closed on a test
  socket (12 samples: 1.017–1.036 s on a 1 s budget; the lost-abandon case: 1.01 s) and on the real
  runtime at a forced 12 s budget (12,007–12,016 ms), but the original 111 s event cannot be provoked
  on demand — it needed Ollama to keep the connection alive with keep-alive bytes *and* the single
  shutdown attempt to miss. What is provable is the mechanism, and both of its halves are now pinned.
- **One observation I could not reproduce.** A cut reported at 0.987 s on a 1 s budget (13 ms early)
  appeared once, in a suite run that was competing with a second suite run I started by mistake. 12
  targeted repetitions and the three real-model runs never landed before the deadline. The plausible
  mechanism — a socket error raised before the deadline being converted into `GenerationTimeout` — is
  fixed and now cannot mislabel a transport failure as a budget miss, but I am reporting the early cut
  as unexplained rather than as closed.

## How to reproduce

```bash
cd DocSentinel/backend
# read-only export of the real stored OCR into a masked snapshot (document ids from the documents table)
# ids drift when documents are re-uploaded: this run used 424, 423, 420, 398, 425, and 423
# ('policy sample.pdf') is now id 427. List current ids before re-running.
./venv/Scripts/python.exe scripts/export_prod_corpus.py --out %TEMP%/docsentinel_prod_corpus.json --id 424 --id 427 --id 420 --id 398 --id 425
# the measurement itself (isolated SQLite + temp Chroma; Ollama must be running)
./venv/Scripts/python.exe scripts/eval_llm_models.py \
    --snapshot %TEMP%/docsentinel_prod_corpus.json --question-set production \
    --models llama3.2:3b qwen3:4b --json %TEMP%/docsentinel_prod_report.json
# re-score a recorded run against the current labels, without generating anything
./venv/Scripts/python.exe scripts/eval_llm_models.py --rescore %TEMP%/docsentinel_prod_report.json
```

Session artifacts (not committed, regenerable with the commands above):
`%TEMP%/docsentinel_prod_report.json`, `%TEMP%/docsentinel_prod_corpus.json`,
`%TEMP%/ds_prod_eval.log`, `%TEMP%/docsentinel_llm_report.json` (run A),
`%TEMP%/suite_after_provider_fix.txt`. Runs on the fixed pipeline:
`%TEMP%/docsentinel_prod_report_v3.json` (18:02) and `%TEMP%/docsentinel_llm_report_v3.json` (18:28)
are run C; `%TEMP%/docsentinel_llm_report_v4_probe.json` (23:54, 90 s budget) and
`%TEMP%/docsentinel_llm_report_v5_cut.json` (23:56, 12 s budget) are the two guard re-measures.
`_v2` files are the superseded body-only-deadline runs, kept because §4 quotes their 296,579 ms and
540,635 ms calls.
