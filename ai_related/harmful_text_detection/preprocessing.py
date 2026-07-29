import re

class TextPreprocessor:
    """Text cleaning and normalization for toxicity detection."""

    @staticmethod
    def clean_text(text: str) -> str:
        """
        Clean and normalize text.

        Args:
            text: Raw text input

        Returns:
            Cleaned text.
        """
        if not isinstance(text, str):
            return ""

        text = text.strip()

        text = re.sub(r'https?://\S+', '', text)
        text = re.sub(r'www\.\S+', '', text)

        text = re.sub(r'<[^>]+>', '', text)

        text = re.sub(r'[@#](\w+)', r'\1', text)

        text = re.sub(r'[^\w\s.,!?-]', '', text)

        text = re.sub(r'\s+', ' ', text).strip()

        return text

