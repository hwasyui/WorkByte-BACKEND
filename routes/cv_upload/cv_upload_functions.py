import os
import io
import json
import re
from typing import Dict, List, Optional

from PyPDF2 import PdfReader
from functions.logger import logger


def _extract_text_from_pdf(file_bytes: bytes) -> str:
    try:
        logger("CV_UPLOAD", "Starting PDF text extraction", level="DEBUG")
        reader = PdfReader(io.BytesIO(file_bytes))
        pages = [page.extract_text() or "" for page in reader.pages]
        text = "\n".join(pages).strip()
        logger("CV_UPLOAD", f"PDF text extraction successful: {len(text)} characters", level="DEBUG")
        return text
    except Exception as e:
        logger("CV_UPLOAD", f"PDF text extraction failed: {str(e)}", level="ERROR")
        raise RuntimeError(f"Failed to extract text from PDF: {str(e)}")


def _extract_text_from_image(file_bytes: bytes) -> str:
    logger("CV_UPLOAD", "Starting image OCR extraction", level="DEBUG")

    # Try Tesseract first
    try:
        logger("CV_UPLOAD", "Attempting Tesseract OCR", level="DEBUG")
        from PIL import Image
        import pytesseract
        image = Image.open(io.BytesIO(file_bytes))
        if image.mode != "RGB":
            image = image.convert("RGB")
        text = pytesseract.image_to_string(image).strip()
        if text:
            logger("CV_UPLOAD", f"Tesseract OCR successful: {len(text)} characters", level="DEBUG")
            return text
        else:
            logger("CV_UPLOAD", "Tesseract OCR returned empty text", level="DEBUG")
    except Exception as e:
        logger("CV_UPLOAD", f"Tesseract OCR failed: {str(e)}", level="DEBUG")

    # Try Google Cloud Vision
    try:
        logger("CV_UPLOAD", "Attempting Google Cloud Vision OCR", level="DEBUG")
        from google.cloud import vision
        client = vision.ImageAnnotatorClient()
        image = vision.Image(content=file_bytes)
        response = client.document_text_detection(image=image)
        if response.error.message:
            logger("CV_UPLOAD", f"Google Vision OCR error: {response.error.message}", level="DEBUG")
            raise RuntimeError(f"Google Vision OCR failed: {response.error.message}")
        text = response.full_text_annotation.text.strip()
        if text:
            logger("CV_UPLOAD", f"Google Vision OCR successful: {len(text)} characters", level="DEBUG")
            return text
        else:
            logger("CV_UPLOAD", "Google Vision OCR returned empty text", level="DEBUG")
    except Exception as e:
        logger("CV_UPLOAD", f"Google Cloud Vision OCR failed: {str(e)}", level="DEBUG")

    # Try Gemini via Vertex AI
    try:
        logger("CV_UPLOAD", "Attempting Gemini Vertex AI OCR", level="DEBUG")
        from google import genai
        import os
        project_id = os.getenv("GOOGLE_PROJECT_ID")
        location = os.getenv("GOOGLE_LOCATION", "us-central1")
        if not project_id:
            logger("CV_UPLOAD", "GOOGLE_PROJECT_ID not set for Vertex AI", level="DEBUG")
            raise RuntimeError("GOOGLE_PROJECT_ID not set")
        client = genai.Client(vertexai=True, project=project_id, location=location)
        # Convert bytes to PIL Image for Gemini
        from PIL import Image
        image = Image.open(io.BytesIO(file_bytes))
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                "Extract all visible text from this image. Return only the extracted text, no explanations or additional content.",
                image
            ],
            config={"temperature": 0.0, "max_output_tokens": 2048},
        )
        text = response.text.strip()
        if text:
            logger("CV_UPLOAD", f"Gemini Vertex AI OCR successful: {len(text)} characters", level="DEBUG")
            return text
        else:
            logger("CV_UPLOAD", "Gemini Vertex AI OCR returned empty text", level="DEBUG")
    except Exception as e:
        logger("CV_UPLOAD", f"Gemini Vertex AI OCR failed: {str(e)}", level="DEBUG")

    # Try Direct Gemini API as final fallback
    try:
        logger("CV_UPLOAD", "Attempting Direct Gemini API OCR", level="DEBUG")
        from google import genai
        import os
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger("CV_UPLOAD", "GEMINI_API_KEY not set for direct API", level="DEBUG")
            raise RuntimeError("GEMINI_API_KEY not set")
        client = genai.Client(api_key=api_key)
        # Convert bytes to PIL Image for Gemini
        from PIL import Image
        image = Image.open(io.BytesIO(file_bytes))
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                "Extract all visible text from this image. Return only the extracted text, no explanations or additional content.",
                image
            ],
            config={"temperature": 0.0, "max_output_tokens": 2048},
        )
        text = response.text.strip()
        if text:
            logger("CV_UPLOAD", f"Direct Gemini API OCR successful: {len(text)} characters", level="DEBUG")
            return text
        else:
            logger("CV_UPLOAD", "Direct Gemini API OCR returned empty text", level="DEBUG")
    except Exception as e:
        logger("CV_UPLOAD", f"Direct Gemini API OCR failed: {str(e)}", level="DEBUG")

    logger("CV_UPLOAD", "All OCR methods failed", level="ERROR")
    raise RuntimeError("All OCR methods failed: Tesseract, Google Cloud Vision, and Gemini API.")


def _find_section_tokens(text: str) -> List[str]:
    return [
        "summary",
        "professional summary",
        "profile",
        "about me",
        "skills",
        "technical skills",
        "expertise",
        "languages",
        "work experience",
        "experience",
        "employment history",
        "education",
        "academic background",
        "certifications",
        "specialities",
        "specialties",
        "areas of expertise",
    ]


def _split_text_into_sections(text: str) -> Dict[str, List[str]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    sections: Dict[str, List[str]] = {"top": []}
    current = "top"
    tokens = _find_section_tokens(text)

    for line in lines:
        lower = line.lower().strip()
        matched = False
        for token in tokens:
            if lower == token or lower.startswith(token + ":") or lower.startswith(token + " -"):
                current = token
                sections[current] = []
                matched = True
                break
        if matched:
            continue
        sections.setdefault(current, []).append(line)

    return sections


def _split_items(lines: List[str]) -> List[str]:
    items: List[str] = []
    for line in lines:
        cleaned_line = re.sub(r"\([^\)]*\)", "", line).strip()
        if any(sep in cleaned_line for sep in [",", "•", "-", ";", "/"]):
            parts = re.split(r"[,;•/\\-]", cleaned_line)
            items.extend([part.strip() for part in parts if part.strip()])
        else:
            items.append(cleaned_line)
    return items


def _extract_email(text: str) -> Optional[str]:
    match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    return match.group(0) if match else None


def _extract_phone(text: str) -> Optional[str]:
    match = re.search(r"(\+?\d[\d\s\-\.\(\)]{6,}\d)", text)
    if not match:
        return None
    return re.sub(r"[\s\-\.\(\)]", "", match.group(0))


def _extract_name(text: str) -> Optional[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines[:6]:
        if _extract_email(line) or _extract_phone(line):
            continue
        if len(line.split()) <= 5 and re.match(r"^[A-Za-z .,'-]+$", line):
            return line
    return None


def _parse_resume_text(text: str) -> Dict:
    logger("CV_UPLOAD", f"Starting fallback resume parsing: {len(text)} characters", level="DEBUG")
    sections = _split_text_into_sections(text)
    parsed: Dict = {
        "full_name": _extract_name(text),
        "email": _extract_email(text),
        "phone": _extract_phone(text),
        "bio": None,
        "skills": [],
        "specialities": [],
        "languages": [],
        "work_experience": [],
        "education": [],
        "certifications": [],
    }

    if sections.get("summary"):
        parsed["bio"] = " ".join(sections["summary"]).strip()
    elif sections.get("profile"):
        parsed["bio"] = " ".join(sections["profile"]).strip()
    elif sections.get("top"):
        parsed["bio"] = " ".join(sections["top"][:3]).strip()

    section_mapping = [
        ("skills", "skills"),
        ("technical skills", "skills"),
        ("expertise", "specialities"),
        ("specialities", "specialities"),
        ("specialties", "specialities"),
        ("areas of expertise", "specialities"),
        ("languages", "languages"),
        ("work experience", "work_experience"),
        ("experience", "work_experience"),
        ("employment history", "work_experience"),
        ("education", "education"),
        ("academic background", "education"),
        ("certifications", "certifications"),
    ]

    for section_name, target_field in section_mapping:
        if section_name in sections:
            if target_field in {"skills", "languages", "specialities"}:
                parsed[target_field] = _split_items(sections[section_name])
            else:
                parsed[target_field] = sections[section_name]

    parsed["skills"] = list(dict.fromkeys(parsed["skills"]))
    parsed["languages"] = list(dict.fromkeys(parsed["languages"]))
    parsed["specialities"] = list(dict.fromkeys(parsed["specialities"]))

    logger("CV_UPLOAD", f"Fallback parsing complete: name={parsed['full_name']}, skills={len(parsed['skills'])}, email={parsed['email']}", level="DEBUG")
    return parsed


async def _parse_resume_text_with_llm(text: str) -> Dict:
    logger("CV_UPLOAD", f"Starting LLM resume parsing: {len(text)} characters", level="DEBUG")
    try:
        from google import genai
    except ImportError:
        logger("CV_UPLOAD", "google-genai package not installed", level="ERROR")
        raise RuntimeError("LLM resume parsing requires the google-genai package.")

    project_id = os.getenv("GOOGLE_PROJECT_ID")
    location = os.getenv("GOOGLE_LOCATION", "us-central1")
    model = os.getenv("GOOGLE_LLM", "gemini-2.5-flash")

    logger("CV_UPLOAD", f"Using Vertex AI: project={project_id}, location={location}, model={model}", level="DEBUG")

    prompt = (
        "Parse the following resume text into a JSON object with the keys: full_name, email, phone, bio, "
        "skills, specialities, languages, work_experience, education, certifications. "
        "Return valid JSON only. If a value is missing, use null for strings and [] for lists. "
        f"Resume text:\n\n{text}"
    )

    try:
        client = genai.Client(vertexai=True, project=project_id, location=location)
        logger("CV_UPLOAD", "Sending request to Vertex AI Gemini", level="DEBUG")
        response = await client.aio.models.generate_content(
            model=model,
            contents=prompt,
            config={"temperature": 0.0, "max_output_tokens": 1024},
        )
        raw = response.text.strip()
        logger("CV_UPLOAD", f"Vertex AI response received: {len(raw)} characters", level="DEBUG")
        logger("CV_UPLOAD", f"Raw response: {raw[:200]}...", level="DEBUG")
        parsed = json.loads(raw)
        logger("CV_UPLOAD", f"LLM parsing successful: name={parsed.get('full_name')}, skills={len(parsed.get('skills', []))}", level="DEBUG")
        return parsed
    except json.JSONDecodeError as e:
        logger("CV_UPLOAD", f"LLM returned invalid JSON: {str(e)} | raw={raw[:300]}", level="ERROR")
        raise RuntimeError(f"LLM returned invalid JSON: {str(e)} | raw={raw[:300]}")
    except Exception as e:
        logger("CV_UPLOAD", f"LLM parsing failed: {str(e)}", level="ERROR")
        raise RuntimeError(f"LLM resume parsing failed: {str(e)}")