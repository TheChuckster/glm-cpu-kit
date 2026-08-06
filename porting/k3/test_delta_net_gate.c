// Self-validating test of ggml_delta_net's per-channel forget gate.
//
// No oracle needed. A per-channel gate whose channels all hold the SAME value is
// mathematically identical to a per-head gate holding that value, so the two
// paths must agree bit-for-bit-ish. If they diverge, the per-channel code added
// for Kimi-K3 is wrong - in the kernel's indexing, in build_fused_delta_net's
// permute, or in contiguity.
//
// This exercises the real C kernel, which hand-tracing and the numpy
// composition checks do not. Runs in milliseconds; no model load.
//
//   gcc -O2 -I ggml/include -I ggml/src test_delta_net_gate.c \
//       -L build-k3/ggml/src -lggml -lm -o /tmp/test_dn && \
//   LD_LIBRARY_PATH=build-k3/ggml/src /tmp/test_dn
#include "ggml.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define S 8      // head dim  (S_k == S_v)
#define H 3      // heads
#define T 5      // tokens
#define NS 1     // sequences

static float frand(unsigned * st) {
    *st = *st * 1103515245u + 12345u;
    return (float)((*st >> 9) & 0xFFFF) / 32768.0f - 1.0f;
}

// Run delta_net once. gate_per_channel selects which gate layout to build.
// Returns a malloc'd copy of the output region.
static float * run(int per_channel, float gval, const float * q, const float * k,
                   const float * v, const float * beta, const float * state, int * out_n) {
    struct ggml_init_params ip = { 256u*1024*1024, NULL, false };
    struct ggml_context * ctx = ggml_init(ip);

    struct ggml_tensor * tq = ggml_new_tensor_4d(ctx, GGML_TYPE_F32, S, T, H, NS);
    struct ggml_tensor * tk = ggml_new_tensor_4d(ctx, GGML_TYPE_F32, S, T, H, NS);
    struct ggml_tensor * tv = ggml_new_tensor_4d(ctx, GGML_TYPE_F32, S, T, H, NS);
    struct ggml_tensor * tb = ggml_new_tensor_4d(ctx, GGML_TYPE_F32, 1, T, H, NS);
    struct ggml_tensor * ts = ggml_new_tensor_4d(ctx, GGML_TYPE_F32, S, S*H, 1, NS);

    memcpy(tq->data, q, ggml_nbytes(tq));
    memcpy(tk->data, k, ggml_nbytes(tk));
    memcpy(tv->data, v, ggml_nbytes(tv));
    memcpy(tb->data, beta, ggml_nbytes(tb));
    memcpy(ts->data, state, ggml_nbytes(ts));

    // g is [n_tokens, width, H, NS]; width 1 = per-head, S = per-channel.
    const int W = per_channel ? S : 1;
    struct ggml_tensor * tg = ggml_new_tensor_4d(ctx, GGML_TYPE_F32, T, W, H, NS);
    float * gd = (float *) tg->data;
    for (int i = 0; i < T*W*H*NS; ++i) gd[i] = gval;

    struct ggml_tensor * r = ggml_delta_net(ctx, tq, tk, tv, tg, tb, ts, NULL);
    struct ggml_cgraph * gf = ggml_new_graph(ctx);
    ggml_build_forward_expand(gf, r);
    ggml_graph_compute_with_ctx(ctx, gf, 1);

    const int n = S*H*T*NS;   // the output region, before the trailing state
    float * out = (float *) malloc(n * sizeof(float));
    memcpy(out, r->data, n * sizeof(float));
    *out_n = n;
    ggml_free(ctx);
    return out;
}

int main(void) {
    unsigned st = 12345;
    static float q[S*T*H*NS], k[S*T*H*NS], v[S*T*H*NS], beta[T*H*NS], state[S*S*H*NS];
    for (int i = 0; i < S*T*H*NS; ++i) { q[i] = frand(&st); k[i] = frand(&st); v[i] = frand(&st); }
    for (int i = 0; i < T*H*NS; ++i)   beta[i]  = frand(&st);
    for (int i = 0; i < S*S*H*NS; ++i) state[i] = frand(&st) * 0.1f;

    int rc = 0;
    const float gvals[] = { -0.25f, -1.0f, -3.0f };

    for (int t = 0; t < 3; ++t) {
        const float gv = gvals[t];
        int n1 = 0, n2 = 0;
        float * a = run(0, gv, q, k, v, beta, state, &n1);   // per-head
        float * b = run(1, gv, q, k, v, beta, state, &n2);   // per-channel, all equal

        double worst = 0.0;
        for (int i = 0; i < n1; ++i) {
            double d = fabs((double)a[i] - (double)b[i]);
            if (d > worst) worst = d;
        }
        const int ok = worst < 1e-5;
        printf("  g = %-6.2f  max|per_head - per_channel| = %.3e   %s\n",
               gv, worst, ok ? "ok" : "MISMATCH");
        if (!ok) rc = 1;
        free(a); free(b);
    }

    printf("\n%s\n", rc ? "FAILED: the per-channel gate path does not reduce to the per-head one"
                        : "per-channel gate reduces correctly to the per-head case");
    return rc;
}
