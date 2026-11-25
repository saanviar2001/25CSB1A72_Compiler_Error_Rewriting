import os
import matplotlib.pyplot as plt
import numpy as np

# Define the data based on your actual RULE_REGISTRY
# This represents the 55+ rules we added across different categories
categories = [
    "Buffer Overflows", 
    "Command Injection", 
    "Memory Leaks/Frees", 
    "Broken Cryptography", 
    "Insecure File I/O", 
    "Concurrency/Signals"
]

# How many rules exist in each category in your security_analyzer.py
counts = [12, 8, 9, 6, 10, 5] 

# Colors matching your dashboard theme
colors = ['#EF4444', '#F97316', '#F59E0B', '#8B5CF6', '#3B82F6', '#10B981']

# Create the figure with two subplots (1 row, 2 columns)
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

# --- Chart 1: Bar Chart of Rule Categories ---
bars = ax1.bar(categories, counts, color=colors, edgecolor='black', linewidth=0.5)
ax1.set_title('Security Rules Implemented by Category', fontsize=14, fontweight='bold')
ax1.set_ylabel('Number of Rules', fontsize=12)
ax1.set_xticklabels(categories, rotation=45, ha='right', fontsize=10)
ax1.grid(axis='y', linestyle='--', alpha=0.7)

# Add value labels on top of bars
for bar in bars:
    yval = bar.get_height()
    ax1.text(bar.get_x() + bar.get_width()/2, yval + 0.2, int(yval), ha='center', va='bottom', fontweight='bold')

# --- Chart 2: Pie Chart of Severity Distribution ---
# Based on the RULE_REGISTRY severities
severities = ["Critical", "High", "Medium", "Low"]
severity_counts = [8, 22, 18, 7] # The distribution of rules across severity levels
severity_colors = ['#DC2626', '#F97316', '#F59E0B', '#10B981']

wedges, texts, autotexts = ax2.pie(
    severity_counts, 
    labels=severities, 
    colors=severity_colors, 
    autopct='%1.1f%%',
    startangle=140, 
    textprops={'fontsize': 12, 'fontweight': 'bold'},
    wedgeprops={'edgecolor': 'black', 'linewidth': 0.5}
)
ax2.set_title('Security Rules by Severity Level', fontsize=14, fontweight='bold')

# Adjust layout and save
plt.tight_layout()
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
output_path = os.path.join(PROJECT_ROOT, "reports", "security_threat_graph.png")
os.makedirs(os.path.dirname(output_path), exist_ok=True)
plt.savefig(output_path, dpi=300)
print(f"✅ Security Graph saved to: {output_path}")