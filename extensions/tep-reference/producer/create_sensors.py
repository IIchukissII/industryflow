# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Create 52 sensors for TEP equipment
"""
import uuid
import json

# Generate 52 sensor UUIDs
sensors = []

# xmeas_1 to xmeas_41 (41 measured variables)
for i in range(1, 42):
    sensor_id = str(uuid.uuid4())
    sensors.append({
        "sensor_id": sensor_id,
        "sensor_name": f"xmeas_{i}",
        "sensor_type": "measured",
        "unit": "varies",
        "position": i - 1,
        "is_required_for_ml": True
    })

# xmv_1 to xmv_11 (11 manipulated variables)
for i in range(1, 12):
    sensor_id = str(uuid.uuid4())
    sensors.append({
        "sensor_id": sensor_id,
        "sensor_name": f"xmv_{i}",
        "sensor_type": "manipulated",
        "unit": "varies",
        "position": 40 + i,
        "is_required_for_ml": True
    })

# Save to file
with open('data/sensors_mapping.json', 'w') as f:
    json.dump(sensors, f, indent=2)

print(f"✅ Generated {len(sensors)} sensors")
print(f"   Saved to: data/sensors_mapping.json")

# Create bulk add payload
bulk_payload = {
    "sensors": [
        {
            "sensor_id": s["sensor_id"],
            "sensor_name": s["sensor_name"],
            "sensor_type": s["sensor_type"],
            "unit": s["unit"],
            "position": s["position"],
            "is_required_for_ml": s["is_required_for_ml"]
        }
        for s in sensors
    ]
}

with open('data/bulk_sensors_payload.json', 'w') as f:
    json.dump(bulk_payload, f, indent=2)

print(f"✅ Bulk payload saved to: data/bulk_sensors_payload.json")
