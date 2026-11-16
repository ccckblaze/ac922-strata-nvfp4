#!/bin/bash
# Extra points for the adopted boot, which the shape panel deliberately does not carry:
#  - more streams than cards (the aggregate ceiling at short context; slots, not cards, decide it)
#  - a deep single stream measured with a long generation, because the published deep figures
#    were 64-token calibers - they report the drafting ramp, not steady decode
#  - the elasticity check: short burst right after a deep one (does the deep conversation pay rent?)
set -u
N=/data/work/note312
source /etc/profile.d/env.sh
TAG=$1
P=/usr/bin/python3.11
A=$N/aggbench.py
J=$N/shapes.jsonl
AB_TIMEOUT=${AB_TIMEOUT:-900} $P $A --tag "$TAG" --depth 8000 --n 8 --gen 256 >> $J 2>> $N/shapes.err
AB_TIMEOUT=${AB_TIMEOUT:-900} $P $A --tag "$TAG" --depth 8000 --n 12 --gen 256 >> $J 2>> $N/shapes.err
AB_TIMEOUT=${AB_TIMEOUT:-900} $P $A --tag "$TAG" --depth 50000 --n 8 --gen 256 >> $J 2>> $N/shapes.err
AB_TIMEOUT=${AB_TIMEOUT:-1200} $P $A --tag "$TAG" --depth 131000 --n 1 --gen 512 >> $J 2>> $N/shapes.err
AB_TIMEOUT=${AB_TIMEOUT:-1800} $P $A --tag "$TAG" --depth 245000 --n 1 --gen 384 >> $J 2>> $N/shapes.err
AB_TIMEOUT=${AB_TIMEOUT:-900} $P $A --tag "$TAG" --depth 8000 --n 6 --gen 256 >> $J 2>> $N/shapes.err
echo "panel=$TAG done $(date +%H:%M:%S)" >> $N/panel.log
