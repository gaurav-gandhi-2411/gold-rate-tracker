"""ml.direction — directional-forecast evaluation harness (Phi23).

Walk-forward OOS evaluation of whether a learned classifier beats the
"always-up" base-rate baseline (~70% bull days).  The gate in gate.py
is the load-bearing decision that controls whether a calibrated direction
probability is ever shown to users.
"""
