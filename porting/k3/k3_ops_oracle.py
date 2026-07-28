#!/usr/bin/env python3
"""Reference implementations + test fixtures for Kimi-K3's four new ops.

Kimi K3 needs four operations that ik_llama.cpp does not have. Three of them
fail SILENTLY when implemented wrong: the model still runs, it just produces
slightly worse text. That is the worst possible failure mode for a 2.8T model
nobody can run at reference precision to compare against. So the ops get pinned
down here first, and the C implementation is checked against these fixtures op
by op, before anything is wired into a graph.

Every function below is transcribed from Moonshot's own `modeling_kimi_linear.py`
(trust_remote_code, in moonshotai/Kimi-K3) with the source line cited. Read them
side by side - the whole value of this file is being a faithful transcription,
and a transcription nobody checked is worth nothing.

  situ            modeling_kimi_linear.py:64-83   (class SituAndMul)
  attn_res        modeling_kimi_linear.py:1075-88 (def _apply_attn_res)
  mla_output_gate modeling_kimi_linear.py:470-473 (KimiMLA.forward tail)
  kda_gate        fla-core 0.5.1 fla/ops/kda/gate.py, as vendored by
                  gavamedia/deltafin tools/fla/ops/kda/__init__.py

Usage:
    python3 k3_ops_oracle.py --emit fixtures/     # write fixtures + manifest
    python3 k3_ops_oracle.py --verify fixtures/   # re-derive, compare hashes
    python3 k3_ops_oracle.py --check-torch        # cross-check vs PyTorch

numpy only, so this runs on the inference box itself. --check-torch is the
guard against transcription error and needs torch; it is not needed to emit.
"""
import argparse
import hashlib
import json
import os
import sys

import numpy as np

# --- K3's real constants, from moonshotai/Kimi-K3 config.json -----------------
# Fixtures below use small shapes to stay reviewable, EXCEPT where a dimension
# is load-bearing: KDA's K=V=128 is required by the kernel contract, and A_log
# being per-channel [K] rather than per-head [H] is the whole reason ik's
# existing ggml_delta_net cannot be reused as-is.
K3 = dict(
    hidden_size=7168,
    routed_expert_hidden_size=3584,   # latent MoE: experts run at 3584, not 7168
    moe_intermediate_size=3072,
    num_experts=896,
    num_experts_per_token=16,
    num_shared_experts=2,
    num_hidden_layers=93,
    attn_res_block_size=12,
    activation_situ_beta=4.0,
    activation_situ_linear_beta=25.0,
    kda_head_dim=128,                 # K = V = 128, required
    kda_num_heads=96,
    kda_short_conv_kernel_size=4,
    kda_gate_lower_bound=-5.0,
    kda_use_full_rank_gate=True,      # => A_log is [K], not [H]
    rms_norm_eps=1e-5,
)

SEED = 0x4B33  # "K3"


# --- helpers ------------------------------------------------------------------

def _sigmoid(x):
    # Branch on sign so exp() never sees a large positive argument.
    out = np.empty_like(x)
    pos, neg = x >= 0, x < 0
    out[pos] = 1.0 / (1.0 + np.exp(-x[pos]))
    ex = np.exp(x[neg])
    out[neg] = ex / (1.0 + ex)
    return out


def _softplus(x):
    # log1p(exp(x)) overflows for x >~ 88 in float32; logaddexp does not.
    return np.logaddexp(np.zeros_like(x), x)


def _softmax(x, axis=-1):
    z = x - np.max(x, axis=axis, keepdims=True)
    e = np.exp(z)
    return e / np.sum(e, axis=axis, keepdims=True)


# --- the four ops -------------------------------------------------------------

def situ(x, beta=4.0, linear_beta=25.0):
    """SiTU-and-multiply. Replaces SwiGLU EVERYWHERE in K3 (hidden_act="situ").

    modeling_kimi_linear.py:77-82
        gate, up = x[..., :d], x[..., d:]
        situ_a = beta * tanh(gate / beta) * sigmoid(gate)
        if linear_beta is not None:
            up = linear_beta * tanh(up / linear_beta)
        return situ_a * up

    Read it as a range-limited SwiGLU: as beta -> inf, beta*tanh(g/beta) -> g and
    situ_a -> g*sigmoid(g) = SiLU(g). The tanh soft-clips gate to +/-beta and up
    to +/-linear_beta. That clipping is not cosmetic - K3 is QAT'd with MXFP8
    activations, and these are the bounds the quantiser was trained against.

    Both halves are computed in float32 and cast back, per the reference.
    """
    x = np.asarray(x)
    d = x.shape[-1] // 2
    gate = x[..., :d].astype(np.float32)
    up = x[..., d:].astype(np.float32)
    situ_a = beta * np.tanh(gate / beta) * _sigmoid(gate)
    if linear_beta is not None:
        up = linear_beta * np.tanh(up / linear_beta)
    return (situ_a * up).astype(x.dtype)


def attn_res(prefix_sum, block_residual, proj_weight, norm_weight, eps=1e-5):
    """Attention Residuals: K3's replacement for the plain residual stream.

    modeling_kimi_linear.py:1075-1088
        v = cat((block_residual, prefix_sum.unsqueeze(1)), dim=1)
        k = v * rsqrt(mean(v^2, -1) + eps)          # RMSNorm, WITHOUT its weight
        score_weight = norm.weight * proj.weight.squeeze(0)
        scores = (k * score_weight).sum(-1)
        probs = scores.softmax(-1)
        return probs @ v

    Instead of accumulating h += f(h) uniformly, each layer takes a softmax-
    weighted average over the outputs of previous blocks - it selectively
    retrieves across depth. K3 applies it TWICE per layer (before attention and
    before the MLP) with separate proj/norm weights.

    Two things matter for the C port:

    1. norm.weight and proj.weight collapse into ONE [hidden] vector that is
       constant per layer. Fold them at load time; at runtime this op is an
       RMSNorm, a dot product against that vector, a softmax over (nblocks+1),
       and a weighted sum. Trivial FLOPs, exactly as advertised - but it needs
       the per-block residual history kept live, which is the real cost.
    2. The RMSNorm here deliberately has NO weight applied to v. The weight
       enters only through score_weight, and the weighted sum is over the
       UNNORMALISED v. Normalising the output instead is the obvious wrong turn.

    prefix_sum:     (T, H)
    block_residual: (T, nblocks, H)   -> returns (T, H)
    """
    prefix_sum = np.asarray(prefix_sum, dtype=np.float32)
    block_residual = np.asarray(block_residual, dtype=np.float32)
    v = np.concatenate([block_residual, prefix_sum[:, None, :]], axis=1)
    variance = np.mean(v * v, axis=-1, keepdims=True)
    k = v / np.sqrt(variance + eps)
    score_weight = np.asarray(norm_weight, np.float32) * np.asarray(proj_weight, np.float32)
    scores = np.sum(k * score_weight, axis=-1)
    probs = _softmax(scores, axis=-1)
    return np.einsum("tb,tbh->th", probs, v).astype(np.float32)


def mla_output_gate(attn_output, g_pre):
    """Gated MLA: a sigmoid gate on the attention output, BEFORE o_proj.

    modeling_kimi_linear.py:470-473
        if self.use_output_gate:
            g = self.g_proj(hidden_states).sigmoid()
            attn_output = attn_output * g
        attn_output = self.o_proj(attn_output)

    g_pre is g_proj applied to the layer's INPUT hidden_states - not to the
    attention output. Gating on the wrong tensor is a silent, plausible-looking
    bug, which is why this three-line op gets a fixture at all.
    """
    return (np.asarray(attn_output, np.float32) * _sigmoid(np.asarray(g_pre, np.float32)))


def kda_gate(g, A_log, dt_bias, lower_bound=-5.0):
    """KDA's forget gate - the op ik's ggml_delta_net cannot currently express.

    fla/ops/kda/gate.py, via deltafin tools/fla/ops/kda/__init__.py:_kda_gate
        g = g + dt_bias.view(H, K)
        a = exp(A_log)                       # [H] -> (H,1)  or  [K] -> (1,K)
        if lower_bound is not None:
            g = lower_bound * sigmoid(a * g)
        else:
            g = -a * softplus(g)

    THE PORTING PROBLEM, in one line: K3 ships A_log with shape [128] = [K],
    i.e. one decay per CHANNEL, broadcast across all heads. Qwen3-Next's gated
    DeltaNet - which is what ik's ggml_delta_net implements - has one SCALAR
    decay per head. K3 sets use_full_rank_gate=true and gate_lower_bound=-5.0.

    So the gate `g` entering the recurrence is a full [B,T,H,K] tensor rather
    than [B,T,H], and ggml_delta_net's inner loop has to broadcast a per-channel
    decay instead of a per-head scalar. That is the single highest-risk item in
    the whole port, and this fixture is how you know you got it right.

    g:       (B, T, H, K) raw gate projection
    A_log:   (K,) for K3 (per-channel), or (H,) for the Kimi-Linear-48B variant
    dt_bias: (H*K,) or None
    """
    g = np.asarray(g, np.float32)
    B, T, H, K = g.shape
    if dt_bias is not None:
        g = g + np.asarray(dt_bias, np.float32).reshape(H, K)
    A_log = np.asarray(A_log, np.float32)
    if A_log.size == H:
        a = np.exp(A_log).reshape(H, 1)
    elif A_log.size == K:
        a = np.exp(A_log).reshape(1, K)
    else:
        raise ValueError(f"A_log size {A_log.size} matches neither H={H} nor K={K}")
    if lower_bound is not None:
        return lower_bound * _sigmoid(a * g)
    return -a * _softplus(g)


def latent_moe_shape_note():
    """Not an op - a shape contract that is easy to get wrong. No fixture.

    modeling_kimi_linear.py:814-838

        topk_idx, topk_w = gate(hidden_states)        # ROUTER on full hidden 7168
        h = routed_expert_down_proj(hidden_states)    # 7168 -> 3584
        y = moe_infer(h, topk_idx, topk_w)            # experts live at 3584
        if latent_moe_use_norm: y = routed_expert_norm(y)   # RMSNorm at 3584
        y = routed_expert_up_proj(y)                  # 3584 -> 7168
        y = y + shared_experts(identity)              # shared on full hidden 7168

    Three different widths in one block. The router scores the FULL 7168-wide
    hidden, the 16 routed experts run in a 3584-wide latent space, and the 2
    shared experts run at 7168 on the ORIGINAL input (identity), not on the
    down-projected one. A DeepSeek-style MoE block, which is what ik has, runs
    everything at one width - so this is a new code path, not a parameter change.

    Per-expert weights: 3 * 3584 * 3072 = 33.0M params = 17.5 MB at MXFP4's
    4.25 bpw. Times 16 active times 92 MoE layers = 25.8 GB read per token.
    """
    return dict(
        router_width=K3["hidden_size"],
        expert_width=K3["routed_expert_hidden_size"],
        expert_intermediate=K3["moe_intermediate_size"],
        shared_expert_width=K3["hidden_size"],
        bytes_per_expert=3 * 3584 * 3072 * 4.25 / 8,
        bytes_per_token=16 * 92 * (3 * 3584 * 3072 * 4.25 / 8),
    )


# --- fixtures -----------------------------------------------------------------

def build_fixtures():
    """Deterministic inputs + reference outputs for every op.

    Shapes are small enough to eyeball, except K=128 which is fixed by the KDA
    kernel contract and A_log which must be per-channel to exercise the case
    that actually differs from Qwen3-Next.
    """
    rng = np.random.default_rng(SEED)
    f = {}

    # SiTU. Range matters: values must reach where tanh clipping bites, or the
    # fixture would pass against a plain SwiGLU and prove nothing. linear_beta
    # is 25, so up needs samples well beyond +/-25.
    x = np.concatenate([
        rng.normal(0, 6.0, size=(64, 256)).astype(np.float32),    # gate half
        rng.normal(0, 40.0, size=(64, 256)).astype(np.float32),   # up half
    ], axis=-1)
    f["situ"] = dict(
        inputs={"x": x},
        params={"beta": K3["activation_situ_beta"],
                "linear_beta": K3["activation_situ_linear_beta"]},
        output=situ(x, K3["activation_situ_beta"], K3["activation_situ_linear_beta"]),
    )

    # AttnRes. nblocks = ceil(93 / 12) = 8 for the last layer of K3.
    T, H, NB = 32, 256, 8
    prefix_sum = rng.normal(0, 1.0, size=(T, H)).astype(np.float32)
    block_residual = rng.normal(0, 1.0, size=(T, NB, H)).astype(np.float32)
    proj_w = rng.normal(0, 0.05, size=(H,)).astype(np.float32)
    norm_w = rng.normal(1.0, 0.05, size=(H,)).astype(np.float32)
    f["attn_res"] = dict(
        inputs={"prefix_sum": prefix_sum, "block_residual": block_residual,
                "proj_weight": proj_w, "norm_weight": norm_w},
        params={"eps": K3["rms_norm_eps"]},
        output=attn_res(prefix_sum, block_residual, proj_w, norm_w, K3["rms_norm_eps"]),
    )

    # Gated MLA.
    attn_out = rng.normal(0, 1.0, size=(32, 256)).astype(np.float32)
    g_pre = rng.normal(0, 3.0, size=(32, 256)).astype(np.float32)
    f["mla_output_gate"] = dict(
        inputs={"attn_output": attn_out, "g_pre": g_pre},
        params={},
        output=mla_output_gate(attn_out, g_pre),
    )

    # KDA gate, per-channel A_log - the K3 case.
    B, T2, Hh, K = 1, 16, 4, K3["kda_head_dim"]
    g = rng.normal(0, 2.0, size=(B, T2, Hh, K)).astype(np.float32)
    A_log = rng.normal(0, 0.5, size=(K,)).astype(np.float32)
    dt_bias = rng.normal(0, 0.1, size=(Hh * K,)).astype(np.float32)
    f["kda_gate_per_channel"] = dict(
        inputs={"g": g, "A_log": A_log, "dt_bias": dt_bias},
        params={"lower_bound": K3["kda_gate_lower_bound"]},
        output=kda_gate(g, A_log, dt_bias, K3["kda_gate_lower_bound"]),
    )

    # KDA gate, per-head A_log with no lower bound - the Kimi-Linear-48B path.
    # Included so a single implementation can be proven to cover both, since ik
    # may want to keep one kernel for both architectures.
    A_log_h = rng.normal(0, 0.5, size=(Hh,)).astype(np.float32)
    f["kda_gate_per_head_softplus"] = dict(
        inputs={"g": g, "A_log": A_log_h, "dt_bias": dt_bias},
        params={"lower_bound": None},
        output=kda_gate(g, A_log_h, dt_bias, None),
    )
    return f


def emit(dirpath):
    os.makedirs(dirpath, exist_ok=True)
    fixtures = build_fixtures()
    manifest = {
        "seed": SEED,
        "note": "float32 little-endian raw arrays; C order. Regenerate with "
                "k3_ops_oracle.py --emit and diff the manifest.",
        "k3_config": K3,
        "latent_moe": latent_moe_shape_note(),
        "ops": {},
    }
    for op, spec in fixtures.items():
        entry = {"params": spec["params"], "inputs": {}, "output": {}}
        for name, arr in list(spec["inputs"].items()) + [("__out__", spec["output"])]:
            arr = np.ascontiguousarray(arr, dtype=np.float32)
            fn = f"{op}.{'out' if name == '__out__' else name}.f32"
            arr.tofile(os.path.join(dirpath, fn))
            rec = {"file": fn, "shape": list(arr.shape),
                   "sha256": hashlib.sha256(arr.tobytes()).hexdigest()}
            if name == "__out__":
                entry["output"] = rec
            else:
                entry["inputs"][name] = rec
        manifest["ops"][op] = entry
    with open(os.path.join(dirpath, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"wrote {len(fixtures)} ops to {dirpath}/")
    for op in fixtures:
        print(f"  {op}")
    return 0


def verify(dirpath):
    """Re-derive from the same seed and compare hashes. Catches an accidental
    edit to an op that would otherwise silently move the goalposts for the C
    side - the fixtures are a contract, not scratch data."""
    path = os.path.join(dirpath, "manifest.json")
    if not os.path.exists(path):
        print(f"no manifest at {path}; run --emit first", file=sys.stderr)
        return 1
    with open(path) as fh:
        old = json.load(fh)
    fixtures = build_fixtures()
    bad = 0
    for op, spec in fixtures.items():
        want = old.get("ops", {}).get(op)
        if want is None:
            print(f"  {op}: MISSING from manifest"); bad = 1; continue
        got = hashlib.sha256(
            np.ascontiguousarray(spec["output"], dtype=np.float32).tobytes()).hexdigest()
        if got != want["output"]["sha256"]:
            print(f"  {op}: OUTPUT CHANGED\n    manifest {want['output']['sha256'][:16]}"
                  f"\n    current  {got[:16]}")
            bad = 1
        else:
            print(f"  {op}: ok")
    return bad


def check_torch():
    """Cross-check the numpy transcription against PyTorch, running Moonshot's
    own expressions. This is the guard against a plausible-but-wrong reading of
    the reference - the failure mode this whole file exists to prevent."""
    try:
        import torch
        import torch.nn.functional as F
    except ImportError:
        print("torch not installed; skipping (pip install torch)", file=sys.stderr)
        return 0

    rng = np.random.default_rng(SEED + 1)
    worst = {}

    # SiTU, exactly as modeling_kimi_linear.py:77-82 writes it.
    x = rng.normal(0, 8.0, size=(16, 128)).astype(np.float32)
    beta, lb = K3["activation_situ_beta"], K3["activation_situ_linear_beta"]
    tx = torch.from_numpy(x)
    d = tx.shape[-1] // 2
    gate, up = tx[..., :d].float(), tx[..., d:].float()
    ref = (beta * torch.tanh(gate / beta) * torch.sigmoid(gate)) * (lb * torch.tanh(up / lb))
    worst["situ"] = float(np.abs(ref.numpy() - situ(x, beta, lb)).max())

    # AttnRes, exactly as modeling_kimi_linear.py:1080-1088 writes it.
    T, H, NB, eps = 8, 64, 4, K3["rms_norm_eps"]
    ps = rng.normal(size=(T, H)).astype(np.float32)
    br = rng.normal(size=(T, NB, H)).astype(np.float32)
    pw = rng.normal(0, 0.05, size=(H,)).astype(np.float32)
    nw = rng.normal(1.0, 0.05, size=(H,)).astype(np.float32)
    v = torch.cat((torch.from_numpy(br), torch.from_numpy(ps).unsqueeze(1)), dim=1).float()
    variance = v.pow(2).mean(-1, keepdim=True)
    k = v * torch.rsqrt(variance + eps)
    sw = torch.from_numpy(nw).float() * torch.from_numpy(pw).float()
    probs = (k * sw).sum(-1).softmax(-1).unsqueeze(1)
    ref = torch.matmul(probs, v).squeeze(1)
    worst["attn_res"] = float(np.abs(ref.numpy() - attn_res(ps, br, pw, nw, eps)).max())

    # KDA gate, per-channel, exactly as fla's gate.py writes it.
    B, T2, Hh, K = 1, 8, 4, K3["kda_head_dim"]
    g = rng.normal(0, 2.0, size=(B, T2, Hh, K)).astype(np.float32)
    A_log = rng.normal(0, 0.5, size=(K,)).astype(np.float32)
    dtb = rng.normal(0, 0.1, size=(Hh * K,)).astype(np.float32)
    tg = torch.from_numpy(g).float() + torch.from_numpy(dtb).float().view(Hh, K)
    a = torch.from_numpy(A_log).float().exp().view(1, K)
    ref = K3["kda_gate_lower_bound"] * torch.sigmoid(a * tg)
    worst["kda_gate_per_channel"] = float(
        np.abs(ref.numpy() - kda_gate(g, A_log, dtb, K3["kda_gate_lower_bound"])).max())

    # KDA gate, per-head + softplus branch.
    A_log_h = rng.normal(0, 0.5, size=(Hh,)).astype(np.float32)
    a_h = torch.from_numpy(A_log_h).float().exp().view(Hh, 1)
    ref = -a_h * F.softplus(tg)
    worst["kda_gate_per_head_softplus"] = float(
        np.abs(ref.numpy() - kda_gate(g, A_log_h, dtb, None)).max())

    ok = True
    for op, err in sorted(worst.items()):
        flag = "ok" if err < 1e-5 else "MISMATCH"
        if err >= 1e-5:
            ok = False
        print(f"  {op:<32} max|numpy-torch| = {err:.3e}  {flag}")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--emit", metavar="DIR", help="write fixtures + manifest")
    ap.add_argument("--verify", metavar="DIR", help="re-derive and compare hashes")
    ap.add_argument("--check-torch", action="store_true",
                    help="cross-check the numpy transcription against PyTorch")
    a = ap.parse_args()
    if not (a.emit or a.verify or a.check_torch):
        ap.print_help()
        return 1
    rc = 0
    if a.check_torch:
        rc |= check_torch()
    if a.emit:
        rc |= emit(a.emit)
    if a.verify:
        rc |= verify(a.verify)
    return rc


if __name__ == "__main__":
    sys.exit(main())
