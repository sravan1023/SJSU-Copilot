from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd

app = FastAPI()

# allow frontend (React) to talk to backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def home():
    return {"message": "Backend is running"}


@app.post("/chat")
def chat(query: dict):
    question = query.get("message", "").lower()

    # FIX: header=1 (because your Excel has extra top row)
    df = pd.read_excel("data.xlsx", header=1)

    results = []

    for _, row in df.iterrows():
        try:
            name = str(row.get("Name", ""))
            days = str(row.get("Day(s)", ""))
            time = str(row.get("Time (s)", ""))

            # some columns are messy → safe access
            walk = str(row.iloc[3]) if len(row) > 3 else ""

            text = f"{name} is available on {days} at {time}. {walk}"

            # simple keyword match
            if any(word in text.lower() for word in question.split()):
                results.append(text)

        except Exception as e:
            continue  # skip bad rows

    if not results:
        return {"response": "No matching professor found."}

    return {"response": "\n".join(results[:5])}