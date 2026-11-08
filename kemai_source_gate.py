#!/usr/bin/env python3
"""Compatibility entry for the shared source gate; implementation is in kemai_pipeline."""
from kemai_pipeline.source_gate import *  # noqa: F401,F403
from kemai_pipeline.source_gate import main


if __name__ == "__main__":
    main()
