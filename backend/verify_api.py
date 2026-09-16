import sys

sys.path.insert(0, r"c:\Users\saiku\OneDrive\Desktop\Multi-Format-Quiz-Question-Bank-Generator\backend")

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
response = client.post(
    "/api/questions/generate",
    files={"file": ("sample.txt", b"Photosynthesis converts sunlight into chemical energy. Mitosis produces identical cells. Gravity keeps planets in orbit.")},
)
print("generate", response.status_code, len(response.json()["questions"]))

json_response = client.get("/api/questions/export?format=json")
print("json", json_response.status_code, len(json_response.json()["questions"]))

csv_response = client.get("/api/questions/export?format=csv")
print("csv", csv_response.status_code, "question" in csv_response.text.lower(), csv_response.headers.get("content-type"))
