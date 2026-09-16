# Keyed claims and potential conflicts

A `key` identifies one knowledge slot inside one space, for example `atlas:approved-budget`. It is not a topic tag. Use different keys for independent assertions. Prefer `correct` with the inspected revision when you know a prior assertion has been replaced.

Saving different claims under the same key with overlapping validity marks them disputed. Comparison preserves numbers, signs, units, qualifiers, negation and word order. Only whitespace, case and trailing sentence punctuation are cosmetic. Word overlap does not prove agreement. Paraphrases or added qualifiers can conservatively require review; this is a potential conflict signal, not a semantic proof of contradiction.

Search/context return a `warnings` array. A warning remains present when the budget omits one or all conflicting alternatives. Do not choose an amount or owner from incomplete disputed context: inspect the evidence and explicitly resolve the claim. Unkeyed claims do not gain automatic semantic conflict detection.

Existing stores may contain incompatible claims left active by older releases. Recall detects and labels eligible same-key conflicts without rewriting stored records or revisions; exact inspection still shows persisted state. There is no automatic selection of a winner or destructive backfill. Separate spaces and nonoverlapping validity periods do not conflict.

## Regression coverage

`tests/test_claim_conflicts.py` exercises changed amounts, decimals, signs, dates, currencies, owners, reversed relationships, negation, legacy records, tight budgets, cosmetic repeats, scope isolation and nonoverlapping periods. The existing grounded-extraction regression now deliberately treats a changed qualifier as requiring review while retaining both source records.
