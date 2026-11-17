#!/bin/bash
# Shortcut: all six cards (symmetric 3:3 NVLink triangles, batch 6/3).  Takes the dev
card pair away from cards 4/5, so the occupancy guard has to pass.
exec bash "$(cd "$(dirname "$0")" && pwd)/lane.sh" 6 "$@"
