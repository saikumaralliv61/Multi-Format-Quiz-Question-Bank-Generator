from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pypdf import PdfReader
import httpx
from dotenv import load_dotenv
from json_repair import repair_json
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from .db import get_connection, init_db

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

app = FastAPI(title="Quiz Question Bank Generator API")
logger = logging.getLogger(__name__)
question_bank: list[dict[str, Any]] = []

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def parse_ollama_json(content: str) -> dict[str, Any] | None:
    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        try:
            result = json.loads(repair_json(content))
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
    return result if isinstance(result, dict) else None


def normalize_text(raw_text: str) -> str:
    text = raw_text.replace("\r", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_text_from_content(filename: str, content: bytes) -> str:
    filename = filename.lower()
    if filename.endswith(".pdf"):
        try:
            reader = PdfReader(io.BytesIO(content))
            pages = [page.extract_text() or "" for page in reader.pages]
        except Exception as exc:
            raise HTTPException(status_code=400, detail="The uploaded PDF could not be read.") from exc
        return normalize_text("\n".join(pages))

    if filename.endswith((".txt", ".md", ".csv")):
        return normalize_text(content.decode("utf-8", errors="ignore"))

    raise HTTPException(status_code=400, detail="Unsupported file type. Use .txt, .md, .csv, or .pdf")


def extract_text_from_upload(file: UploadFile) -> str:
    return extract_text_from_content(file.filename or "", file.file.read())


def normalize_document_filename(filename: str) -> str:
    return os.path.basename(filename).strip().lower()


def extract_pages_from_upload(file: UploadFile) -> list[str]:
    filename = (file.filename or "").lower()
    content = file.file.read()

    if filename.endswith(".pdf"):
        try:
            reader = PdfReader(io.BytesIO(content))
            return [re.sub(r"[ \t]+", " ", page.extract_text() or "").strip() for page in reader.pages]
        except Exception as exc:
            raise HTTPException(status_code=400, detail="The uploaded PDF could not be read.") from exc

    if filename.endswith((".txt", ".md", ".csv")):
        return [page for page in (re.sub(r"[ \t]+", " ", part).strip() for part in content.decode("utf-8", errors="ignore").split("\f")) if page]

    raise HTTPException(status_code=400, detail="Unsupported file type. Use .txt, .md, .csv, or .pdf")


def analyze_important_topics(pages: list[str]) -> dict[str, Any]:
    excluded_pattern = re.compile(
        r"^(?:unit|chapter|module|course|subject|syllabus|contents?|index|references?|bibliography|"
        r"acknowledg|objective|outcome|co[- ]?outcome|learning|introduction|conclusion|question paper|"
        r"university|college|department|semester|examination|date|time|page|appendix)\b",
        re.IGNORECASE,
    )
    definition_pattern = re.compile(r"\b(?:is defined as|is known as|refers to|means|defined by)\b", re.IGNORECASE)
    academic_signal_pattern = re.compile(
        r"\b(?:algorithm|architecture|application|advantage|classification|concept|difference|diagram|"
        r"equation|formula|method|model|principle|process|theory|theorem|type|types|uses?|steps?|"
        r"properties|characteristics|examples?|limitations?|disadvantage|calculation|analysis)\b",
        re.IGNORECASE,
    )
    topics: dict[str, dict[str, Any]] = {}

    def clean_topic(candidate: str) -> str:
        candidate = re.sub(r"^(?:unit\s+(?:\d+|[ivxlcdm]+)|chapter\s+\w+|\d+(?:\.\d+)*|[A-Z])\s*[:.)-]\s*", "", candidate, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", candidate).strip(" :.-")

    def is_academic_topic(candidate: str) -> bool:
        words = candidate.split()
        return (
            2 <= len(words) <= 12
            and len(candidate) <= 90
            and not excluded_pattern.search(candidate)
            and not re.fullmatch(r"[\d\W]+", candidate)
            and not candidate.endswith((".", ",", ";", ":"))
        )

    def add_topic(name: str, page_number: int, evidence: list[str], bonus: int = 0) -> None:
        name = clean_topic(name)
        if not is_academic_topic(name):
            return
        key = re.sub(r"[^a-z0-9 ]", "", name.lower())
        existing_key = next((item for item in topics if key in item or item in key), key)
        entry = topics.setdefault(existing_key, {"name": name, "pages": set(), "score": 0, "evidence": []})
        entry["pages"].add(page_number)
        entry["score"] += 1 + bonus + len(evidence) // 2
        entry["evidence"].extend(evidence)

    for page_number, page_text in enumerate(pages, start=1):
        lines = [line.strip(" -*•\t") for line in page_text.splitlines() if line.strip()]
        sentences = [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", page_text) if len(sentence.strip()) >= 45]
        for line in lines:
            candidate = clean_topic(line)
            if is_academic_topic(candidate):
                related = [sentence for sentence in sentences if candidate.lower() in sentence.lower()]
                add_topic(candidate, page_number, related[:3], 1 if related else 0)

        for sentence in sentences:
            if definition_pattern.search(sentence):
                label = re.split(r"\s+(?:is defined as|is known as|refers to|means|defined by)\s+", sentence, maxsplit=1, flags=re.IGNORECASE)[0]
                add_topic(label, page_number, [sentence], 3)
            elif academic_signal_pattern.search(sentence):
                label = re.split(r"\s+(?:is|are|includes?|involves?|uses?|has|have|can)\s+", sentence, maxsplit=1, flags=re.IGNORECASE)[0]
                add_topic(label, page_number, [sentence], 1)

    if not topics:
        raise HTTPException(status_code=400, detail="No clear academic topics could be identified in the uploaded PDF.")

    ranked = sorted(topics.values(), key=lambda item: (-item["score"], min(item["pages"])))
    maximum_score = ranked[0]["score"]
    results = []
    for index, item in enumerate(ranked, start=1):
        importance = "Very High" if item["score"] >= max(5, maximum_score * 0.7) else "High" if item["score"] >= 3 else "Medium"
        evidence = list(dict.fromkeys(item["evidence"]))
        combined_text = " ".join(evidence).lower()
        question_types = ["MCQ", "Fill in the Blanks", "Short Answer", "Long Answer"]
        if re.search(r"\b(formula|equation|calculate|calculation|numerical|computation)\b", combined_text):
            question_types.append("Numerical")
        results.append({
            "rank": index,
            "topic": item["name"],
            "important_topic": item["name"],
            "importance": importance,
            "key_concepts_subtopics": evidence[0][:400] if evidence else f"Concepts related to {item['name']} found in the PDF.",
            "covers": evidence[0][:400] if evidence else f"Concepts related to {item['name']} found in the PDF.",
            "question_types": question_types,
            "pages": sorted(item["pages"]),
        })

    top_five = [{"topic": item["topic"], "key_points": item["key_concepts_subtopics"]} for item in results[:5]]
    return {"title": "IMPORTANT TOPICS", "topics": results, "top_priority_topics": [item["topic"] for item in results[:5]], "top_5_most_important_topics": top_five}


def extract_unit_sections(text: str) -> list[tuple[str, str]]:
    unit_pattern = re.compile(r"\bunit\s+(\d+|[ivxlcdm]+)\b", re.IGNORECASE)
    matches = list(unit_pattern.finditer(text))
    if not matches:
        return [("General", text)]

    sections = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        unit_label = match.group(1).upper() if match.group(1).isalpha() else match.group(1)
        section_text = text[start:end].strip(" :-.\n\t")
        if section_text:
            sections.append((f"Unit {unit_label}", section_text))
    return sections


def is_non_question_content(sentence: str) -> bool:
    return bool(re.search(
        r"\b(?:course title|course name|course overview|course objectives?|course outcomes?|"
        r"learning objectives?|learning outcomes?|objectives?|outcomes?|syllabus|introduction|"
        r"about this course|this course teaches|the course covers|text books?|textbooks?|"
        r"reference books?|references?|bibliography|suggested readings|"
        r"publisher|publishers|press|pearson|mcgraw|sultan chand|kalyani|oxford university press|"
        r"\b(?:19|20)\d{2}\b)",
        sentence,
        re.IGNORECASE,
    ))


def filter_generation_source(text: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
    return " ".join(sentence.strip() for sentence in sentences if sentence.strip() and not is_non_question_content(sentence))


def generate_questions(text: str, difficulty: str = "Medium", question_type: str = "quiz") -> list[dict[str, Any]]:
    difficulty_options = {"Easy", "Medium", "Hard"}
    question_type_options = {"quiz", "fill_blank", "mcq", "mid_pattern", "sem_pattern", "all_mix"}
    if difficulty not in difficulty_options:
        raise HTTPException(status_code=400, detail="Difficulty must be Easy, Medium, or Hard.")
    if question_type not in question_type_options:
        raise HTTPException(status_code=400, detail="Unsupported question type.")

    unit_sections = extract_unit_sections(text)
    if not any(section_text.strip() for _, section_text in unit_sections):
        raise HTTPException(status_code=400, detail="No usable text found in the uploaded document.")

    questions = []
    question_id = 1
    for unit_label, section_text in unit_sections:
        sentences = re.split(r"(?<=[.!?])\s+", section_text)
        sentences = [
            sentence.strip()
            for sentence in sentences
            if len(sentence.strip()) > 40 and not is_non_question_content(sentence)
        ]
        if question_type == "all_mix":
            selected = sentences[:3]
        elif question_type == "mid_pattern":
            selected = sentences[:16]
        elif question_type == "sem_pattern":
            selected = sentences[:10]
        else:
            selected = sentences[:5]

        for position, sentence in enumerate(selected, start=1):
            topic = sentence.split()[0:4]
            generated_type = question_type
            section = "General"
            marks = 2
            if question_type == "mid_pattern":
                section = "Section A" if position <= 10 else "Section B"
                marks = 1 if position <= 10 else 5
                generated_type = "quiz"
            elif question_type == "sem_pattern":
                section = "Section A" if position <= 5 else "Section B"
                marks = 2 if position <= 5 else 8
                generated_type = "quiz"
            elif question_type == "all_mix":
                generated_type = ("mcq", "fill_blank", "quiz")[(position - 1) % 3]
                section = "Mixed Questions"
                marks = 1

            question: dict[str, Any] = {
                "id": question_id,
                "question_number": question_id,
                "difficulty": difficulty,
                "topic": " ".join(topic),
                "unit": unit_label,
                "question_type": question_type,
                "format": generated_type,
                "section": section,
                "marks": marks,
            }

            if generated_type == "fill_blank":
                words = sentence.split()
                answer_word = words[0].strip(".,!?;:")
                question["question"] = sentence.replace(answer_word, "________", 1)
            elif generated_type == "mcq":
                correct_answer = sentence[:100]
                distractors = [other[:100] for other in selected if other != sentence][:3]
                options = [correct_answer, *distractors]
                question["question"] = f"Which statement best matches this topic in {unit_label}: {' '.join(topic)}?"
                question["options"] = options
            else:
                if question_type == "mid_pattern":
                        if section == "Section A":
                            wh_starts = ("What", "Why", "How", "Where", "When")
                            question["question"] = f"{wh_starts[(position - 1) % len(wh_starts)]} is the main concept described in this statement: '{sentence[:260]}'?"
                        else:
                            question["question"] = f"Discuss the concept and its key points: '{sentence[:260]}'"
                elif question_type == "sem_pattern":
                    question["question"] = f"Describe the concept in this sentence and discuss its key points: '{sentence[:260]}'"
                else:
                    question["question"] = f"What is the main concept explained in this statement from {unit_label}: '{sentence[:260]}'?"

            questions.append(question)
            question_id += 1

    if not questions:
        raise HTTPException(status_code=400, detail="No usable unit content found in the uploaded document.")

    return questions


def get_document(document_id: str) -> dict[str, str] | None:
    init_db()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT document_id, filename, content, ollama_context FROM documents WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_document_by_filename(filename: str) -> dict[str, str] | None:
    init_db()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT document_id, filename, content, ollama_context FROM documents WHERE lower(filename) = ?",
            (normalize_document_filename(filename),),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def save_document(document_id: str, filename: str, content: str) -> None:
    init_db()
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO documents (document_id, filename, content) VALUES (?, ?, ?)",
            (document_id, normalize_document_filename(filename), content),
        )
        conn.commit()
    finally:
        conn.close()


def save_document_context(document_id: str, context: str) -> None:
    init_db()
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE documents SET ollama_context = ? WHERE document_id = ?",
            (context, document_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_cached_questions(document_id: str, difficulty: str, question_type: str) -> list[dict[str, Any]] | None:
    cache_key = f"v12:{document_id}:{difficulty}:{question_type}"
    init_db()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT questions_json FROM generation_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if not row:
            return None
        questions = json.loads(row["questions_json"])
        return questions if isinstance(questions, list) else None
    except (json.JSONDecodeError, TypeError):
        return None
    finally:
        conn.close()


def save_cached_questions(document_id: str, difficulty: str, question_type: str, questions: list[dict[str, Any]]) -> None:
    cache_key = f"v12:{document_id}:{difficulty}:{question_type}"
    init_db()
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO generation_cache (cache_key, questions_json) VALUES (?, ?)",
            (cache_key, json.dumps(questions)),
        )
        conn.commit()
    finally:
        conn.close()


QUESTION_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "topic": {"type": "string"},
                    "unit": {"type": "string"},
                    "format": {"type": "string", "enum": ["quiz", "fill_blank", "mcq"]},
                    "section": {"type": "string"},
                    "marks": {"type": "integer"},
                    "options": {"type": "array", "items": {"type": "string"}},
                    "answer": {"type": "string"},
                },
                "required": ["question", "topic", "unit", "format", "section", "marks"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["questions"],
    "additionalProperties": False,
}


EXAM_PATTERNS = {
    "mid_pattern": {
        "section_a_count": 10,
        "section_a_marks": 1,
        "section_b_count": 6,
        "section_b_marks": 5,
        "section_a_instruction": "Answer all questions",
        "section_a_marks_label": "1 x 10 = 10 marks",
        "section_b_instruction": "Answer any four questions",
        "section_b_marks_label": "4 x 5 = 20 marks",
    },
    "sem_pattern": {
        "section_a_count": 5,
        "section_a_marks": 2,
        "section_b_count": 5,
        "section_b_marks": 8,
        "section_a_instruction": "Answer all questions",
        "section_a_marks_label": "2 x 5 = 10 marks",
        "section_b_instruction": "Answer all questions",
        "section_b_marks_label": "5 x 8 = 40 marks",
    },
}


def call_ollama(
    prompt: str,
    temperature: float,
    max_tokens: int,
    response_schema: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    endpoint = os.getenv("OLLAMA_ENDPOINT", "http://localhost:11434/api/generate").strip().rstrip("/")
    model = os.getenv("OLLAMA_MODEL", "llama3.2:latest").strip()
    timeout = min(float(os.getenv("OLLAMA_TIMEOUT", "120")), 180.0)
    native_ollama = endpoint.endswith("/api/generate")
    request_options = {
        "temperature": temperature,
        "num_predict": max_tokens,
        "repeat_penalty": 1.15,
        "repeat_last_n": 128,
    }
    formats = [response_schema or "json"] if native_ollama else [None]
    if native_ollama and response_schema:
        formats.append("json")

    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0), trust_env=False) as client:
            for output_format in formats:
                request_body = {"model": model, "prompt": prompt}
                if native_ollama:
                    request_body.update({"stream": False, "format": output_format, "options": request_options})
                else:
                    request_body.update({
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        "response_format": {"type": "json_object"},
                    })

                response = client.post(
                    endpoint,
                    headers={"Authorization": "Bearer ollama", "Content-Type": "application/json"},
                    json=request_body,
                )
                if response.is_error:
                    logger.warning("Ollama returned HTTP %s using format %s: %s", response.status_code, output_format, response.text[:500])
                    continue

                response_body = response.json()
                if response_body.get("error"):
                    logger.warning("Ollama prediction failed using format %s: %s", output_format, response_body["error"])
                    continue
                content = response_body["response"] if native_ollama else response_body["choices"][0]["message"]["content"]
                if not isinstance(content, str):
                    continue
                content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE)
                parsed = parse_ollama_json(content)
                if parsed is not None:
                    return parsed
                logger.warning("Ollama returned invalid JSON using format %s", output_format)
        return None
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        logger.exception("Ollama request failed: %s", exc)
        return None


def compress_document_context(text: str) -> str | None:
    """Create a reusable factual context once so later generations use a smaller prompt."""
    prompt = f"""
Create a compact but complete study context from the supplied document.
Preserve every important fact, definition, formula, process, example, unit, and relationship.
Do not add outside knowledge. Organize the result with headings and bullet points.
Return ONLY valid JSON in this exact shape: {{"context": "..."}}

Document:
{text}
"""
    result = call_ollama(prompt, temperature=0.1, max_tokens=2500)
    context = result.get("context") if result else None
    return context.strip() if isinstance(context, str) and context.strip() else None


def build_question_generation_prompt(text: str, difficulty: str, question_type: str) -> str:
    """Create a compact prompt tuned to the selected question generation mode."""
    base_rules = [
        "You are an academic question generator. Use ONLY the supplied study material.",
        "Do not use outside knowledge. Do not invent facts. Keep every question grounded in the source.",
        "Do not mention course titles, unit titles, or generic phrases such as 'Theory in General'.",
        "Do not ask questions about the course title, course name, course overview, course objectives, course outcomes, learning objectives, or learning outcomes.",
        "Do not ask questions about textbooks, text books, reference books, bibliography, references, or suggested readings.",
        "Keep each question under 220 characters.",
        "Return concise JSON without markdown or extra explanation.",
        "Difficulty: " + difficulty,
        "Requested format: " + question_type,
        "Return ONLY valid JSON in this exact shape:",
        '{"questions": [{"question": "...", "topic": "...", "unit": "...", "format": "quiz|fill_blank|mcq", "section": "...", "marks": 1, "options": [], "answer": "..."}]}',
    ]

    if question_type == "quiz":
        dynamic_rules = [
            "Create exactly 5 quiz questions.",
            "Use varied WH-style openings such as What, Why, How, Where, and When; do not start every question with the same phrase.",
            "Use a direct short-answer style question with no answer key in the output.",
            "Each question must be clear, factual, and answerable from the material.",
        ]
    elif question_type == "fill_blank":
        dynamic_rules = [
            "Create exactly 5 fill-in-the-blank questions.",
            "Each question must contain exactly one literal blank written as ____ (four underscores).",
            "Keep the blank in the sentence; do not remove it or return only the completed sentence.",
            "Do not include answer keys or explanations.",
            "Keep the sentence grammatically correct after the blank is inserted.",
        ]
    elif question_type == "mcq":
        dynamic_rules = [
            "Create exactly 7 MCQ questions.",
            "Provide exactly 4 different options in the options array.",
            "Every option must be a possible answer, not a copy or paraphrase of the question.",
            "Never repeat an option, and never use the full question as an option.",
            "Use plausible distractors grounded in the study material.",
            "Put the correct option first, and make the answer field exactly match that first option.",
            "The format field must be 'mcq'.",
        ]
    elif question_type == "mid_pattern":
        dynamic_rules = [
            "Reproduce this exact mid-examination pattern: Section A contains exactly 10 WH-style short-answer questions worth 1 mark each, and Section B contains exactly 6 descriptive questions worth 5 marks each.",
            "Section A instruction: 'Answer all questions'; Section A marks: '1 x 10 = 10 marks'. Section B instruction: 'Answer any four questions'; Section B marks: '4 x 5 = 20 marks'.",
            "Return exactly 16 questions: question numbers 1-10 in Section A and 11-16 in Section B.",
            "Format must be quiz-style only, without MCQ or fill_blank variants.",
            "Return questions only. Do not include answer keys, answers, explanations, or source answers.",
            "Section A questions must vary WH openings such as What, Why, How, Where, and When. Section B should test explanation, discussion, comparison, process, and application.",
        ]
    elif question_type == "sem_pattern":
        dynamic_rules = [
            "Reproduce this exact semester-examination pattern: Section A contains exactly 5 short-answer questions worth 2 marks each; Section B contains exactly 5 descriptive questions worth 8 marks each.",
            "Both sections use the instruction 'Answer all questions'. Section A marks are '2 x 5 = 10 marks' and Section B marks are '5 x 8 = 40 marks'.",
            "Return exactly 10 questions: question numbers 1-5 in Section A and 6-10 in Section B.",
            "Format must be quiz-style only, without MCQ or fill_blank variants.",
            "Return questions only. Do not include answer keys, answers, explanations, or source answers.",
            "Use the supplied sentences directly to form questions. Do not use What, Why, When, Where, or How openings.",
            "Section A questions 1-5 should test concise concepts. Section B questions 6-10 should use the same direct sentence-based format while testing deeper explanation, illustration, comparison, process, charting, or application.",
        ]
    elif question_type == "all_mix":
        dynamic_rules = [
            "Create exactly 3 questions total: exactly 1 MCQ, exactly 1 fill-in-the-blank, and exactly 1 WH-style short-answer question.",
            "Use formats mcq, fill_blank, and quiz exactly once each, in that order.",
            "The WH-style question must begin with What, Why, When, Where, or How.",
        ]
    else:
        dynamic_rules = ["Create a valid set of grounded questions matching the requested format."]

    prompt = "\n".join(base_rules + dynamic_rules + ["Study material:", text])
    return prompt


def normalize_question_format(question_type: str, item: dict[str, Any]) -> str:
    """Ensure each returned item follows the selected generation mode."""
    if question_type in {"quiz", "fill_blank", "mcq"}:
        return question_type
    if question_type in {"mid_pattern", "sem_pattern"}:
        return "quiz"
    if question_type == "all_mix":
        current_format = item.get("format")
        return current_format if current_format in {"quiz", "fill_blank", "mcq"} else "quiz"
    return "quiz"


def normalize_exam_pattern(question_type: str, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply the selected reference paper's section and marks contract."""
    pattern = EXAM_PATTERNS.get(question_type)
    if not pattern:
        return questions

    expected_count = pattern["section_a_count"] + pattern["section_b_count"]
    if len(questions) != expected_count:
        return []

    for index, question in enumerate(questions):
        in_section_a = index < pattern["section_a_count"]
        question["section"] = "Section A" if in_section_a else "Section B"
        question["marks"] = pattern["section_a_marks"] if in_section_a else pattern["section_b_marks"]
        question["pattern_question_number"] = index + 1
        unit = question.get("unit")
        if isinstance(unit, str) and re.match(r"^\s*[IVXLCDM]+\s+Introduction\s+to\b", unit, re.IGNORECASE):
            question["unit"] = "General"
        if not in_section_a and question_type == "sem_pattern":
            question["or_pair"] = ((index - pattern["section_a_count"]) // 2) + 1
    return questions


def normalize_fill_blank_question(question: str) -> str:
    """Ensure a model response visibly contains a blank for this format."""
    if re.search(r"_{2,}", question):
        return re.sub(r"_{2,}", "____", question, count=1)

    words = list(re.finditer(r"\b[A-Za-z][A-Za-z-]{4,}\b", question))
    if words:
        match = words[0]
        return f"{question[:match.start()]}____{question[match.end():]}"
    return f"{question.rstrip('.')} ____"


def normalize_mcq_options(question: str, options: Any) -> list[str] | None:
    """Keep only distinct answer choices and reject the old repeated-question fallback."""
    if not isinstance(options, list):
        return None

    cleaned = []
    seen = set()
    normalized_question = re.sub(r"\s+", " ", question).strip().casefold()
    for option in options:
        if not isinstance(option, str):
            continue
        value = re.sub(r"\s+", " ", option).strip()
        normalized_value = value.casefold()
        if not value or normalized_value == normalized_question or normalized_value in seen:
            continue
        seen.add(normalized_value)
        cleaned.append(value)

    return cleaned if len(cleaned) >= 2 else None


def normalize_answer_text(value: str) -> str:
    """Compare model answer text without punctuation or option-label differences."""
    value = re.sub(r"^\s*[A-D][.):-]\s*", "", value, flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def fallback_mcq_options(question: str, text: str) -> list[str] | None:
    """Use distinct source statements when the model returns unusable choices."""
    candidates = re.split(r"(?<=[.!?])\s+", text)
    source_options = []
    normalized_question = re.sub(r"\s+", " ", question).strip().casefold()
    for candidate in candidates:
        value = re.sub(r"\s+", " ", candidate).strip(" -\t")
        if len(value) < 20 or value.casefold() == normalized_question:
            continue
        if value.casefold() not in {option.casefold() for option in source_options}:
            source_options.append(value[:220])
        if len(source_options) == 4:
            break
    return source_options if len(source_options) >= 2 else None


def generate_questions_with_ollama(text: str, difficulty: str, question_type: str) -> list[dict[str, Any]] | None:
    """Ask Ollama for grounded questions; return None when the service fails."""
    # Keep the prompt within the local model's context window for large uploads.
    generation_text = filter_generation_source(text)
    generation_text = generation_text[:6000 if question_type in EXAM_PATTERNS else 7000]
    prompt = build_question_generation_prompt(generation_text, difficulty, question_type)

    result = call_ollama(
        prompt,
        temperature=0.2,
        max_tokens=(
            5200
            if question_type == "sem_pattern"
            else 4200
            if question_type == "mid_pattern"
            else 1400
            if question_type == "mcq"
            else 900
            if question_type == "fill_blank"
            else 700
        ),
        response_schema=QUESTION_RESPONSE_SCHEMA,
    )
    generated = result.get("questions") if result else None
    if not isinstance(generated, list) or not generated:
        return None

    questions = []
    for index, item in enumerate(generated, start=1):
        if not isinstance(item, dict) or not item.get("question"):
            continue
        if is_non_question_content(f"{item.get('question', '')} {item.get('topic', '')}"):
            continue

        item["id"] = index
        item["question_number"] = index
        item["difficulty"] = difficulty
        item["question_type"] = question_type
        item.setdefault("topic", "Study material")
        item.setdefault("unit", "General")
        item.setdefault("section", "General")
        item.setdefault("marks", 2)

        item["format"] = normalize_question_format(question_type, item)

        if item["format"] == "fill_blank":
            item["question"] = normalize_fill_blank_question(str(item["question"]))
            item.pop("options", None)
        elif item["format"] == "quiz":
            item.pop("options", None)
            item.pop("answer", None)
            item.pop("source_answer", None)
        elif item["format"] == "mcq":
            options = normalize_mcq_options(str(item["question"]), item.get("options"))
            if options is None:
                options = fallback_mcq_options(str(item["question"]), generation_text)
            if options is None:
                continue
            item["options"] = options
        elif question_type == "all_mix":
            if item.get("format") != "mcq":
                item.pop("options", None)

        if question_type == "sem_pattern":
            question_text = str(item["question"]).strip()
            if re.search(r"explain in detail the concept described in this statement|can this concept be understood in detail|does this sentence describe", question_text, re.IGNORECASE) or re.match(r"^(what|why|when|where|how)\b", question_text, re.IGNORECASE):
                item["question"] = f"Describe the concept in this sentence and discuss its key points: {question_text.rstrip('?')}"

        questions.append(item)

    if not questions:
        return None

    if question_type == "all_mix":
        by_format = {format_name: [item for item in questions if item.get("format") == format_name] for format_name in ("mcq", "fill_blank", "quiz")}
        if any(len(items) != 1 for items in by_format.values()):
            return None
        questions = [by_format[format_name][0] for format_name in ("mcq", "fill_blank", "quiz")]
        if not re.match(r"^(what|why|when|where|how)\b", questions[2]["question"].strip(), re.IGNORECASE):
            questions[2]["question"] = f"What is the main concept described here: {questions[2]['question']}"

    if question_type in EXAM_PATTERNS:
        questions = normalize_exam_pattern(question_type, questions)
        if not questions:
            return None

    if question_type in {"quiz", "fill_blank", "mcq"}:
        if any(item.get("format") != question_type for item in questions):
            return None
    elif question_type in {"mid_pattern", "sem_pattern"}:
        if any(item.get("format") != "quiz" for item in questions):
            return None

    for item in questions:
        item.pop("answer", None)
        item.pop("source_answer", None)

    return questions


def fetch_questions() -> list[dict[str, Any]]:
    return [
        {
            "id": question.get("id"),
            "text": question.get("question", ""),
            "difficulty": question.get("difficulty", ""),
            "topic": question.get("topic", ""),
        }
        for question in reversed(question_bank)
    ]


@app.on_event("startup")
def startup_event() -> None:
    init_db()


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "message": "Quiz generator API is running"}


@app.get("/api/questions")
def list_questions() -> dict[str, Any]:
    questions = fetch_questions()
    return {"questions": questions, "count": len(questions)}


@app.api_route("/api/questions/export", methods=["GET", "POST"])
def export_questions(format: str = "json", payload: dict[str, Any] | None = Body(None)) -> Response:
    if payload and isinstance(payload.get("questions"), list):
        questions = [
            {
                "id": question.get("id"),
                "text": question.get("question", question.get("text", "")),
                "difficulty": question.get("difficulty", ""),
                "topic": question.get("topic", ""),
            }
            for question in payload["questions"]
            if isinstance(question, dict) and question.get("question", question.get("text"))
        ]
    else:
        questions = fetch_questions()

    if format == "json":
        return JSONResponse({"questions": questions, "count": len(questions)})

    if format == "csv":
        output = io.StringIO()
        fieldnames = ["id", "question", "difficulty", "topic", "created_at"]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for question in questions:
            writer.writerow({
                "id": question.get("id", ""),
                "question": question.get("text", ""),
                "difficulty": question.get("difficulty", ""),
                "topic": question.get("topic", ""),
                "created_at": question.get("created_at", ""),
            })
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=question_bank.csv"},
        )

    if format == "pdf":
        if not questions:
            raise HTTPException(status_code=400, detail="Generate some questions before exporting the question bank.")

        output = io.BytesIO()
        document = SimpleDocTemplate(
            output,
            pagesize=letter,
            rightMargin=0.65 * inch,
            leftMargin=0.65 * inch,
            topMargin=0.65 * inch,
            bottomMargin=0.65 * inch,
        )
        styles = getSampleStyleSheet()
        story = [Paragraph("Quiz Question Bank", styles["Title"]), Spacer(1, 0.2 * inch)]

        for index, question in enumerate(questions, start=1):
            metadata = (
                f"<b>Question {index}</b> | Difficulty: {escape(str(question.get('difficulty', '')))} "
                f"| Topic: {escape(str(question.get('topic', '')))}"
            )
            story.append(Paragraph(metadata, styles["Heading3"]))
            story.append(Paragraph(escape(str(question.get("text", ""))), styles["BodyText"]))
            story.append(Spacer(1, 0.08 * inch))
            story.append(Spacer(1, 0.18 * inch))

        document.build(story)
        return Response(
            content=output.getvalue(),
            media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=question_bank.pdf"},
        )

    raise HTTPException(status_code=400, detail="Unsupported export format. Use json or csv.")


@app.post("/api/questions/generate")
async def generate_question_bank(
    file: UploadFile | None = File(None),
    difficulty: str = Form("Medium"),
    question_type: str = Form("quiz"),
    filename: str | None = Form(None),
) -> dict[str, Any]:
    if file is not None:
        file_content = await file.read()
        upload_filename = file.filename or "uploaded-document"
        document_id = hashlib.sha256(file_content).hexdigest()
        document = get_document(document_id)
        if document is None:
            text = extract_text_from_content(file.filename or "", file_content)
            save_document(document_id, file.filename or "uploaded-document", text)
            document = get_document(document_id)
        text = document["content"]
    elif filename:
        document = get_document_by_filename(filename)
        if document is None:
            raise HTTPException(status_code=404, detail="Stored filename was not found. Upload the document first.")
        document_id = document["document_id"]
        text = document["content"]
    else:
        raise HTTPException(status_code=400, detail="Upload a document or provide a stored filename.")

    context = document.get("ollama_context")
    # Avoid a second Ollama request before generation; it makes normal uploads feel stuck.
    # Cached contexts remain supported, while new requests use the source directly.
    if not context:
        context = text

    questions = get_cached_questions(document_id, difficulty, question_type)
    cached = questions is not None
    if not cached:
        questions = generate_questions_with_ollama(context or text, difficulty, question_type)
        if not questions:
            questions = generate_questions(text, difficulty, question_type)
        if questions:
            save_cached_questions(document_id, difficulty, question_type, questions)
    if not questions:
        raise HTTPException(
            status_code=503,
            detail="Ollama generation failed or is unavailable. Start Ollama, pull the selected model, and try again.",
        )
    public_questions = [
        {key: value for key, value in question.items() if key not in {"answer", "source_answer"}}
        for question in questions
    ]
    question_bank.extend(public_questions)
    return {
        "questions": public_questions,
        "count": len(public_questions),
        "cached": cached,
        "context_cached": bool(context),
        "filename": document["filename"] if file is not None else normalize_document_filename(filename or ""),
    }


@app.post("/api/topics/analyze")
async def analyze_topics(file: UploadFile = File(...)) -> dict[str, Any]:
    pages = extract_pages_from_upload(file)
    return analyze_important_topics(pages)
