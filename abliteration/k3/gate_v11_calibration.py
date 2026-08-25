#!/usr/bin/env python3
"""Configure the frozen three-phase gate for V11's single prompt candidate."""

import gate_v10_calibration as core


PROMPT_ORDER = ("prompt11",)
PROMPTS = {
    "prompt11": {
        "alias": "kimi-k3-q5attn-abl-v11-p01-cal",
        "filename": "v11-system-prompt-01-targeted-contract.txt",
        "sha256": "38f39a47f0f051d6270325963423a40dd71d3f18a93902e08e72e74dec4abd8b",
    },
}
EVALUATOR_SHA256 = "1f6b43a330cfbddd8334170c067e6719607f12d9fdeff6d1718d4f8ab733e61a"
PROVENANCE_HELPER_SHA256 = (
    "63836bc34d7096a062e90cee855d320944fac71ab55769700f6dfa3fab8e4616"
)
STATE_HELPER_SHA256 = (
    "652004720ed37c1b8bf1b5d90f823c449b6d7500711e655b8737e4491fb59935"
)
PHASE_SCHEMA = "k3-v11-calibration-phase-v1"
SELECTION_SCHEMA = "k3-v11-calibration-selection-v1"
DATASETS = core.DATASETS
PHASE_ORDER = core.PHASE_ORDER


def main():
    core.PROMPT_ORDER = PROMPT_ORDER
    core.PROMPTS = PROMPTS
    core.EVALUATOR_SHA256 = EVALUATOR_SHA256
    core.PROVENANCE_HELPER_SHA256 = PROVENANCE_HELPER_SHA256
    core.STATE_HELPER_SHA256 = STATE_HELPER_SHA256
    core.PHASE_SCHEMA = PHASE_SCHEMA
    core.SELECTION_SCHEMA = SELECTION_SCHEMA
    core.main()


if __name__ == "__main__":
    main()
