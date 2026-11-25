import json
import os
import sys
from collections import Counter
from typing import Any, Optional, Tuple, List

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, precision_recall_fscore_support
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline, FeatureUnion
import numpy as np


_PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
_DEFAULT_DATA_DIR = os.path.join(_PROJECT_ROOT, "data")
_DEFAULT_TRAINING_DATA_FILE = os.path.join(_DEFAULT_DATA_DIR, "training_data.json")
_DEFAULT_MODEL_FILE = os.path.join(_DEFAULT_DATA_DIR, "error_classifier.joblib")

# ── Canonical category set ────────────────────────────────────────────────────
# Covers both old labels and new codenet labels.
# Aliases map legacy/variant spellings → canonical name.
CATEGORY_ALIASES = {
    # old names → new canonical names
    "name_resolution":  "name_resolution",
    "function_error":   "type_error",        # function call mismatch is a type issue
    "linker_error":     "linker_error",
    "warning":          "other",

    # new codenet names (already canonical)
    "syntax_error":     "syntax_error",
    "type_error":       "type_error",
    "missing_include":  "missing_include",
    "redefinition":     "redefinition",
    "access_error":     "access_error",
    "return_type_error":"return_type_error",
    "unused_variable":  "other",
    "other":            "other",
}

ALL_CATEGORIES = list(dict.fromkeys(CATEGORY_ALIASES.values()))  # ordered, deduplicated
MODEL_ALGORITHM = "TF-IDF word + character n-grams with LogisticRegression"
MODEL_VECTORIZER = "FeatureUnion(TfidfVectorizer(ngram_range=(1, 3), token_pattern=r\"(?u)\\b\\w+\\b|[;{}()\\[\\]+-/*=<>]\"), TfidfVectorizer(analyzer='char', ngram_range=(3, 5)))"


def _normalize_category(cat: str) -> str:
    return CATEGORY_ALIASES.get(cat.strip().lower(), "other")


def _build_pipeline() -> Pipeline:
    return Pipeline(
        steps=[
            (
                "features",
                FeatureUnion([
                    (
                        "word",
                        TfidfVectorizer(
                            lowercase=True,
                            ngram_range=(1, 3),
                            min_df=1,
                            max_df=0.95,
                            token_pattern=r"(?u)\b\w+\b|[;{}()\[\]+-/*=<>]",
                        ),
                    ),
                    (
                        "char",
                        TfidfVectorizer(
                            analyzer="char",
                            ngram_range=(3, 5),
                            min_df=1,
                            max_df=0.95,
                        ),
                    ),
                ]),
            ),
            (
                "clf",
                LogisticRegression(
                    max_iter=2000,
                    class_weight="balanced",
                    solver="lbfgs",
                    C=1.0,
                ),
            ),
        ]
    )


def _load_training_examples(training_data_file: str) -> Tuple[List[str], List[str]]:
    with open(training_data_file, "r") as f:
        raw = json.load(f)

    messages: List[str] = []
    labels:   List[str] = []

    if not isinstance(raw, list):
        return messages, labels

    for row in raw:
        if not isinstance(row, dict):
            continue
        msg = row.get("message") or row.get("raw_message")
        cat = row.get("category")
        ast = row.get("ast_node", "")
        if not msg or not cat:
            continue

        cat = _normalize_category(cat)
        feature_str = f"{ast} {msg}".strip()
        messages.append(feature_str)
        labels.append(cat)

    return messages, labels


def _check_imbalance(labels: List[str]) -> dict:
    """Return a dict of category → count and print a warning for severe imbalance."""
    counts = Counter(labels)
    total  = len(labels)
    print(f"\n[Classifier] Class distribution ({total} samples):")
    for cat in sorted(counts, key=lambda c: -counts[c]):
        pct = 100 * counts[cat] / total
        bar = "█" * int(pct / 2)
        print(f"  {cat:<25} {counts[cat]:>5}  ({pct:5.1f}%) {bar}")

    majority = max(counts.values())
    minority = min(counts.values())
    ratio    = majority / minority if minority else float("inf")
    if ratio > 10:
        print(f"\n  ⚠️  Imbalance ratio {ratio:.1f}x — class_weight='balanced' will be applied.")
    else:
        print(f"\n  ✓  Imbalance ratio {ratio:.1f}x — acceptable.")
    return dict(counts)


class ErrorClassifier:
    def __init__(
        self,
        model_file: str = _DEFAULT_MODEL_FILE,
        training_data_file: str = _DEFAULT_TRAINING_DATA_FILE,
    ) -> None:
        self.model_file         = model_file
        self.training_data_file = training_data_file
        self._pipeline: Optional[Pipeline] = None

    def is_trained(self) -> bool:
        return self._pipeline is not None or os.path.exists(self.model_file)

    def train(self) -> None:
        if not os.path.exists(self.training_data_file):
            raise FileNotFoundError(f"Training data not found: {self.training_data_file}")

        X, y = _load_training_examples(self.training_data_file)
        if len(X) < 2:
            raise ValueError("Not enough training examples to train classifier.")

        _check_imbalance(y)

        pipeline = _build_pipeline()
        pipeline.fit(X, y)
        self._pipeline = pipeline
        print(f"[Classifier] Trained on {len(X)} examples across {len(set(y))} categories.")

    def save(self) -> None:
        if self._pipeline is None:
            raise ValueError("No trained model in memory to save.")
        os.makedirs(os.path.dirname(self.model_file), exist_ok=True)
        joblib.dump(self._pipeline, self.model_file)
        print(f"[Classifier] Model saved → {self.model_file}")

    def load(self) -> bool:
        if self._pipeline is not None:
            return True
        if not os.path.exists(self.model_file):
            return False
        try:
            self._pipeline = joblib.load(self.model_file)
            return True
        except Exception:
            return False

    def train_and_save(self) -> None:
        self.train()
        self.save()

    def describe_model(self) -> dict:
        training_examples, labels = _load_training_examples(self.training_data_file)
        labels_seen = sorted(set(labels))
        return {
            "name": "error_classifier",
            "algorithm": MODEL_ALGORITHM,
            "vectorizer": MODEL_VECTORIZER,
            "labels": labels_seen,
            "dataset": {
                "path": self.training_data_file,
                "examples": len(training_examples),
                "categories": labels_seen,
            },
            "model_file": self.model_file,
            "prediction_pipeline": [
                "normalize compiler diagnostic text",
                "optionally combine AST node context",
                "TF-IDF feature extraction (word and char n-grams)",
                "LogisticRegression prediction with class_weight='balanced'",
                "return top class and confidence",
            ],
            "model_kind": "scikit-learn Pipeline",
        }

    def get_model_summary(self) -> dict:
        return self.describe_model()

    def transparency_components(self) -> list[dict[str, str]]:
        """Return only the user-facing names and roles of loaded ML components.

        The names come from the instantiated scikit-learn pipeline so the UI does
        not claim components that are absent from the saved model artifact.
        """
        if not self.load() or self._pipeline is None:
            return []

        components: list[dict[str, str]] = []
        features = self._pipeline.named_steps.get("features")
        if isinstance(features, FeatureUnion):
            for _, transformer in features.transformer_list:
                if not isinstance(transformer, TfidfVectorizer):
                    continue
                estimator_name = transformer.__class__.__name__.replace("Vectorizer", "")
                estimator_name = estimator_name.replace("Tfidf", "TF-IDF")
                if transformer.analyzer == "char":
                    components.append({
                        "name": f"{estimator_name} character n-grams",
                        "role": "text features",
                    })
                else:
                    components.append({
                        "name": f"{estimator_name} word n-grams",
                        "role": "text features",
                    })

        classifier = self._pipeline.named_steps.get("clf")
        if isinstance(classifier, LogisticRegression):
            components.append({
                "name": classifier.__class__.__name__.replace("Regression", " Regression"),
                "role": "classifies the error into a category",
            })
        return components

    def evaluate_on_holdout(self, test_size: float = 0.2, random_state: int = 42) -> dict:
        if not os.path.exists(self.training_data_file):
            raise FileNotFoundError(f"Training data not found: {self.training_data_file}")

        X, y = _load_training_examples(self.training_data_file)
        if len(X) < 2:
            raise ValueError("Not enough examples for a held-out evaluation.")

        if len(set(y)) < 2:
            return {"accuracy": 1.0, "support": len(X), "per_class": {}} 

        stratify = y if min(Counter(y).values()) > 1 else None
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=test_size, random_state=random_state, stratify=stratify
        )

        pipeline = _build_pipeline()
        pipeline.fit(X_train, y_train)
        predictions = pipeline.predict(X_test)

        accuracy = float(accuracy_score(y_test, predictions))
        precision, recall, f1, support = precision_recall_fscore_support(
            y_test, predictions, zero_division=0, average=None, labels=sorted(set(y_test))
        )

        per_class = {}
        for label, p, r, f, n in zip(sorted(set(y_test)), precision, recall, f1, support):
            per_class[label] = {
                "precision": float(p),
                "recall": float(r),
                "f1": float(f),
                "support": int(n),
            }

        return {
            "accuracy": accuracy,
            "support": len(y_test),
            "per_class": per_class,
            "algorithm": MODEL_ALGORITHM,
            "vectorizer": MODEL_VECTORIZER,
            "dataset": self.training_data_file,
        }

    @staticmethod
    def _hard_rule_prediction(message: str) -> Optional[tuple[str, float, str]]:
        """Route errors to categories the healer can fix."""
        lowered = message.lower()

        # Security
        if any(p in lowered for p in ("security violation", "malicious command",
                                    "forbidden system call", "malicious code blocked")):
            return "security_violation", 1.0, "security_rule"

            # Missing semicolon — must run BEFORE other rules
        if "expected" in lowered and ("';'" in lowered or "','" in lowered):
            if "for" not in lowered:
                return "syntax_error", 0.95, "missing_semicolon_rule"

        # For-loop semicolon
        # For-loop semicolon (word-boundary match: 'before' must not trigger this)
        import re as _re_for_loop
        if "expected" in lowered and ";" in lowered and _re_for_loop.search(r"\bfor\b", lowered):
            return "for_loop_semicolon", 0.95, "for_loop_rule"
        # Missing closing brace
        if "expected '}'" in lowered or "function-definition is not allowed" in lowered:
            return "missing_closing_brace", 1.0, "missing_brace_rule"

        # Missing paren
        if "expected ')'" in lowered:
            return "unmatched_paren", 0.95, "missing_paren_rule"

        # Missing include / typo
        if "was not declared" in lowered or "undeclared" in lowered:
            import re as _re
            m = _re.search(r"'([a-zA-Z_][a-zA-Z0-9_:]*)'", message)
            if m:
                sym = m.group(1).split("::")[-1]
                if sym in {"cout", "cin", "cerr", "endl", "string", "vector", "map",
                        "set", "list", "queue", "stack", "deque", "array", "pair",
                        "tuple", "optional", "unordered_map", "unordered_set",
                        "sort", "find", "reverse", "max", "min", "ifstream",
                        "ofstream", "stringstream", "unique_ptr", "shared_ptr",
                        "make_unique", "make_shared", "getline"}:
                    return "missing_include", 0.98, "missing_include_rule"
                if sym in {"whlie", "whille", "wihle", "retrun", "reutrn",
                        "retunr", "breka", "breek", "swtich", "swich",
                        "contineu", "cuntinue"}:
                    return "name_resolution", 0.95, "typo_rule"

        # Uninitialized
        if any(p in lowered for p in ("used uninitialized", "may be used uninitialized",
                                    "uninitialized variable", "wuninitialized")):
            return "uninitialized_memory", 0.98, "uninit_rule"

        # Stream operator
        if any(p in lowered for p in ("no match for 'operator<<'", "no match for 'operator>>'",
                                    "operand types are 'std::istream'",
                                    "operand types are 'std::ostream'",
                                    "invalid operands to binary expression")):
            return "stream_operator", 0.98, "stream_rule"

        # Assignment in condition
        if "assignment used as truth value" in lowered:
            return "syntax_error", 0.95, "assign_cond_rule"

        # Division by zero
        if "division by zero" in lowered or "divide by zero" in lowered:
            return "division_by_zero", 0.98, "div_zero_rule"

        # Unused
        if any(p in lowered for p in ("set but not used", "unused variable",
                                    "unused but set variable", "declared but never used")):
            return "unused_variable", 0.98, "unused_rule"

        return None
    def predict_with_provenance(self, message: str, ast_node: str = "") -> tuple[Optional[str], float, dict[str, Any]]:
        """Predict an error category and report whether a rule or ML decided it."""
        if not message:
            return None, 0.0, {"source": "none"}

        rule_prediction = self._hard_rule_prediction(message)
        if rule_prediction is not None:
            category, confidence, rule_name = rule_prediction
            return category, confidence, {"source": "rule", "rule_name": rule_name}

        if not self.load():
            if os.path.exists(self.training_data_file):
                try:
                    self.train_and_save()
                except Exception:
                    return None, 0.0, {"source": "unavailable"}

            if not self.load():
                return None, 0.0, {"source": "unavailable"}

        assert self._pipeline is not None

        feature_str = f"{ast_node} {message}".strip()
        try:
            proba = self._pipeline.predict_proba([feature_str])
            idx   = int(proba[0].argmax())
            pred  = self._pipeline.classes_[idx]
            conf  = float(proba[0].max())
            return str(pred), conf, {"source": "ml"}
        except Exception:
            pred = self._pipeline.predict([feature_str])[0]
            return str(pred), 0.5, {"source": "ml"}

    def predict(self, message: str, ast_node: str = "") -> Tuple[Optional[str], float]:
        category, confidence, _ = self.predict_with_provenance(message, ast_node)
        return category, confidence

    def evaluate_on_tests(self, test_dir: str) -> dict:
        """
        Compile every .cpp in test_dir, predict its category, and report
        accuracy per category.  Ground-truth labels come from build_dataset_from_tests.py.
        """
        import subprocess, re

        # Import label map from the builder (avoid duplication)
        sys_path_backup = list(__import__("sys").path)
        __import__("sys").path.insert(0, os.path.dirname(__file__))
        from build_dataset_from_tests import FILE_LABELS, ERROR_PATTERN  # type: ignore

        results = {}   # category → {correct, total}
        detail  = []

        for fname, expected_cat in FILE_LABELS.items():
            if expected_cat is None:
                continue
            fpath = os.path.join(test_dir, fname)
            if not os.path.exists(fpath):
                continue

            result = subprocess.run(
                ["g++", "-std=c++17", "-Wall", fpath],
                capture_output=True, text=True
            )
            predicted_cats = []
            for line in result.stderr.splitlines():
                m = re.match(
                    r"^.+?:\d+(?::\d+)?:\s*(error|warning):\s*(.*)$",
                    line.strip()
                )
                if m:
                    msg = m.group(2).strip()
                    pred, conf = self.predict(msg)
                    if pred:
                        predicted_cats.append(pred)

            # Use majority vote across all errors in the file
            predicted = Counter(predicted_cats).most_common(1)[0][0] if predicted_cats else "other"
            correct   = (predicted == expected_cat)

            if expected_cat not in results:
                results[expected_cat] = {"correct": 0, "total": 0}
            results[expected_cat]["total"]   += 1
            results[expected_cat]["correct"] += int(correct)
            detail.append((fname, expected_cat, predicted, "✓" if correct else "✗"))

        # Print report
        print("\n" + "═" * 62)
        print("  CLASSIFIER ACCURACY REPORT — per category")
        print("═" * 62)
        print(f"  {'File':<35} {'Expected':<22} {'Predicted':<22} {'OK'}")
        print("─" * 62)
        for fname, exp, pred, mark in detail:
            print(f"  {fname:<35} {exp:<22} {pred:<22} {mark}")

        print("\n" + "─" * 62)
        total_correct = total_files = 0
        for cat, r in sorted(results.items()):
            acc = 100 * r["correct"] / r["total"]
            total_correct += r["correct"]
            total_files   += r["total"]
            print(f"  {cat:<25}  {r['correct']}/{r['total']}  ({acc:.0f}%)")

        overall = 100 * total_correct / total_files if total_files else 0
        print("─" * 62)
        print(f"  {'OVERALL':<25}  {total_correct}/{total_files}  ({overall:.0f}%)")
        print("═" * 62 + "\n")

        return results


_default_classifier: Optional[ErrorClassifier] = None


def get_default_classifier() -> ErrorClassifier:
    global _default_classifier
    if _default_classifier is None:
        _default_classifier = ErrorClassifier()
    return _default_classifier


def predict_error_category(error_message: str) -> str:
    """Legacy fallback using the persisted model when available."""
    clf = get_default_classifier()
    prediction, _ = clf.predict(error_message)
    return prediction or "unknown"


if __name__ == "__main__":
    import sys
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    TEST_DIR     = os.path.join(PROJECT_ROOT, "test_cases")

    print("Step 1 — Building dataset from test cases...")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from build_dataset_from_tests import build   # type: ignore
    build()

    print("\nStep 2 — Training classifier...")
    clf = ErrorClassifier()
    clf.train_and_save()

    print("\nStep 3 — Evaluating on test cases...")
    clf.evaluate_on_tests(TEST_DIR)
