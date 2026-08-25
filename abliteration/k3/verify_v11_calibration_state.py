#!/usr/bin/env python3
"""Configure the frozen state verifier for V11's single prompt candidate."""

import verify_v10_calibration_state as core


PROMPTS = {
    "prompt11": {
        "alias": "kimi-k3-q5attn-abl-v11-p01-cal",
        "filename": "v11-system-prompt-01-targeted-contract.txt",
        "sha256": "38f39a47f0f051d6270325963423a40dd71d3f18a93902e08e72e74dec4abd8b",
    },
}

REQUEST_PREFIX = core.REQUEST_PREFIX


def main():
    core.PROMPTS = PROMPTS
    core.main()


if __name__ == "__main__":
    main()
