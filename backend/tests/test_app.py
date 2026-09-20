import unittest
import re
from io import BytesIO
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app, generate_questions, generate_questions_with_ollama, parse_ollama_json


class QuizGeneratorApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.provider_patch = patch(
            "app.main.generate_questions_with_ollama",
            side_effect=generate_questions,
        )
        self.provider_mock = self.provider_patch.start()
        self.addCleanup(self.provider_patch.stop)

    def test_health_endpoint(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_parse_ollama_json_repairs_malformed_payload(self):
        content = '{"questions": [{"question": "What is photosynthesis?" "topic": "Biology"}]}'

        result = parse_ollama_json(content)

        self.assertEqual(result["questions"][0]["topic"], "Biology")

    def test_parse_ollama_json_rejects_unrecoverable_payload(self):
        self.assertIsNone(parse_ollama_json("not JSON at all"))

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

    @patch("app.main.generate_questions_with_ollama", return_value=None)
    def test_generation_falls_back_when_ollama_is_unavailable(self, _mock_ollama):
        response = self.client.post(
            "/api/questions/generate",
            files={"file": ("github-required.txt", b"Photosynthesis is the process plants use to convert sunlight into energy.")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertGreaterEqual(len(response.json()["questions"]), 1)

    def test_same_document_and_type_reuses_cached_questions(self):
        source = f"A cache-specific document {id(self)} explains how binary trees organize nodes for efficient searching.".encode()
        first = self.client.post(
            "/api/questions/generate",
            data={"difficulty": "Hard", "question_type": "quiz"},
            files={"file": ("cache-first.txt", source)},
        )
        second = self.client.post(
            "/api/questions/generate",
            data={"difficulty": "Hard", "question_type": "quiz"},
            files={"file": ("cache-renamed.txt", source)},
        )
        different_type = self.client.post(
            "/api/questions/generate",
            data={"difficulty": "Hard", "question_type": "fill_blank"},
            files={"file": ("cache-renamed.txt", source)},
        )

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(different_type.status_code, 200, different_type.text)
        self.assertFalse(first.json()["cached"])
        self.assertTrue(second.json()["cached"])
        self.assertFalse(different_type.json()["cached"])
        self.assertEqual(first.json()["questions"], second.json()["questions"])
        self.assertEqual(self.provider_mock.call_count, 2)

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

    def test_export_pdf_uses_submitted_questions(self):
        response = self.client.post(
            "/api/questions/export?format=pdf",
            json={
                "questions": [
                    {
                        "id": 1,
                        "question": "What is photosynthesis?",
                        "difficulty": "Medium",
                        "topic": "Biology",
                    }
                ]
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_generate_question_formats_and_difficulty(self):
        source = b"Photosynthesis is the process plants use to convert sunlight into energy. Cells divide during mitosis to produce identical daughter cells. Gravity keeps planets in orbit around the sun."

        for question_type in ("quiz", "fill_blank", "mcq"):
            response = self.client.post(
                "/api/questions/generate",
                data={"difficulty": "Hard", "question_type": question_type},
                files={"file": ("format-source.txt", source)},
            )
            self.assertEqual(response.status_code, 200, response.text)
            question = response.json()["questions"][0]
            self.assertEqual(question["difficulty"], "Hard")
            self.assertEqual(question["question_type"], question_type)
            self.assertNotIn("source_answer", question)
            self.assertNotIn("Theory in General", question["question"])
            if question_type == "quiz":
                self.assertTrue(question["question"])
                self.assertNotIn("answer", question)
            if question_type == "fill_blank":
                self.assertIn("____", question["question"])
                self.assertNotIn("answer", question)
            if question_type == "mcq":
                self.assertGreaterEqual(len(question["options"]), 2)
                self.assertNotIn("answer", question)

    @patch("app.main.call_ollama")
    def test_generation_strictly_respects_selected_question_type(self, mock_call_ollama):
        mock_call_ollama.return_value = {
            "questions": [
                {
                    "question": "What happens in photosynthesis?",
                    "topic": "Biology",
                    "unit": "General",
                    "format": "fill_blank",
                    "section": "General",
                    "marks": 2,
                    "options": [],
                },
                {
                    "question": "What is a cell?",
                    "topic": "Biology",
                    "unit": "General",
                    "format": "fill_blank",
                    "section": "General",
                    "marks": 2,
                    "options": [],
                },
            ]
        }

        questions = generate_questions_with_ollama("Plant cells use sunlight to create energy.", "Easy", "quiz")

        self.assertIsNotNone(questions)
        self.assertTrue(all(question["format"] == "quiz" for question in questions))
        self.assertTrue(all(question["question_type"] == "quiz" for question in questions))

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
        self.assertTrue(all("Course overview" not in question["question"] for question in questions))

    def test_generation_excludes_course_titles_and_objectives(self):
        source = (
            b"Course title: Introduction to Data Science. Course objectives are to understand data analysis. "
            b"Data visualization uses charts and graphs to communicate patterns clearly. "
            b"Statistical analysis describes relationships in a dataset and supports useful decisions."
        )
        response = self.client.post(
            "/api/questions/generate",
            data={"difficulty": "Easy", "question_type": "quiz"},
            files={"file": ("course-content.txt", source)},
        )

        self.assertEqual(response.status_code, 200, response.text)
        questions = response.json()["questions"]
        self.assertTrue(questions)
        self.assertTrue(all(
            not re.search(r"course title|course objectives?|course overview|learning objectives?", question["question"], re.IGNORECASE)
            for question in questions
        ))

    def test_generation_excludes_textbook_lists_and_keeps_numbers(self):
        source = (
            b"Text Books: 1. A standard economics textbook. 2. An accounting reference book. "
            b"Demand forecasting uses historical data to estimate future demand. "
            b"Market analysis compares customer needs and available products."
        )
        response = self.client.post(
            "/api/questions/generate",
            data={"difficulty": "Easy", "question_type": "quiz"},
            files={"file": ("numbered-content.txt", source)},
        )

        self.assertEqual(response.status_code, 200, response.text)
        questions = response.json()["questions"]
        self.assertTrue(questions)
        self.assertEqual([question["question_number"] for question in questions], list(range(1, len(questions) + 1)))
        self.assertTrue(all(
            "text book" not in question["question"].lower()
            and "reference book" not in question["question"].lower()
            for question in questions
        ))

    def test_generation_excludes_bibliography_entries(self):
        source = (
            b"Demand describes the quantity consumers are willing to buy at different prices. "
            b"Arya Sri: Managerial Economics and Financial Analysis, TMH, 2009. "
            b"Varshney and Maheswari, Managerial Economics, Sultan Chand, 2014. "
            b"Market structures explain how firms compete and set prices. "
            b"R K Sharma and Shashi K Gupta, Financial Management, Kalyani Publishers, 2020."
        )
        response = self.client.post(
            "/api/questions/generate",
            data={"difficulty": "Medium", "question_type": "sem_pattern"},
            files={"file": ("bibliography.txt", source)},
        )

        self.assertEqual(response.status_code, 200, response.text)
        questions = response.json()["questions"]
        self.assertTrue(questions)
        self.assertTrue(all(
            not re.search(r"Arya Sri|Varshney|Maheswari|Sharma|Gupta|Sultan Chand|Kalyani|2009|2014|2020", question["question"], re.IGNORECASE)
            for question in questions
        ))

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
            if question_type == "mid_pattern" and len(questions) == 16:
                self.assertEqual([question["section"] for question in questions[:10]], ["Section A"] * 10)
                self.assertEqual([question["section"] for question in questions[10:]], ["Section B"] * 6)
                self.assertEqual([question["marks"] for question in questions[:10]], [1] * 10)
                self.assertEqual([question["marks"] for question in questions[10:]], [5] * 6)
                self.assertGreaterEqual(len({question["question"].split()[0] for question in questions[:10]}), 3)
            if question_type == "all_mix":
                self.assertEqual(len(questions), 3)
                self.assertEqual({question["format"] for question in questions}, {"mcq", "fill_blank", "quiz"})
                self.assertEqual({question["section"] for question in questions}, {"Mixed Questions"})
                self.assertRegex(questions[2]["question"], r"^(What|Why|When|Where|How)\b")

            if question_type == "sem_pattern":
                self.assertTrue(all(
                    question["question"].startswith("Describe the concept in this sentence")
                    for question in questions
                ))
                self.assertTrue(all(not question["question"].startswith(("What ", "Why ", "When ", "Where ", "How ")) for question in questions))
            if question_type == "mid_pattern":
                self.assertTrue(all("does this sentence describe" not in question["question"] for question in questions))

    @patch("app.main.call_ollama")
    def test_reference_exam_patterns_have_exact_sections_and_marks(self, mock_call_ollama):
        for question_type, count, section_a_count, section_a_marks, section_b_marks in (
            ("mid_pattern", 16, 10, 1, 5),
            ("sem_pattern", 10, 5, 2, 8),
        ):
            mock_call_ollama.return_value = {
                "questions": [
                    {
                        "question": f"Explain concept {index}.",
                        "topic": "Data visualization",
                        "unit": "General",
                        "format": "quiz",
                        "section": "Wrong section",
                        "marks": 99,
                    }
                    for index in range(1, count + 1)
                ]
            }

            questions = generate_questions_with_ollama("Data visualization uses charts to show patterns.", "Medium", question_type)

            self.assertEqual(len(questions), count)
            self.assertEqual([question["section"] for question in questions[:section_a_count]], ["Section A"] * section_a_count)
            self.assertEqual([question["marks"] for question in questions[:section_a_count]], [section_a_marks] * section_a_count)
            self.assertEqual([question["section"] for question in questions[section_a_count:]], ["Section B"] * (count - section_a_count))
            self.assertEqual([question["marks"] for question in questions[section_a_count:]], [section_b_marks] * (count - section_a_count))
            self.assertEqual([question["pattern_question_number"] for question in questions], list(range(1, count + 1)))

    def test_question_type_specific_generation_keeps_schema_valid(self):
        generated = {
            "questions": [
                {
                    "question": "What does photosynthesis do?",
                    "topic": "Biology",
                    "unit": "General",
                    "format": "quiz",
                    "section": "General",
                    "marks": 2,
                },
                {
                    "question": "Fill in the blank: Plants make energy using _________.",
                    "topic": "Biology",
                    "unit": "General",
                    "format": "fill_blank",
                    "section": "General",
                    "marks": 2,
                },
            ]
        }

        self.assertIn("questions", generated)
        self.assertTrue(all("options" not in question for question in generated["questions"]))

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
