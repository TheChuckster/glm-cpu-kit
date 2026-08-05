#!/usr/bin/env python3
"""Prove the decay-first refactor of ik's delta_net is identical for a per-head
gate, and correct for a per-channel one.

The refactor moved `decay` out of three separate expressions and into a single
in-place scale of the state. For a scalar decay that is algebra (a scalar
distributes over the sums); the point of this script is to confirm the algebra
rather than assume it, because a silent mismatch here would degrade every
Qwen3-Next model ik already serves.

The per-channel case is then checked against the same recurrence written the
obvious way, with decay indexed by the key/column axis, per mainline's
  "S[i][:] *= exp(g[i]) => for each row j of M: M[j][i] *= exp(g[i])"
"""
import numpy as np

rng = np.random.default_rng(0)
HD, T = 16, 5


def original(q, k, v, g_head, beta_raw, s0):
    """ik's ORIGINAL scalar path, transcribed verbatim."""
    state = s0.copy()
    out = np.zeros((T, HD), np.float64)
    eps, scale = 1e-12, 1.0 / np.sqrt(HD)
    for t in range(T):
        q_t, k_t, v_t = q[t], k[t], v[t]
        q_ni = 1.0 / np.sqrt((q_t * q_t).sum() + eps)
        k_ni = 1.0 / np.sqrt((k_t * k_t).sum() + eps)
        beta = 1.0 / (1.0 + np.exp(-beta_raw[t]))
        decay = np.exp(min(g_head[t], 50.0))
        attn = ((k_t * k_ni) * (q_t * q_ni * scale)).sum()
        v_new_buf = np.zeros(HD)
        for row in range(HD):
            v_prime = out_val = 0.0
            for col in range(HD):
                s = state[row + col * HD]
                v_prime += s * k_t[col]
                out_val += s * q_t[col]
            v_new = v_t[row] * beta - v_prime * beta * decay * k_ni
            v_new_buf[row] = v_new
            out[t, row] = out_val * decay * q_ni * scale + v_new * attn
        for col in range(HD):
            kc = k_t[col] * k_ni
            for row in range(HD):
                s = decay * state[row + col * HD] + v_new_buf[row] * kc
                state[row + col * HD] = np.clip(s, -1e6, 1e6)
    return out, state


def refactored(q, k, v, g, beta_raw, s0, per_channel):
    """The NEW path: decay the state in place first, then read it.
    g is [T] for per-head, [T, HD] for per-channel."""
    state = s0.copy()
    out = np.zeros((T, HD), np.float64)
    eps, scale = 1e-12, 1.0 / np.sqrt(HD)
    for t in range(T):
        q_t, k_t, v_t = q[t], k[t], v[t]
        q_ni = 1.0 / np.sqrt((q_t * q_t).sum() + eps)
        k_ni = 1.0 / np.sqrt((k_t * k_t).sum() + eps)
        beta = 1.0 / (1.0 + np.exp(-beta_raw[t]))
        attn = ((k_t * k_ni) * (q_t * q_ni * scale)).sum()
        # decay indexed by COLUMN (the key axis)
        for col in range(HD):
            d = np.exp(min(g[t, col] if per_channel else g[t], 50.0))
            state[col * HD:(col + 1) * HD] *= d
        v_new_buf = np.zeros(HD)
        for row in range(HD):
            v_prime = out_val = 0.0
            for col in range(HD):
                s = state[row + col * HD]
                v_prime += s * k_t[col]
                out_val += s * q_t[col]
            v_new = v_t[row] * beta - v_prime * beta * k_ni
            v_new_buf[row] = v_new
            out[t, row] = out_val * q_ni * scale + v_new * attn
        for col in range(HD):
            kc = k_t[col] * k_ni
            for row in range(HD):
                s = state[row + col * HD] + v_new_buf[row] * kc
                state[row + col * HD] = np.clip(s, -1e6, 1e6)
    return out, state


def reference_per_channel(q, k, v, g, beta_raw, s0):
    """Per-channel written the obvious matrix way, as an independent check.
    S[row, col]; decay multiplies column col."""
    S = s0.reshape(HD, HD, order="F").copy()  # S[row, col]
    out = np.zeros((T, HD), np.float64)
    eps, scale = 1e-12, 1.0 / np.sqrt(HD)
    for t in range(T):
        q_t, k_t, v_t = q[t], k[t], v[t]
        q_ni = 1.0 / np.sqrt((q_t * q_t).sum() + eps)
        k_ni = 1.0 / np.sqrt((k_t * k_t).sum() + eps)
        beta = 1.0 / (1.0 + np.exp(-beta_raw[t]))
        attn = ((k_t * k_ni) * (q_t * q_ni * scale)).sum()
        S = S * np.exp(np.minimum(g[t], 50.0))[None, :]   # broadcast over columns
        v_new = v_t * beta - (S @ k_t) * beta * k_ni
        out[t] = (S @ q_t) * q_ni * scale + v_new * attn
        S = np.clip(S + np.outer(v_new, k_t * k_ni), -1e6, 1e6)
    return out, S


q = rng.standard_normal((T, HD))
k = rng.standard_normal((T, HD))
v = rng.standard_normal((T, HD))
beta_raw = rng.standard_normal(T)
s0 = rng.standard_normal(HD * HD) * 0.1

print("=== 1. per-head gate: original vs refactored (must be identical) ===")
g_head = rng.standard_normal(T) * 0.5 - 1.0
o1, s1 = original(q, k, v, g_head, beta_raw, s0)
o2, s2 = refactored(q, k, v, g_head, beta_raw, s0, per_channel=False)
print(f"  max |out diff|   = {np.abs(o1 - o2).max():.3e}")
print(f"  max |state diff| = {np.abs(s1 - s2).max():.3e}")
print(f"  VERDICT: {'PASS - no regression for Qwen3-Next' if np.allclose(o1, o2, atol=1e-12) and np.allclose(s1, s2, atol=1e-12) else 'FAIL'}")

print()
print("=== 2. per-channel gate: kernel layout vs independent matrix reference ===")
g_chan = rng.standard_normal((T, HD)) * 0.5 - 1.0
o3, s3 = refactored(q, k, v, g_chan, beta_raw, s0, per_channel=True)
o4, S4 = reference_per_channel(q, k, v, g_chan, beta_raw, s0)
s3m = s3.reshape(HD, HD, order="F")
print(f"  max |out diff|   = {np.abs(o3 - o4).max():.3e}")
print(f"  max |state diff| = {np.abs(s3m - S4).max():.3e}")
print(f"  VERDICT: {'PASS - per-channel decay on the key axis is right' if np.allclose(o3, o4, atol=1e-10) else 'FAIL'}")

print()
print("=== 3. sanity: a per-channel gate with all channels equal == per-head ===")
g_flat = np.repeat(g_head[:, None], HD, axis=1)
o5, s5 = refactored(q, k, v, g_flat, beta_raw, s0, per_channel=True)
print(f"  max |out diff| vs per-head = {np.abs(o5 - o1).max():.3e}")
print(f"  VERDICT: {'PASS - degenerates correctly' if np.allclose(o5, o1, atol=1e-12) else 'FAIL'}")
