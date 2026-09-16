from __future__ import annotations

import csv
import io
import re
import sqlite3
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pypdf import PdfReader

from .db import get_connection, init_db

app = FastAPI(title="Quiz Question Bank Generator API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def normalize_text(raw_text: str) -> str:
    text = raw_text.replace("\r", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_text_from_upload(file: UploadFile) -> str:
    filename = (file.filename or "").lower()
    content = file.file.read()

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


def generate_questions(text: str) -> list[dict[str, str]]:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 40]

    if not sentences:
        raise HTTPException(status_code=400, detail="No usable text found in the uploaded document.")

    selected = sentences[:5]
    questions = []
    difficulty_levels = ["Easy", "Medium", "Hard"]

    for index, sentence in enumerate(selected, start=1):
        topic = sentence.split()[0:4]
        questions.append(
            {
                "id": index,
                "question": f"What is the main idea described in the following context: '{sentence[:120]}...' ?",
                "answer": sentence[:200],
                "difficulty": difficulty_levels[index % len(difficulty_levels)],
                "topic": " ".join(topic),
            }
        )

    return questions


def save_questions(questions: list[dict[str, str]]) -> None:
    conn: sqlite3.Connection = get_connection()
    try:
        conn.executemany(
            "INSERT INTO questions (text, answer, difficulty, topic) VALUES (?, ?, ?, ?)",
            [
                (question["question"], question["answer"], question["difficulty"], question["topic"])
                for question in questions
            ],
        )
        conn.commit()
    finally:
        conn.close()


def fetch_questions() -> list[dict[str, Any]]:
    conn: sqlite3.Connection = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, text, answer, difficulty, topic, created_at FROM questions ORDER BY created_at DESC, id DESC"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


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


@app.get("/api/questions/export")
def export_questions(format: str = "json") -> Response:
    questions = fetch_questions()

    if format == "json":
        return JSONResponse({"questions": questions, "count": len(questions)})

    if format == "csv":
        output = io.StringIO()
        fieldnames = ["id", "question", "answer", "difficulty", "topic", "created_at"]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for question in questions:
            writer.writerow({
                "id": question.get("id", ""),
                "question": question.get("text", ""),
                "answer": question.get("answer", ""),
                "difficulty": question.get("difficulty", ""),
                "topic": question.get("topic", ""),
                "created_at": question.get("created_at", ""),
            })
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=question_bank.csv"},
        )

    raise HTTPException(status_code=400, detail="Unsupported export format. Use json or csv.")


@app.post("/api/questions/generate")
async def generate_question_bank(file: UploadFile = File(...)) -> dict[str, Any]:
    text = extract_text_from_upload(file)
    questions = generate_questions(text)
    save_questions(questions)
    return {"questions": questions, "count": len(questions)}
