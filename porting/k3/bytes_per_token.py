#!/usr/bin/env python3
"""Exact bytes read per generated token, straight from the GGUF tensor table.

Token generation on this box is memory-bound, so "how fast should this model
be?" is really "how many bytes does one token touch, and what does the box do
per second?". The first half is exactly computable and usually estimated
instead - which is how a model gets called slow when it is doing fine, or
called fine when it is slow.

A routed expert tensor is only read for the experts a token selects, so it
counts for n_expert_used/n_expert of its size. Everything else - attention,
norms, shared experts, embeddings - is read in full every token.

Dependency-free (chuckdancer has no numpy, and gguf-py imports it to read a
header). Pass all shards; the tensor table lives in each.

    bytes_per_token.py /models/Kimi-K3-UD-Q2_K_XL/*.gguf --used 16 --experts 896
"""
import struct, sys

U8, I8, U16, I16, U32, I32, F32, BOOL, STR, ARR, U64, I64, F64 = range(13)
_FMT = {U8: "<B", I8: "<b", U16: "<H", I16: "<h", U32: "<I", I32: "<i",
        F32: "<f", BOOL: "<?", U64: "<Q", I64: "<q", F64: "<d"}
_SZ = {U8: 1, I8: 1, U16: 2, I16: 2, U32: 4, I32: 4, F32: 4, BOOL: 1,
       U64: 8, I64: 8, F64: 8}

# (type_size, block_size) per ggml type id, i.e. bytes per block of N weights.
GGML_TYPES = {
    0: (4, 1, "F32"),      1: (2, 1, "F16"),      2: (18, 32, "Q4_0"),
    3: (20, 32, "Q4_1"),   6: (22, 32, "Q5_0"),   7: (24, 32, "Q5_1"),
    8: (34, 32, "Q8_0"),   9: (36, 32, "Q8_1"),  10: (84, 256, "Q2_K"),
    11: (110, 256, "Q3_K"), 12: (144, 256, "Q4_K"), 13: (176, 256, "Q5_K"),
    14: (210, 256, "Q6_K"), 15: (292, 256, "Q8_K"), 16: (66, 256, "IQ2_XXS"),
    17: (74, 256, "IQ2_XS"), 18: (98, 256, "IQ3_XXS"), 19: (50, 256, "IQ1_S"),
    20: (18, 32, "IQ4_NL"), 21: (110, 256, "IQ3_S"), 22: (82, 256, "IQ2_S"),
    23: (136, 256, "IQ4_XS"), 24: (1, 1, "I8"), 25: (2, 1, "I16"),
    26: (4, 1, "I32"), 27: (8, 1, "I64"), 28: (8, 1, "F64"),
    29: (56, 256, "IQ1_M"), 30: (2, 1, "BF16"),
}


class R:
    def __init__(self, f):
        self.f = f

    def raw(self, n):
        b = self.f.read(n)
        if len(b) != n:
            raise EOFError
        return b

    def scalar(self, t):
        return struct.unpack(_FMT[t], self.raw(_SZ[t]))[0]

    def string(self):
        return self.raw(self.scalar(U64)).decode("utf-8", "replace")

    def value(self, t):
        if t == STR:
            return self.string()
        if t == ARR:
            et = self.scalar(U32)
            n = self.scalar(U64)
            return [self.value(et) for _ in range(n)]
        return self.scalar(t)


def tensors(path):
    """Yield (name, ggml_type, nbytes) for every tensor in one shard.

    Size comes from the DIFFERENCE BETWEEN DATA OFFSETS, not from a type table.
    ik_llama.cpp ships quant types mainline does not (IQ*_K, MXFP4, ...), and a
    hardcoded table silently scores those zero - which is exactly wrong for the
    expert tensors that dominate these models. Offsets are in the file and are
    correct by construction. The cost is counting inter-tensor alignment padding,
    which is 32 bytes per tensor.
    """
    import os
    with open(path, "rb") as f:
        r = R(f)
        if r.raw(4) != b"GGUF":
            raise ValueError(f"{path}: not a GGUF file")
        r.scalar(U32)                      # version
        n_tensors = r.scalar(U64)
        n_kv = r.scalar(U64)
        alignment = 32
        for _ in range(n_kv):
            key = r.string()
            val = r.value(r.scalar(U32))
            if key == "general.alignment":
                alignment = val
        infos = []
        for _ in range(n_tensors):
            name = r.string()
            dims = [r.scalar(U64) for _ in range(r.scalar(U32))]
            tt = r.scalar(U32)
            infos.append((name, dims, tt, r.scalar(U64)))
        pos = f.tell()
        data_start = (pos + alignment - 1) // alignment * alignment
        data_len = os.path.getsize(path) - data_start

    infos.sort(key=lambda t: t[3])
    for i, (name, dims, tt, off) in enumerate(infos):
        end = infos[i + 1][3] if i + 1 < len(infos) else data_len
        yield name, tt, end - off


def main():
    def opt(flag, default):
        return int(sys.argv[sys.argv.index(flag) + 1]) if flag in sys.argv else default
    n_used, n_exp = opt("--used", 8), opt("--experts", 0)

    # Drop each flag AND the value after it, or the values land in the file list.
    args, skip = [], False
    for a in sys.argv[1:]:
        if skip:
            skip = False
        elif a.startswith("--"):
            skip = True
        else:
            args.append(a)

    routed = always = 0
    by_type = {}
    always_by_type = {}
    for path in args:
        for name, tt, nbytes in tensors(path):
            tname = GGML_TYPES.get(tt, (0, 1, f"type{tt}"))[2]
            # Routed experts are stored as one tensor per layer with the expert
            # index as the slowest dimension; llama.cpp names them *_exps.
            # *_shexp is a SHARED expert - always read - so test _exps first.
            if "_exps" in name:
                routed += nbytes
            else:
                always += nbytes
            e = by_type.setdefault(tname, [0, 0])
            e[0] += 1
            e[1] += nbytes
            if "_exps" not in name:
                a = always_by_type.setdefault(tname, [0, 0])
                a[0] += 1
                a[1] += nbytes

    total = routed + always
    frac = n_used / n_exp if n_exp else 1.0
    per_tok = always + routed * frac
    G = 1 << 30

    print(f"total          {total/G:10.2f} GiB")
    print(f"  routed exps  {routed/G:10.2f} GiB  ({100*routed/total:.1f}%)")
    print(f"  everything   {always/G:10.2f} GiB  ({100*always/total:.1f}%)")
    print(f"\nper token, at {n_used}/{n_exp} experts active:")
    print(f"  routed       {routed*frac/G:10.2f} GiB")
    print(f"  always-read  {always/G:10.2f} GiB")
    print(f"  TOTAL        {per_tok/G:10.2f} GiB per token")
    print("\nby quant type:")
    for t, (cnt, b) in sorted(by_type.items(), key=lambda kv: -kv[1][1]):
        print(f"  {t:<9} {cnt:5d} tensors {b/G:9.2f} GiB")
    # The actionable view: always-read bytes are paid on EVERY token, so this is
    # the list of what to requantize, ordered by what it would buy.
    print("\nalways-read by quant type (this is the requant target list):")
    for t, (cnt, b) in sorted(always_by_type.items(), key=lambda kv: -kv[1][1]):
        print(f"  {t:<9} {cnt:5d} tensors {b/G:9.2f} GiB   {100*b/per_tok:5.1f}% of a token")

    print("\nimplied bandwidth at N tok/s:")
    for tps in (2, 3, 3.67, 5, 10):
        print(f"  {tps:5.2f} tok/s -> {per_tok*tps/1e9:7.1f} GB/s")


if __name__ == "__main__":
    main()
