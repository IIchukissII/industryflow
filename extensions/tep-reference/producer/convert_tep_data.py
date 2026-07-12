# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Convert TEP RData files to CSV for streaming simulation
"""
import os

import pandas as pd
import pyreadr
from pathlib import Path

# Configuration. The source RData is not committed (it is large, and not ours to redistribute) —
# download it and either drop it in this extension's own `data/` or point these at it.
#
# These used to reach into `../ml_service/notebooks/data/`, a path that no longer exists and that an
# extension had no business depending on in the first place: an extension implements against the
# platform's contracts, it does not reach into a service's internals (ADR-0008).
FAULT_FREE_FILE = os.getenv('TEP_FAULT_FREE_FILE', 'data/TEP_FaultFree_Training.RData')
FAULTY_FILE = os.getenv('TEP_FAULTY_FILE', 'data/TEP_Faulty_Training.RData')
OUTPUT_FILE = 'data/tep_streaming_data.csv'
RANDOM_STATE = 42

# Balanced sampling
SAMPLES_PER_CLASS = 10000  # 10k normal + 10k anomaly = 20k total for testing

print("="*70)
print("TEP Data Conversion to CSV")
print("="*70)

# Load NORMAL data (fault 0)
print("\n1️⃣ Loading NORMAL data...")
result_normal = pyreadr.read_r(FAULT_FREE_FILE)
df_normal = result_normal['fault_free_training']
print(f"   ✓ Loaded: {df_normal.shape[0]:,} normal samples")

# Sample from normal data
if len(df_normal) > SAMPLES_PER_CLASS:
    df_normal = df_normal.sample(n=SAMPLES_PER_CLASS, random_state=RANDOM_STATE)
    print(f"   ✓ Sampled: {len(df_normal):,} samples")

# Load ANOMALY data (faults 1-20)
print("\n2️⃣ Loading ANOMALY data...")
result_faulty = pyreadr.read_r(FAULTY_FILE)
df_faulty = result_faulty['faulty_training']
print(f"   ✓ Loaded: {df_faulty.shape[0]:,} anomaly samples")

# Sample from faulty data
df_faulty = df_faulty.sample(n=SAMPLES_PER_CLASS, random_state=RANDOM_STATE)
print(f"   ✓ Sampled: {len(df_faulty):,} samples")

# Combine datasets
print("\n3️⃣ Combining datasets...")
df = pd.concat([df_normal, df_faulty], ignore_index=True)

# Shuffle
df = df.sample(frac=1, random_state=RANDOM_STATE).reset_index(drop=True)
print(f"   ✓ Combined dataset: {len(df):,} samples")

# Create is_anomaly column
df['is_anomaly'] = (df['faultNumber'] > 0).astype(int)

# Get sensor columns
sensor_cols = [col for col in df.columns if col.startswith(('xmeas_', 'xmv_'))]
print(f"   ✓ Found {len(sensor_cols)} sensor columns")

# Keep only sensor columns + metadata
columns_to_keep = sensor_cols + ['faultNumber', 'is_anomaly']
df_output = df[columns_to_keep]

# Save to CSV
print("\n4️⃣ Saving to CSV...")
output_path = Path(OUTPUT_FILE)
output_path.parent.mkdir(parents=True, exist_ok=True)
df_output.to_csv(output_path, index=False)
print(f"   ✓ Saved to: {output_path}")
print(f"   ✓ File size: {output_path.stat().st_size / 1024**2:.2f} MB")

print("\n" + "="*70)
print("✅ Conversion complete!")
print("="*70)
print("\nDataset info:")
print(f"  • Total samples: {len(df_output):,}")
print(f"  • Normal samples: {(df_output['is_anomaly']==0).sum():,}")
print(f"  • Anomaly samples: {(df_output['is_anomaly']==1).sum():,}")
print(f"  • Sensor columns: {len(sensor_cols)}")
print(f"  • Columns: {', '.join(df_output.columns[:5])}...")
