"""
The professor office-hours route reads data.xlsx once, not on every request.

Run from backend/ with:
    python -m pytest tests/test_professors.py
"""
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

import main
from routers import professors

SHEET = pd.DataFrame(
    [["Dr. Ada Lovelace", "Mon/Wed", "2-3pm", "MacQuarrie Hall 215"]],
    columns=["Name", "Day(s)", "Time (s)", "Where"],
)


def test_spreadsheet_is_read_once_across_requests():
    professors._load_data.cache_clear()
    client = TestClient(main.app)
    try:
        with patch.object(professors.pd, "read_excel", return_value=SHEET) as read_excel:
            first = client.post("/api/professors", json={"message": "lovelace"})
            second = client.post("/api/professors", json={"message": "lovelace hours"})
        assert read_excel.call_count == 1
        assert first.status_code == second.status_code == 200
        assert "Lovelace" in first.json()["response"]
    finally:
        professors._load_data.cache_clear()


def test_no_match_still_answers():
    professors._load_data.cache_clear()
    client = TestClient(main.app)
    try:
        with patch.object(professors.pd, "read_excel", return_value=SHEET):
            res = client.post("/api/professors", json={"message": "nobody"})
        assert res.json() == {"response": "No matching professor found.", "results": []}
    finally:
        professors._load_data.cache_clear()
