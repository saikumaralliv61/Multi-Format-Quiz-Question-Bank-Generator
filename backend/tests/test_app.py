import unittest
from io import BytesIO

from fastapi.testclient import TestClient

from app.main import app


class QuizGeneratorApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_health_endpoint(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_generate_and_list_questions(self):
        response = self.client.post(
            "/api/questions/generate",
            files={"file": ("sample.txt", b"Photosynthesis is the process plants use to convert sunlight into energy. Cells divide during mitosis to produce identical daughter cells. Gravity keeps planets in orbit around the sun.")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertGreaterEqual(len(payload["questions"]), 1)

        list_response = self.client.get("/api/questions")
        self.assertEqual(list_response.status_code, 200, list_response.text)
        questions = list_response.json()["questions"]
        self.assertGreaterEqual(len(questions), 1)

    def test_export_questions(self):
        self.client.post(
            "/api/questions/generate",
            files={"file": ("sample.txt", b"Photosynthesis converts sunlight into chemical energy. Mitosis produces identical cells. Gravity keeps planets in orbit.")},
        )

        response = self.client.get("/api/questions/export?format=json")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("questions", response.json())

        csv_response = self.client.get("/api/questions/export?format=csv")
        self.assertEqual(csv_response.status_code, 200, csv_response.text)
        self.assertIn("question", csv_response.text.lower())


if __name__ == "__main__":
    unittest.main()
