#!/usr/bin/env python3
import argparse
import csv
import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.utils import to_float


FEATURES = [
    "confidence",
    "score",
    "vote_diff",
    "spread_pct",
    "abs_delta",
    "gamma",
    "decay_pct",
    "stable",
    "cooldown_ready",
    "flow_match",
    "selected",
]


def label_from_outcome(v: str) -> int:
    s = (v or "").strip().lower()
    if s == "win":
        return 1
    if s == "loss":
        return 0
    return -1


def normalize_row(row: Dict[str, str]) -> Dict[str, float]:
    confidence = to_float(row.get("confidence", "0")) / 100.0
    score = to_float(row.get("score", "0")) / 100.0
    vote_diff = min(1.0, max(0.0, to_float(row.get("vote_diff", "0")) / 5.0))
    spread_pct = to_float(row.get("spread_pct", "0"))
    spread_score = max(0.0, 1.0 - min(1.0, spread_pct / 5.0))
    abs_delta = abs(to_float(row.get("delta", "0")))
    gamma = min(1.0, max(0.0, to_float(row.get("gamma", "0")) * 1000.0))
    decay = to_float(row.get("decay_pct", "0"))
    decay_score = max(0.0, 1.0 - min(1.0, decay / 3000.0))
    stable = 1.0 if (row.get("stable", "N").strip().upper() == "Y") else 0.0
    cooldown_ready = 1.0 if to_float(row.get("cooldown_sec", "0")) <= 0 else 0.0
    flow_match = 1.0 if (row.get("flow_match", "N").strip().upper() == "Y") else 0.0
    selected = 1.0 if (row.get("selected", "N").strip().upper() == "Y") else 0.0

    return {
        "confidence": confidence,
        "score": score,
        "vote_diff": vote_diff,
        "spread_pct": spread_score,
        "abs_delta": abs_delta,
        "gamma": gamma,
        "decay_pct": decay_score,
        "stable": stable,
        "cooldown_ready": cooldown_ready,
        "flow_match": flow_match,
        "selected": selected,
    }


def sigmoid(z: float) -> float:
    if z >= 0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


def standardize(
    xs: List[Dict[str, float]]
) -> Tuple[List[Dict[str, float]], Dict[str, float], Dict[str, float]]:
    means: Dict[str, float] = {}
    stds: Dict[str, float] = {}
    n = max(1, len(xs))
    for f in FEATURES:
        vals = [x[f] for x in xs]
        m = sum(vals) / n
        means[f] = m
        var = sum((v - m) ** 2 for v in vals) / n
        stds[f] = math.sqrt(var) if var > 1e-12 else 1.0

    out: List[Dict[str, float]] = []
    for x in xs:
        out.append({f: (x[f] - means[f]) / stds[f] for f in FEATURES})
    return out, means, stds


def train_logistic(
    x_rows: List[Dict[str, float]], y: List[int], lr: float, epochs: int
) -> Tuple[Dict[str, float], float]:
    w = {f: 0.0 for f in FEATURES}
    b = 0.0
    n = len(x_rows)
    for _ in range(epochs):
        grad_w = {f: 0.0 for f in FEATURES}
        grad_b = 0.0
        for i in range(n):
            z = b + sum(w[f] * x_rows[i][f] for f in FEATURES)
            p = sigmoid(z)
            e = p - y[i]
            for f in FEATURES:
                grad_w[f] += e * x_rows[i][f]
            grad_b += e
        for f in FEATURES:
            w[f] -= lr * (grad_w[f] / n)
        b -= lr * (grad_b / n)
    return w, b


def evaluate(x_rows: List[Dict[str, float]], y: List[int], w: Dict[str, float], b: float) -> float:
    if not x_rows:
        return 0.0
    correct = 0
    for i in range(len(x_rows)):
        z = b + sum(w[f] * x_rows[i][f] for f in FEATURES)
        p = sigmoid(z)
        pred = 1 if p >= 0.5 else 0
        if pred == y[i]:
            correct += 1
    return correct / len(x_rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Train adaptive model from decision journal outcomes.")
    parser.add_argument("--journal-csv", default="decision_journal.csv")
    parser.add_argument("--model-file", default=".adaptive_model.json")
    parser.add_argument("--min-labels", type=int, default=20)
    parser.add_argument("--lr", type=float, default=0.15)
    parser.add_argument("--epochs", type=int, default=600)
    args = parser.parse_args()

    rows: List[Dict[str, float]] = []
    labels: List[int] = []

    if not os.path.exists(args.journal_csv):
        print(f"Journal not found: {args.journal_csv} (new day, skipping)")
        return 0

    with open(args.journal_csv, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            y = label_from_outcome(r.get("outcome", ""))
            if y < 0:
                continue
            rows.append(normalize_row(r))
            labels.append(y)

    if len(labels) < args.min_labels:
        print(
            f"Not enough labeled rows: {len(labels)} (need >= {args.min_labels}). "
            "Fill 'outcome' column with Win/Loss first."
        )
        return 0

    x_std, means, stds = standardize(rows)
    weights, bias = train_logistic(x_std, labels, args.lr, args.epochs)
    acc = evaluate(x_std, labels, weights, bias)

    payload = {
        "version": 1,
        "trained_at": datetime.utcnow().isoformat() + "Z",
        "sample_count": len(labels),
        "feature_order": FEATURES,
        "means": means,
        "stds": stds,
        "weights": weights,
        "bias": bias,
        "train_accuracy": acc,
    }

    with open(args.model_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print(f"Model saved: {args.model_file}")
    print(f"Labeled samples: {len(labels)}")
    print(f"Train accuracy: {acc:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
