#!/usr/bin/env python3
"""Configure the frozen state verifier for V20 canonical DRY."""

import verify_v10_calibration_state as core


PROMPTS = {
    "prompt20": {
        "alias": "kimi-k3-q5attn-abl-v20-dry-ttf-cal",
        "filename": "v10-system-prompt-02-semantic-contract.txt",
        "sha256": "44fc73623eb35a4b19b9cbfdf682a015af832ed8954233c68b8cf5845ab116f9",
    },
}

REQUEST_PREFIX = core.REQUEST_PREFIX


def main():
    core.PROMPTS = PROMPTS
    core.main()


if __name__ == "__main__":
    main()
