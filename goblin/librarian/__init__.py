"""Path Librarian: a probe-gated route store that sits beside any model.

The model is never trained or modified. After a hard job, a writer turns the tool trace (never
the model's prose) into a small card; a pure-code checker accepts or rejects it. On a repeat job
one card is injected only if its `applies` probe passes; afterwards its `success` probe decides
refresh or delete. Rules that must not be loosened: the writer works from traces only, the
checker is code, probes are allowlisted reads, cards never carry code, rollout is shadow -> store -> live.
"""
KEY_VERSION = "key_v1"
