#!/usr/bin/env python3
"""Evaluate a temporary held-out classifier and save metrics and charts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from sklearn.metrics import accuracy_score, confusion_matrix, precision_recall_fscore_support
from sklearn.model_selection import train_test_split

from error_classifier import (
    ErrorClassifier,
    _build_pipeline,
    _load_training_examples,
    _normalize_category,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRAINING_DATA = PROJECT_ROOT / "data" / "training_data.json"
REPORT_DIR = PROJECT_ROOT / "reports"
REPORT_FILE = REPORT_DIR / "evaluation.json"
RANDOM_STATE = 42
TEST_SIZE = 0.2
RULE_ONLY_EVALUATION_CLASSES = {"unused_variable"}


def _weakness_reason(
    category: str,
    f1: float,
    support: int,
    total_examples: int,
    matrix: np.ndarray,
    labels: list[str],
    class_index: int,
) -> str:
    if total_examples == 0:
        reason = (
            "This category has no labeled training or test examples; its hard-rule "
            "output is mapped to other, so it remains only as a zero-support rule-only metric."
        )
        return reason
    elif support <= 10 or total_examples <= 50:
        reason = (
            f"Limited labeled support ({total_examples} total examples; "
            f"{support} in the test set) makes this class difficult to learn."
        )
    else:
        reason = "The diagnostic wording overlaps with other categories."

    confusions = matrix[class_index].copy()
    confusions[class_index] = 0
    if confusions.sum():
        confused_index = int(confusions.argmax())
        reason += f" It is most often predicted as {labels[confused_index]}."
    elif f1 == 0:
        reason += " The model did not correctly identify any test examples."
    else:
        reason += " The remaining errors are spread across other categories."
    return reason


def _save_confusion_chart(
    labels: list[str], counts: np.ndarray, normalized: np.ndarray, path: Path
) -> None:
    size = max(8, len(labels) * 1.05)
    figure, axis = plt.subplots(figsize=(size, size * 0.82))
    image = axis.imshow(normalized, cmap="Blues", vmin=0, vmax=1)
    figure.colorbar(image, ax=axis, label="Row-normalized proportion")
    axis.set(
        xticks=np.arange(len(labels)),
        yticks=np.arange(len(labels)),
        xticklabels=labels,
        yticklabels=labels,
        xlabel="Predicted category",
        ylabel="True category",
        title="Classifier Confusion Matrix",
    )
    plt.setp(axis.get_xticklabels(), rotation=35, ha="right", rotation_mode="anchor")
    for row in range(len(labels)):
        for column in range(len(labels)):
            color = "white" if normalized[row, column] >= 0.55 else "#172033"
            axis.text(
                column,
                row,
                f"{counts[row, column]}\n({normalized[row, column]:.1%})",
                ha="center",
                va="center",
                color=color,
                fontsize=8,
            )
    figure.tight_layout()
    figure.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(figure)


def _save_f1_chart(per_class: dict[str, dict[str, Any]], path: Path) -> None:
    labels = list(per_class)
    values = [per_class[label]["f1"] for label in labels]
    figure, axis = plt.subplots(figsize=(9, max(4, len(labels) * 0.55)))
    bars = axis.barh(labels, values, color="#6366F1")
    axis.set_xlim(0, 1)
    axis.set_xlabel("F1 score")
    axis.set_title("Per-class F1")
    axis.grid(axis="x", alpha=0.2)
    axis.set_axisbelow(True)
    for bar, value in zip(bars, values):
        axis.text(
            min(value + 0.015, 0.94),
            bar.get_y() + bar.get_height() / 2,
            f"{value:.3f}",
            va="center",
            fontsize=9,
        )
    figure.tight_layout()
    figure.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(figure)


def run_evaluation() -> dict[str, Any]:
    if not TRAINING_DATA.is_file():
        raise FileNotFoundError(f"Training data not found: {TRAINING_DATA}")

    loaded_features, loaded_labels = _load_training_examples(str(TRAINING_DATA))
    with TRAINING_DATA.open("r", encoding="utf-8") as data_file:
        raw_rows = json.load(data_file)
    raw_messages = [
        row.get("message") or row.get("raw_message")
        for row in raw_rows
        if isinstance(row, dict)
        and (row.get("message") or row.get("raw_message"))
        and row.get("category")
    ] if isinstance(raw_rows, list) else []
    if len(raw_messages) != len(loaded_features):
        raise ValueError("Training rows could not be aligned with classifier loading.")

    unique_rows: dict[tuple[str, str], str] = {}
    for feature, label, message in zip(loaded_features, loaded_labels, raw_messages):
        unique_rows.setdefault((feature, label), message)
    rows = [(feature, label, message) for (feature, label), message in unique_rows.items()]
    if len(rows) < 2:
        raise ValueError("Not enough unique training examples to evaluate.")

    features = [feature for feature, _, _ in rows]
    labels_raw = [label for _, label, _ in rows]
    messages = [message for _, _, message in rows]
    labels = sorted(set(labels_raw))
    class_counts = {label: labels_raw.count(label) for label in labels}
    if min(class_counts.values()) < 2:
        raise ValueError(
            "A stratified split requires at least two unique examples in every class."
        )

    X_train, X_test, y_train, y_test, _messages_train, messages_test = train_test_split(
        features,
        labels_raw,
        messages,
        test_size=TEST_SIZE,
        random_state=RANDOM_STATE,
        stratify=labels_raw,
    )

    pipeline = _build_pipeline()
    pipeline.fit(X_train, y_train)
    ml_predictions = [str(prediction) for prediction in pipeline.predict(X_test)]

    rules = ErrorClassifier._hard_rule_prediction
    combined_predictions: list[str] = []
    rule_mask: list[bool] = []
    for message, ml_prediction in zip(messages_test, ml_predictions):
        rule_prediction = rules(message)
        if rule_prediction is None:
            combined_predictions.append(ml_prediction)
            rule_mask.append(False)
        else:
            combined_predictions.append(_normalize_category(rule_prediction[0]))
            rule_mask.append(True)

    metric_labels = sorted(
        set(y_test) | set(combined_predictions) | RULE_ONLY_EVALUATION_CLASSES
    )
    matrix = confusion_matrix(y_test, combined_predictions, labels=metric_labels)
    row_totals = matrix.sum(axis=1, keepdims=True)
    normalized = np.divide(
        matrix,
        row_totals,
        out=np.zeros_like(matrix, dtype=float),
        where=row_totals != 0,
    )
    precision, recall, f1, support = precision_recall_fscore_support(
        y_test,
        combined_predictions,
        labels=metric_labels,
        zero_division=0,
    )
    per_class = {
        label: {
            "precision": float(precision[index]),
            "recall": float(recall[index]),
            "f1": float(f1[index]),
            "support": int(support[index]),
        }
        for index, label in enumerate(metric_labels)
    }
    macro_f1_trained_classes = float(
        precision_recall_fscore_support(
            y_test,
            combined_predictions,
            labels=labels,
            average="macro",
            zero_division=0,
        )[2]
    )
    macro_f1_including_rule_only_classes = float(
        precision_recall_fscore_support(
            y_test,
            combined_predictions,
            labels=metric_labels,
            average="macro",
            zero_division=0,
        )[2]
    )

    rule_true = [truth for truth, handled in zip(y_test, rule_mask) if handled]
    rule_predicted = [
        prediction for prediction, handled in zip(combined_predictions, rule_mask) if handled
    ]
    ml_true = [truth for truth, handled in zip(y_test, rule_mask) if not handled]
    ml_only_predictions = [
        prediction for prediction, handled in zip(ml_predictions, rule_mask) if not handled
    ]
    weakest = sorted(
        per_class,
        key=lambda label: (per_class[label]["f1"], per_class[label]["support"], label),
    )[:3]
    weakest_classes = [
        {
            "category": category,
            **per_class[category],
            "total_examples": class_counts.get(category, 0),
            "likely_reason": _weakness_reason(
                category,
                per_class[category]["f1"],
                per_class[category]["support"],
                class_counts.get(category, 0),
                matrix,
                metric_labels,
                metric_labels.index(category),
            ),
        }
        for category in weakest
    ]

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    confusion_chart = REPORT_DIR / "confusion_matrix.png"
    f1_chart = REPORT_DIR / "per_class_f1.png"
    _save_confusion_chart(metric_labels, matrix, normalized, confusion_chart)
    _save_f1_chart(per_class, f1_chart)

    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": {
            "algorithm": "TF-IDF word (1-3 grams) + character (3-5 grams) -> "
            "Logistic Regression (class_weight='balanced')",
            "pipeline": "Same temporary pipeline settings as ErrorClassifier.train()",
            "saved_model_used": False,
        },
        "dataset": {
            "path": str(TRAINING_DATA.relative_to(PROJECT_ROOT)),
            "size_before_dedup": len(loaded_features),
            "size_after_dedup": len(rows),
            "duplicates_removed": len(loaded_features) - len(rows),
            "train_size": len(X_train),
            "test_size": len(X_test),
            "test_fraction": TEST_SIZE,
            "random_state": RANDOM_STATE,
            "class_counts_after_dedup": class_counts,
        },
        "metrics": {
            "accuracy": float(accuracy_score(y_test, combined_predictions)),
            "macro_f1_scope": "All trained classes and rule-only classes (including zero-support unused_variable)",
            "macro_f1_trained_classes": macro_f1_trained_classes,
            "macro_f1_trained_class_count": len(labels),
            "macro_f1_including_rule_only_classes": macro_f1_including_rule_only_classes,
            "macro_f1": macro_f1_including_rule_only_classes,
            "weighted_f1": float(
                precision_recall_fscore_support(
                    y_test, combined_predictions, labels=metric_labels, average="weighted", zero_division=0
                )[2]
            ),
        },
        "per_class": per_class,
        "confusion_matrix": {
            "labels": metric_labels,
            "counts": matrix.astype(int).tolist(),
            "row_normalized": normalized.tolist(),
        },
        "hard_rule_evaluation": {
            "rule_handled_rows": len(rule_true),
            "rule_handled_accuracy": (
                float(accuracy_score(rule_true, rule_predicted)) if rule_true else None
            ),
            "ml_handled_rows": len(ml_true),
            "ml_handled_accuracy": (
                float(accuracy_score(ml_true, ml_only_predictions)) if ml_true else None
            ),
        },
        "evaluation_notes": {
            "unused_variable": (
                "unused_variable has no labeled examples; its hard-rule output is mapped to other."
            ),
            "label_provenance": (
                "Labels come from regex rules in src/prepare_training_data.py applied to real GCC "
                "messages from the criyle/codenet-compile-errors dataset; scores measure agreement "
                "with those rule-based labels."
            ),
        },
        "weakest_classes": weakest_classes,
        "artifacts": {
            "confusion_matrix_png": "reports/confusion_matrix.png",
            "per_class_f1_png": "reports/per_class_f1.png",
        },
    }
    REPORT_FILE.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _print_report(report: dict[str, Any]) -> None:
    dataset = report["dataset"]
    metrics = report["metrics"]
    hard_rules = report["hard_rule_evaluation"]
    print(
        "Dataset: "
        f"{dataset['size_before_dedup']} rows before dedup, "
        f"{dataset['size_after_dedup']} after ({dataset['duplicates_removed']} removed); "
        f"train={dataset['train_size']}, test={dataset['test_size']}"
    )
    print(
        f"Accuracy: {metrics['accuracy']:.4f} | "
        f"macro-F1 ({metrics['macro_f1_trained_class_count']} trained classes): "
        f"{metrics['macro_f1_trained_classes']:.4f} | "
        f"macro-F1 including rule-only classes: "
        f"{metrics['macro_f1_including_rule_only_classes']:.4f} | "
        f"Weighted-F1: {metrics['weighted_f1']:.4f}"
    )
    print(
        "Hard rules: "
        f"{hard_rules['rule_handled_rows']} rows, accuracy="
        f"{hard_rules['rule_handled_accuracy']}; ML: "
        f"{hard_rules['ml_handled_rows']} rows, accuracy="
        f"{hard_rules['ml_handled_accuracy']}"
    )
    matrix = report["confusion_matrix"]
    print("\nConfusion matrix counts (rows=true, columns=predicted):")
    print("true\\predicted\t" + "\t".join(matrix["labels"]))
    for label, row in zip(matrix["labels"], matrix["counts"]):
        print(f"{label}\t" + "\t".join(str(value) for value in row))
    print("\nConfusion matrix row-normalized proportions:")
    print("true\\predicted\t" + "\t".join(matrix["labels"]))
    for label, row in zip(matrix["labels"], matrix["row_normalized"]):
        print(f"{label}\t" + "\t".join(f"{value:.4f}" for value in row))
    print("\nPer-class metrics:")
    print("Category\tPrecision\tRecall\tF1\tSupport")
    for category, values in report["per_class"].items():
        print(
            f"{category}\t{values['precision']:.4f}\t{values['recall']:.4f}\t"
            f"{values['f1']:.4f}\t{values['support']}"
        )
    print("\nThree weakest classes:")
    for item in report["weakest_classes"]:
        print(
            f"- {item['category']}: F1={item['f1']:.4f}, "
            f"support={item['support']}; {item['likely_reason']}"
        )
    print(f"\nSaved report: {REPORT_FILE}")
    print(f"Saved PNG: {PROJECT_ROOT / report['artifacts']['confusion_matrix_png']}")
    print(f"Saved PNG: {PROJECT_ROOT / report['artifacts']['per_class_f1_png']}")


if __name__ == "__main__":
    evaluation = run_evaluation()
    _print_report(evaluation)
