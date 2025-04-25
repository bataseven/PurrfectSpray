import json
import numpy as np
from sklearn.preprocessing import PolynomialFeatures
from sklearn.linear_model import LinearRegression
from sklearn.pipeline import make_pipeline
import joblib
from shapely.geometry import Polygon, Point
import os

# Load surfaces
with open('surfaces.json', 'r') as f:
    surfaces_data = json.load(f)

# Load calibration points
with open('calibration.json', 'r') as f:
    calibration_data = json.load(f)

# Create polygons
polygons = []
for surface in surfaces_data:
    poly = Polygon(surface['points'])
    polygons.append(poly)

# Associate points with surfaces
surface_points = {i: [] for i in range(len(polygons))}

for point_data in calibration_data:
    pixel = point_data['pixel']
    angles = point_data['angles']
    pt = Point(pixel)

    found = False
    for idx, poly in enumerate(polygons):
        if poly.contains(pt):
            surface_points[idx].append((pixel, angles))
            found = True
            break

    if not found:
        # Optionally log ignored points
        print(f"Ignored point: {pixel}")

# Make output directory
os.makedirs('models', exist_ok=True)

# Train model per surface
model_mapping = {}
for idx, points in surface_points.items():
    if not points:
        print(f"Skipping surface {idx}: no points.")
        continue

    X = np.array([p[0] for p in points])
    y = np.array([p[1] for p in points])

    model = make_pipeline(PolynomialFeatures(degree=2), LinearRegression())
    model.fit(X, y)

    model_path = f'models/surface_{idx}.pkl'
    joblib.dump(model, model_path)

    model_mapping[idx] = model_path

# Save mapping
with open('models/model_mapping.json', 'w') as f:
    json.dump(model_mapping, f, indent=2)

print("✅ All surface models trained and saved.")
