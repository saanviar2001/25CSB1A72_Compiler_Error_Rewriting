import os
import json
import matplotlib.pyplot as plt
import numpy as np
from collections import Counter
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.model_selection import train_test_split
from sklearn.naive_bayes import MultinomialNB
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.svm import LinearSVC
from sklearn.metrics import accuracy_score, f1_score
from sklearn.pipeline import Pipeline

# 1. Load Data
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE = os.path.join(PROJECT_ROOT, "data", "training_data.json")

with open(DATA_FILE, "r") as f:
    raw_data = json.load(f)

X = []
y = []
for row in raw_data:
    msg = row.get("message", "")
    cat = row.get("category", "other").lower() # Force lowercase
    if msg and cat:
        X.append(msg)
        y.append(cat)

# 🚨 FIX: Filter out classes with only 1 example (causes stratify to crash)
counts = Counter(y)
valid_classes = {cls for cls, count in counts.items() if count >= 2}
X_filtered = [x for x, label in zip(X, y) if label in valid_classes]
y_filtered = [label for label in y if label in valid_classes]

print(f"Original dataset: {len(X)} samples")
print(f"Filtered dataset: {len(X_filtered)} samples (dropped rare classes)")

X_train, X_test, y_train, y_test = train_test_split(
    X_filtered, y_filtered, test_size=0.2, random_state=42, stratify=y_filtered
)

# 2. Define Models
models = {
    "Naive Bayes": MultinomialNB(),
    "Logistic Regression": LogisticRegression(max_iter=1000, class_weight="balanced"),
    "Random Forest": RandomForestClassifier(n_estimators=100, class_weight="balanced", random_state=42),
    "Linear SVM": LinearSVC(class_weight="balanced", random_state=42)
}

results = {"Model": [], "Accuracy": [], "F1 Score": []}

print("\nTraining and evaluating models...")

for name, model in models.items():
    print(f" -> Training {name}...")
    pipeline = Pipeline([
        ('tfidf', TfidfVectorizer(ngram_range=(1, 2))),
        ('clf', model)
    ])
    
    # Train
    pipeline.fit(X_train, y_train)
    
    # Predict
    y_pred = pipeline.predict(X_test)
    
    # Metrics
    acc = accuracy_score(y_test, y_pred)
    f1 = f1_score(y_test, y_pred, average="weighted")
    
    results["Model"].append(name)
    results["Accuracy"].append(acc)
    results["F1 Score"].append(f1)
    print(f"    Accuracy: {acc:.4f} | F1: {f1:.4f}")

# 3. Plot the Graph
x = np.arange(len(results["Model"]))
width = 0.35

fig, ax = plt.subplots(figsize=(10, 6))
rects1 = ax.bar(x - width/2, results["Accuracy"], width, label='Accuracy', color='#4F46E5')
rects2 = ax.bar(x + width/2, results["F1 Score"], width, label='Weighted F1 Score', color='#10B981')

ax.set_ylabel('Score (0.0 - 1.0)')
ax.set_title('Model Performance Comparison on Compiler Error Classification')
ax.set_xticks(x)
ax.set_xticklabels(results["Model"])
ax.set_ylim(0, 1.1)
ax.legend()
ax.grid(axis='y', linestyle='--', alpha=0.7)

# Add value labels on top of bars
def autolabel(rects):
    for rect in rects:
        height = rect.get_height()
        ax.annotate(f'{height:.2f}',
                    xy=(rect.get_x() + rect.get_width() / 2, height),
                    xytext=(0, 3), textcoords="offset points",
                    ha='center', va='bottom', fontsize=10, fontweight='bold')

autolabel(rects1)
autolabel(rects2)

fig.tight_layout()

# Save the graph
output_path = os.path.join(PROJECT_ROOT, "reports", "model_comparison.png")
os.makedirs(os.path.dirname(output_path), exist_ok=True)
plt.savefig(output_path, dpi=300)
print(f"\n✅ Graph saved to: {output_path}")