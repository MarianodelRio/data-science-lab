"""Error types shared across LLM-calling nodes."""


class FoldsAlreadyFrozenError(Exception):
    """Raised by ValidationStrategistNode when validation/fold_config.json already
    exists in the workspace. CV folds are frozen after Pipeline Phase 1 — every
    downstream node depends on consistent fold assignments, so the file must never
    be recomputed/overwritten. Raised BEFORE any write attempt (existence check is
    the first statement in _write_output), so the existing file is left untouched
    (byte-identical) whenever this fires.
    """
