# Known Issues

## KI-001 — LightGBM CRLF Corruption on Windows (STATUS_STACK_BUFFER_OVERRUN)

**Severity:** High (causes complete forecast failure on Windows dev machines)
**Status:** Fixed via `.gitattributes` (Phase 3, 2026-05-15)

### Symptom

Running `python ml/forecast.py` or any script that loads `lgb.Booster(model_file=...)` on
Windows raises one of:

- `lightgbm.basic.LightGBMError: Model format error, expect a tree here`
- `STATUS_STACK_BUFFER_OVERRUN` (Windows error dialog, Python crash)

The crash disappears when running the same code on Linux/macOS or inside GitHub Actions
(Ubuntu runner).

### Root Cause

Git's `core.autocrlf=true` (the Windows default) silently converts LF line endings to CRLF
in checked-out text files. LightGBM model files (`models/production/lgbm.txt`,
`models/production/lgbm_lower.txt`, `models/production/lgbm_upper.txt`) are plain text and
were being converted. LightGBM's C++ parser expects LF-only line endings; on encountering
CRLF it fails to parse the tree structure and either errors or overruns a stack buffer.

This is **not** a Python stack overflow. The OS-level "STATUS_STACK_BUFFER_OVERRUN" dialog is
triggered by a buffer security check inside the LightGBM native library when parsing corrupted
model data.

### Fix

`.gitattributes` now marks model files as binary, disabling all line-ending conversion:

```
models/**/*.txt  binary
models/**/*.onnx binary
```

After pulling the change, developers on Windows must re-checkout the model files to strip the
CRLF:

```powershell
git rm --cached models/production/lgbm*.txt
git checkout models/production/lgbm*.txt
```

### Verification

Load the model and assert it parsed at least 1 tree:

```python
import lightgbm as lgb
b = lgb.Booster(model_file="models/production/lgbm.txt")
assert b.num_trees() > 0, "Model loaded 0 trees — likely CRLF corruption"
```

### See Also

- `.gitattributes` — binary attribute for model files
- `tests/test_model_load.py` — automated regression test
