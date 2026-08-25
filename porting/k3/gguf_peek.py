#!/usr/bin/env python3
"""Minimal dependency-free GGUF reader - KV metadata and tensor names only.

chuckdancer has no numpy, and gguf-py hard-imports it just to read a header.
The format is simple enough that parsing it directly is less work than fixing
the environment, and it keeps this diagnostic runnable anywhere.

    gguf_peek.py <file.gguf> [--tensors] [--filter SUBSTR] [--full]
"""
import struct, sys

U8, I8, U16, I16, U32, I32, F32, BOOL, STR, ARR, U64, I64, F64 = range(13)
_FMT = {U8: "<B", I8: "<b", U16: "<H", I16: "<h", U32: "<I", I32: "<i",
        F32: "<f", BOOL: "<?", U64: "<Q", I64: "<q", F64: "<d"}
_SZ = {U8: 1, I8: 1, U16: 2, I16: 2, U32: 4, I32: 4, F32: 4, BOOL: 1,
       U64: 8, I64: 8, F64: 8}


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
            # Long arrays are token vocabs; summarise rather than print 129k items.
            if n > 12:
                if et == STR:
                    head = [self.string() for _ in range(6)]
                    for _ in range(n - 6):
                        self.string()
                else:
                    head = [self.scalar(et) for _ in range(6)]
                    self.raw(_SZ[et] * (n - 6))
                return f"[{n} items] {head[:6]} ..."
            return [self.value(et) for _ in range(n)]
        return self.scalar(t)


def main():
    path = sys.argv[1]
    want_tensors = "--tensors" in sys.argv
    full_values = "--full" in sys.argv
    filt = None
    if "--filter" in sys.argv:
        filt = sys.argv[sys.argv.index("--filter") + 1]

    with open(path, "rb") as fh:
        r = R(fh)
        magic = r.raw(4)
        if magic != b"GGUF":
            print(f"not a GGUF file: {magic!r}")
            return 1
        ver = r.scalar(U32)
        n_tensors = r.scalar(U64)
        n_kv = r.scalar(U64)
        print(f"# {path.split('/')[-1]}  GGUF v{ver}  tensors={n_tensors}  kv={n_kv}\n")

        kv = {}
        for _ in range(n_kv):
            key = r.string()
            kv[key] = r.value(r.scalar(U32))

        for k, v in kv.items():
            if filt and filt not in k:
                continue
            if not filt and k.startswith("tokenizer.ggml.") and "model" not in k:
                continue
            value = str(v) if full_values else str(v)[:78]
            print(f"  {k:<56} = {value}")

        if want_tensors:
            print(f"\n# {n_tensors} tensors")
            names = []
            for _ in range(n_tensors):
                name = r.string()
                nd = r.scalar(U32)
                dims = [r.scalar(U64) for _ in range(nd)]
                r.scalar(U32)   # ggml type
                r.scalar(U64)   # offset
                names.append((name, dims))
            for name, dims in names:
                if filt and filt not in name:
                    continue
                print(f"  {name:<48} {dims}")
    return 0


sys.exit(main())
