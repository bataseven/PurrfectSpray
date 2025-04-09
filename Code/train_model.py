import json
import numpy as np
from sklearn.preprocessing import PolynomialFeatures
from sklearn.linear_model import LinearRegression
from sklearn.pipeline import make_pipeline
import joblib

# Load calibration data
with open('calibration.json', 'r') as f:
    data = json.load(f)

# Extract inputs (pixels) and outputs (angles)
X = np.array([point['pixel'] for point in data])      # shape: (n_samples, 2)
y = np.array([point['angles'] for point in data])     # shape: (n_samples, 2)

# Create a polynomial regression pipeline
model = make_pipeline(PolynomialFeatures(degree=2), LinearRegression())
model.fit(X, y)

# Save the model
joblib.dump(model, 'model.pkl')

print("✅ Model trained and saved as model.pkl")
