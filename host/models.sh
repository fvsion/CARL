#!/usr/bin/env bash
# Models: the built-in catalogue (host/catalog.json), every .gguf in the models
# folder, and custom Hugging Face downloads. A thin wrapper around tools/carl.py.
#
#   host/models.sh list                       # catalogue + models folder + custom, with status
#   host/models.sh download NAME...|all       # a catalogue model (aria2c 16x, resumable, sha256 verified)
#   host/models.sh download default           # the default model for this Mac (see: serve.sh fit)
#   host/models.sh download hf:OWNER/REPO/FILE.gguf   # any GGUF from Hugging Face (verified too)
#   host/models.sh download hf:OWNER/REPO     # list that repo's GGUF files
#   host/models.sh verify NAME... | delete NAME | path NAME | get NAME FIELD | default | downloaded
#
# Also reachable as: ./carl.sh models | download | verify
set -euo pipefail
exec python3 "$(dirname "$0")/../tools/carl.py" "${@:-list}"
