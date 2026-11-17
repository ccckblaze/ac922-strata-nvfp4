#!/bin/bash
# Shortcut: production lane on GPU 0-3 (NUMA 3:1 island split, batch 4/2).
exec bash "$(cd "$(dirname "$0")" && pwd)/lane.sh" 4 "$@"
