import unittest
from io import BytesIO
from unittest.mock import patch

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

    @patch("app.main.generate_questions", side_effect=AssertionError("fallback generator should not be used"))
    @patch("app.main.generate_questions_with_ollama", return_value=None)
    def test_generation_requires_ollama_without_fallback(self, _mock_ollama, _mock_fallback):
        response = self.client.post(
            "/api/questions/generate",
            files={"file": ("sample.txt", b"Photosynthesis is the process plants use to convert sunlight into energy.")},
        )
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("Ollama", response.json()["detail"])

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

        pdf_response = self.client.get("/api/questions/export?format=pdf")
        self.assertEqual(pdf_response.status_code, 200, pdf_response.text)
        self.assertEqual(pdf_response.headers["content-type"], "application/pdf")
        self.assertTrue(pdf_response.content.startswith(b"%PDF"))

    def test_generate_question_formats_and_difficulty(self):
        source = b"Photosynthesis is the process plants use to convert sunlight into energy. Cells divide during mitosis to produce identical daughter cells. Gravity keeps planets in orbit around the sun."

        for question_type in ("quiz", "fill_blank", "mcq"):
            response = self.client.post(
                "/api/questions/generate",
                data={"difficulty": "Hard", "question_type": question_type},
                files={"file": ("sample.txt", source)},
            )
            self.assertEqual(response.status_code, 200, response.text)
            question = response.json()["questions"][0]
            self.assertEqual(question["difficulty"], "Hard")
            self.assertEqual(question["question_type"], question_type)
            self.assertTrue(question["answer"])
            self.assertTrue(question["source_answer"])
            self.assertNotIn("Theory in General", question["question"])
            if question_type == "quiz":
                self.assertIn(question["source_answer"][:40], question["question"])
            if question_type == "mcq":
                self.assertGreaterEqual(len(question["options"]), 2)

    def test_generation_uses_all_units_and_ignores_course_intro(self):
        source = (
            b"Course overview: this course teaches several broad concepts. "
            b"Unit 1: Cell biology explains how cells are structured and how their parts work together. "
            b"Unit I: Cell biology explains how cells are structured and how their parts work together. "
            b"Unit 2: Genetics describes how traits are inherited through genes and passed between generations."
        )
        response = self.client.post(
            "/api/questions/generate",
            data={"difficulty": "Easy", "question_type": "quiz"},
            files={"file": ("units.txt", source)},
        )

        self.assertEqual(response.status_code, 200, response.text)
        questions = response.json()["questions"]
        units = {question["unit"] for question in questions}
        self.assertEqual(units, {"Unit 1", "Unit I", "Unit 2"})
        self.assertTrue(all("Course overview" not in question["answer"] for question in questions))

    def test_exam_patterns_and_all_mix(self):
        source = b"Unit 1: Data visualization explains charts and graphs used to communicate patterns clearly. Data cleaning removes errors and prepares reliable datasets for analysis. Statistical summaries describe the central features of a dataset."

        for question_type in ("mid_pattern", "sem_pattern", "all_mix"):
            response = self.client.post(
                "/api/questions/generate",
                data={"difficulty": "Medium", "question_type": question_type},
                files={"file": ("pattern.txt", source)},
            )
            self.assertEqual(response.status_code, 200, response.text)
            questions = response.json()["questions"]
            self.assertGreaterEqual(len(questions), 1)
            self.assertTrue(all(question["question_type"] == question_type for question in questions))
            self.assertTrue(all(question["section"] for question in questions))
            self.assertTrue(all(question["marks"] > 0 for question in questions))
            if question_type == "all_mix":
                self.assertGreaterEqual(len({question["format"] for question in questions}), 2)
                self.assertEqual({question["section"] for question in questions}, {"Mid Section A", "Sem Section B"})

    def test_analyze_topics_uses_all_pages_without_questions(self):
        source = (
            b"Course overview only.\n"
            b"Data Visualization\n"
            b"Data visualization uses charts to communicate patterns clearly.\n"
            b"\f"
            b"Statistical Models\n"
            b"Statistical models describe relationships in data and support analysis."
        )
        response = self.client.post("/api/topics/analyze", files={"file": ("study.txt", source)})

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIn("topics", payload)
        self.assertIn("top_priority_topics", payload)
        self.assertTrue(any(topic["topic"] == "Data Visualization" for topic in payload["topics"]))
        self.assertNotIn("questions", payload)

        topic = payload["topics"][0]
        self.assertIn("rank", topic)
        self.assertIn("importance", topic)
        self.assertIn("key_concepts_subtopics", topic)
        self.assertIn("question_types", topic)
        self.assertIn("pages", topic)
        self.assertNotIn("Course overview only", {item["topic"] for item in payload["topics"]})
        self.assertIn("top_5_most_important_topics", payload)


if __name__ == "__main__":
    unittest.main()
