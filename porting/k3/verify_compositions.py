#!/usr/bin/env python3
"""Check the ggml op SEQUENCES in build_kimi_k3.cpp against the oracle.

The risk in this port is not that ggml's sigmoid is wrong, it is that the
composition around it is. This replays each builder's exact op sequence in numpy
and diffs it against k3_ops_oracle.py, which costs seconds instead of the ~15
minutes an 861 GB model load takes.

    python3 verify_compositions.py
"""
import numpy as np
import k3_ops_oracle as oracle

rng = np.random.default_rng(7)
FAIL = []


def check(name, got, want, tol=2e-5):
    got, want = np.asarray(got, np.float32), np.asarray(want, np.float32)
    if got.shape != want.shape:
        print(f"  {name:28} SHAPE {got.shape} != {want.shape}")
        FAIL.append(name)
        return
    d = float(np.abs(got - want).max())
    ok = d <= tol
    print(f"  {name:28} max|diff| = {d:.3e}   {'ok' if ok else 'MISMATCH'}")
    if not ok:
        FAIL.append(name)


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


print("=== situ ===")
# builder: a = scale(tanh(scale(gate, 1/beta)), beta) * sigmoid(gate)
#          u = scale(tanh(scale(up, 1/lbeta)), lbeta)
#          out = a * u
T, D = 6, 32
x = rng.standard_normal((T, 2 * D)).astype(np.float32) * 3.0
gate, up = x[:, :D], x[:, D:]
beta, lbeta = 4.0, 25.0
a = beta * np.tanh(gate / beta) * sigmoid(gate)
u = lbeta * np.tanh(up / lbeta)
check("situ", a * u, oracle.situ(x, beta, lbeta))

print()
print("=== attn_res ===")
# builder: score(x) = sum_rows(rms_norm(x) * w)   [rms_norm carries NO weight]
#          probs    = softmax(concat(scores_ckpt..., score_cur))
#          out      = sum_c bank[c]*p_c + cur*p_cur   [over RAW tensors]
T, NB, H = 5, 4, 24
prefix = rng.standard_normal((T, H)).astype(np.float32)
blocks = rng.standard_normal((T, NB, H)).astype(np.float32)
norm_w = rng.standard_normal(H).astype(np.float32)
proj_w = rng.standard_normal(H).astype(np.float32)
eps = 1e-5
score_w = norm_w * proj_w          # the GGUF ships this already folded


def rms_norm(v):                    # ggml_rms_norm: no weight
    return v / np.sqrt(np.mean(v * v, axis=-1, keepdims=True) + eps)


sc = [np.sum(rms_norm(blocks[:, c, :]) * score_w, axis=-1) for c in range(NB)]
sc.append(np.sum(rms_norm(prefix) * score_w, axis=-1))
scores = np.stack(sc, axis=-1)                       # (T, NB+1)
probs = np.exp(scores - scores.max(-1, keepdims=True))
probs /= probs.sum(-1, keepdims=True)
out = sum(blocks[:, c, :] * probs[:, c:c + 1] for c in range(NB))
out = out + prefix * probs[:, NB:NB + 1]
check("attn_res", out, oracle.attn_res(prefix, blocks, proj_w, norm_w, eps))

print()
print("=== mla output gate ===")
# builder: kqv * sigmoid(mul_mat(attn_gate, layer_input))
T, HD = 5, 32
attn_out = rng.standard_normal((T, HD)).astype(np.float32)
g_pre = rng.standard_normal((T, HD)).astype(np.float32)
check("mla_output_gate", attn_out * sigmoid(g_pre),
      oracle.mla_output_gate(attn_out, g_pre))

print()
print("=== kda gate (per-channel, lower-bound form) ===")
# builder: g  = f_b(f_a(x)) + dt_bias
#          g  = reshape(g, K, H, T)          <- K is the FAST axis
#          g  = g * reshape(ssm_a, 1, H, 1)  <- per-head, broadcast over K
#          g  = sigmoid(scale(g, -1)) * lower_bound
# with ssm_a == -exp(A_log) as shipped by the converter.
T, HH, KK = 4, 6, 8
g_raw = rng.standard_normal((T, HH, KK)).astype(np.float32)
dt_bias = rng.standard_normal(HH * KK).astype(np.float32)
A_log = rng.standard_normal(HH).astype(np.float32) * 0.5
ssm_a = -np.exp(A_log)                      # what the GGUF actually stores
lower = -5.0

g = g_raw + dt_bias.reshape(HH, KK)         # matches ggml broadcast of [H*K]
g = g * ssm_a.reshape(1, HH, 1)             # reshape_3d(ssm_a, 1, H, 1)
g = sigmoid(-g) * lower
check("kda_gate", g,
      oracle.kda_gate(g_raw[None], A_log, dt_bias, lower_bound=lower)[0])

print()
if FAIL:
    print(f"FAILED: {', '.join(FAIL)}")
    raise SystemExit(1)
print("all compositions match the oracle")
