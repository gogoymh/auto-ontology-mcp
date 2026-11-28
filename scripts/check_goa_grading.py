#!/usr/bin/env python3
"""Check the git-ontology-agent grading notes sit under one top-level '채점' document.

Usage: check_goa_grading.py {nested|backup|intact|index}
Runs check_grading_folder.py --project git-ontology-agent; the project's values live in grading_projects.json.
PQ_HUB points at another hub folder.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_grading_folder import main  # noqa: E402

if __name__ == "__main__":
    main(["--project", "git-ontology-agent", *sys.argv[1:]])
