#!/bin/bash
# The standard measurement panel for one boot: decode-first calibers at four depths, plus the
# single-stream reference at the two cheap depths (the ratio agg/(n x tg1) is the scaling number),
# and one full-window concurrency point. Append-only JSONL; every row carries its tag.
set -u
N=/data/work/note312
source /etc/profile.d/env.sh
TAG=$1
MODE=${2:-full}
J=$N/shapes.jsonl
P=/usr/bin/python3.11
A=$N/aggbench.py
for d in 8000 50000 98000; do
  $P $A --tag "$TAG" --depth $d --n 6 --gen 256 >> $J 2>> $N/shapes.err
done
for d in 8000 50000; do
  $P $A --tag "$TAG" --depth $d --n 1 --gen 256 >> $J 2>> $N/shapes.err
done
$P $A --tag "$TAG" --depth 131000 --n 6 --gen 192 >> $J 2>> $N/shapes.err
# The full-window point costs a cold prefill of 4 x 262K per arm (~4 min) and is prefill-bound,
# so it says little about a decode shape. Full mode only, and always for the adopted arm.
if [ "$MODE" = "full" ]; then
  $P $A --tag "$TAG" --depth 262000 --n 4 --gen 128 >> $J 2>> $N/shapes.err
fi
echo "panel=$TAG done $(date +%H:%M:%S)" >> $N/panel.log
